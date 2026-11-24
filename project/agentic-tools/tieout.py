"""
Tick-and-tie engine (pure module: no DB, no HTTP).

A tie-out compares two sources of figures key by key and records, for every item,
a tickmark from a fixed legend. It exists to satisfy the reperformance standard:
an independent auditor, given only the stored record, must be able to redo the
comparison and reach the same marks.

Everything an auditor needs is therefore in the record:
  * the legend (what each tickmark means), versioned and copied into every result;
  * the tolerance actually applied, in plain terms;
  * how each source was obtained (a signed snapshot id and its hash, or the rows
    themselves when they were typed or pasted in);
  * the objective, procedure and conclusion, which are required;
  * every item's left amount, right amount, difference and mark.

Matching is deterministic and deliberately simple: items match on an exact key.
If a source repeats a key, its amounts are summed and the count of lines recorded
(so a many-to-one tie is visible, never hidden). Amounts are Decimal; a value that
is not a number is an error, never a silent zero.

Tolerance. A difference is "within tolerance" when |difference| <= abs, or when
pct is set and |difference| <= pct% of |left amount|. With neither set the
tolerance is zero and only exact agreement ties.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

TIEOUT_VERSION = 1
MAX_ROWS = 5000
MAX_TEXT = 4000

# Tickmark legend. Fixed and versioned: changing a meaning means a new version,
# because stored results are interpreted against the legend copied into them.
LEGEND_VERSION = 1
LEGEND = {
    "T": "Tied: left and right amounts agree exactly.",
    "R": "Within tolerance: amounts differ, but by no more than the stated tolerance.",
    "D": "Difference: amounts differ by more than the stated tolerance. Exception to follow up.",
    "L": "Left only: the key appears in the left source and not in the right. Exception to follow up.",
    "X": "Right only: the key appears in the right source and not in the left. Exception to follow up.",
}
EXCEPTION_MARKS = ("D", "L", "X")
_ZERO = Decimal("0")


class TieoutError(ValueError):
    """Invalid tie-out request. Safe to show to the caller."""


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _dec(value: Any, what: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise TieoutError(f"{what} is not a number")
    try:
        d = Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation:
        raise TieoutError(f"{what} is not a number: {str(value)[:40]!r}")
    if not d.is_finite():
        raise TieoutError(f"{what} is not a finite number")
    return d


def _fmt(d: Decimal) -> str:
    """Fixed-point text, no exponent, so the stored value is unambiguous."""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _narrative(spec: dict, field: str) -> str:
    v = spec.get(field)
    if not isinstance(v, str) or not v.strip():
        raise TieoutError(f"{field} is required: say in plain words what the test is for and how it is performed")
    if len(v) > MAX_TEXT:
        raise TieoutError(f"{field} is limited to {MAX_TEXT} characters")
    return v.strip()


def validate_spec(spec: Any) -> dict:
    """Normalise a tie-out spec or raise TieoutError.

    {"left": {"label", "key", "amount"}, "right": {...},
     "tolerance": {"abs": "0.01", "pct": "0.5"},   # optional
     "objective", "procedure"}                      # required, plain language
    """
    if not isinstance(spec, dict):
        raise TieoutError("spec must be an object")
    out: dict = {}
    for side in ("left", "right"):
        s = spec.get(side)
        if not isinstance(s, dict):
            raise TieoutError(f"{side} must be an object with label, key and amount")
        norm = {}
        for f in ("label", "key", "amount"):
            v = s.get(f)
            if not isinstance(v, str) or not v.strip():
                raise TieoutError(f"{side}.{f} is required")
            norm[f] = v.strip()[:200]
        out[side] = norm
    tol = spec.get("tolerance") or {}
    if not isinstance(tol, dict) or set(tol) - {"abs", "pct"}:
        raise TieoutError("tolerance may only contain abs and pct")
    ntol = {"abs": "0", "pct": "0"}
    for k in ("abs", "pct"):
        if tol.get(k) not in (None, ""):
            d = _dec(tol[k], f"tolerance.{k}")
            if d < 0:
                raise TieoutError(f"tolerance.{k} cannot be negative")
            ntol[k] = _fmt(d)
    out["tolerance"] = ntol
    out["objective"] = _narrative(spec, "objective")
    out["procedure"] = _narrative(spec, "procedure")
    return out


def tolerance_text(tol: dict) -> str:
    a, p = _dec(tol["abs"], "abs"), _dec(tol["pct"], "pct")
    if a == 0 and p == 0:
        return "No tolerance: only exact agreement is tied."
    parts = []
    if a:
        parts.append(f"an absolute difference of up to {_fmt(a)}")
    if p:
        parts.append(f"a difference of up to {_fmt(p)}% of the left amount")
    return "A difference is within tolerance if it is " + " or ".join(parts) + "."


def _aggregate(rows: list, side: dict, role: str) -> dict:
    if len(rows) > MAX_ROWS:
        raise TieoutError(f"the {role} source has more than {MAX_ROWS} rows; narrow it first")
    agg: dict = {}
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or side["key"] not in r:
            raise TieoutError(f"{role} row {i + 1} has no '{side['key']}' column")
        if side["amount"] not in r:
            raise TieoutError(f"{role} row {i + 1} has no '{side['amount']}' column")
        k = r[side["key"]]
        if k is None or str(k).strip() == "":
            raise TieoutError(f"{role} row {i + 1} has an empty key")
        k = str(k).strip()
        amt = _dec(r[side["amount"]], f"{role} row {i + 1} amount")
        cur = agg.get(k)
        if cur:
            cur[0] += amt
            cur[1] += 1
        else:
            agg[k] = [amt, 1]
    return agg


def compute(spec: dict, left_rows: list, right_rows: list) -> dict:
    """Tie the two sources. `spec` must already be validated. Returns
    {"items": [...], "summary": {...}}; items are ordered by key so the output is
    identical on every run."""
    L = _aggregate(left_rows, spec["left"], "left")
    R = _aggregate(right_rows, spec["right"], "right")
    tol_abs, tol_pct = _dec(spec["tolerance"]["abs"], "abs"), _dec(spec["tolerance"]["pct"], "pct")

    items = []
    for key in sorted(set(L) | set(R)):
        l, r = L.get(key), R.get(key)
        item = {"key": key,
                "left": _fmt(l[0]) if l else None, "left_lines": l[1] if l else 0,
                "right": _fmt(r[0]) if r else None, "right_lines": r[1] if r else 0}
        if l and r:
            diff = l[0] - r[0]
            item["difference"] = _fmt(diff)
            if diff == 0:
                mark = "T"
            elif abs(diff) <= tol_abs or (tol_pct and abs(diff) <= abs(l[0]) * tol_pct / Decimal(100)):
                mark = "R"
            else:
                mark = "D"
        else:
            item["difference"] = None
            mark = "L" if l else "X"
        item["mark"] = mark
        items.append(item)

    counts = {m: 0 for m in LEGEND}
    for it in items:
        counts[it["mark"]] += 1
    lt = sum((v[0] for v in L.values()), _ZERO)
    rt = sum((v[0] for v in R.values()), _ZERO)
    exceptions = sum(counts[m] for m in EXCEPTION_MARKS)
    summary = {
        "left_total": _fmt(lt), "right_total": _fmt(rt), "net_difference": _fmt(lt - rt),
        "left_rows": len(left_rows), "right_rows": len(right_rows), "items": len(items),
        "counts": counts, "exceptions": exceptions,
        "outcome": "tied" if exceptions == 0 else "exceptions",
    }
    return {"items": items, "summary": summary}


def describe_source(role: str, kind: str, label: str, rows_json: str, **extra) -> dict:
    """How a source was obtained, with the hash of the exact rows used."""
    return {"role": role, "type": kind, "label": label, "row_count": len(json.loads(rows_json)),
            "rows_sha256": hashlib.sha256(rows_json.encode("utf-8")).hexdigest(), **extra}


def build_record(spec: dict, sources: list, result: dict, conclusion: str, created_by: str,
                 as_of: str, engagement: Optional[str], note: Optional[str],
                 inline_rows: dict, sign: Callable[[str], str]) -> dict:
    """Assemble the storable, signed record. `inline_rows` maps role -> the rows
    themselves for sources that are not a signed snapshot (so the record alone is
    enough to redo the tie)."""
    if sign is None:
        raise TieoutError("a signing function is required")
    conclusion = (conclusion or "").strip()
    if not conclusion:
        raise TieoutError("conclusion is required: state what the result means for the audit")
    if len(conclusion) > MAX_TEXT:
        raise TieoutError(f"conclusion is limited to {MAX_TEXT} characters")
    engagement = (engagement or "").strip()[:128] or None
    note = (note or "").strip()[:2000] or None
    items_json = _canon(result["items"])
    items_sha = hashlib.sha256(items_json.encode("utf-8")).hexdigest()
    payload = {
        "tieout_version": TIEOUT_VERSION,
        "legend_version": LEGEND_VERSION,
        "legend": LEGEND,
        "spec": spec,
        "tolerance_statement": tolerance_text(spec["tolerance"]),
        "sources": sources,
        "inline_rows": inline_rows,
        "summary": result["summary"],
        "items_sha256": items_sha,
        "conclusion": conclusion,
        "as_of": as_of,
        "created_by": created_by,
        "engagement": engagement,
        "note": note,
    }
    payload_json = _canon(payload)
    return {
        "engagement": engagement, "note": note, "payload_json": payload_json,
        "items_json": items_json, "result_sha256": items_sha,
        "outcome": result["summary"]["outcome"], "signature": sign(payload_json),
        "created_by": created_by,
    }


def verify(record: dict, sign: Callable[[str], str]) -> dict:
    """Check a stored tie-out: signature, item hash, and that re-computing the
    marks from the recorded inputs still gives the stored items (so a mark cannot
    have been edited without the arithmetic noticing). Never raises on tampering."""
    payload_json = record["payload_json"]
    items_hash = hashlib.sha256(record["items_json"].encode("utf-8")).hexdigest()
    try:
        payload = json.loads(payload_json)
        declared = payload["items_sha256"]
    except (ValueError, KeyError, TypeError):
        return {"signature_valid": False, "items_match_hash": False, "recomputed_match": None,
                "intact": False, "detail": "payload is unreadable"}
    items_ok = hmac.compare_digest(items_hash, declared) and hmac.compare_digest(items_hash, record["result_sha256"])
    sig_ok = hmac.compare_digest(sign(payload_json), record["signature"])
    # Recompute from inline rows when both sources are inline (snapshot sources need
    # their stored rows, which the caller supplies via recompute_with()).
    recomputed = None
    inline = payload.get("inline_rows") or {}
    if set(inline) == {"left", "right"}:
        try:
            again = compute(payload["spec"], inline["left"], inline["right"])
            recomputed = _canon(again["items"]) == record["items_json"]
        except TieoutError:
            recomputed = False
    detail = "ok"
    if not sig_ok and not items_ok:
        detail = "payload and items have both been altered, or the signing key has changed"
    elif not sig_ok:
        detail = "payload was altered after signing, or the signing key has changed"
    elif not items_ok:
        detail = "items were altered after signing"
    elif recomputed is False:
        detail = "the marks do not follow from the recorded inputs"
    intact = sig_ok and items_ok and recomputed is not False
    return {"signature_valid": sig_ok, "items_match_hash": items_ok, "recomputed_match": recomputed,
            "intact": intact, "detail": detail}


def recompute_with(record: dict, left_rows: list, right_rows: list) -> dict:
    """Redo the tie from rows the caller re-fetched (e.g. from the two signed
    snapshots) and say whether it reproduces the stored items."""
    payload = json.loads(record["payload_json"])
    again = compute(payload["spec"], left_rows, right_rows)
    same = _canon(again["items"]) == record["items_json"]
    return {"reproduced": same,
            "explanation": "the marks follow from the sources" if same
            else "the sources do not produce the stored marks"}
