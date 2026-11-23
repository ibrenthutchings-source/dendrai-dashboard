"""
Report-as-Code (pure module: no DB, no HTTP).

A *report definition* is a versioned JSON document: parameters plus an ordered
list of blocks (text, KPI, table, chart). Every data block carries a governed
query spec (reporting_catalog) so a report can only ever read through the
dataset registry. Definitions are validated here, hashed canonically, run
against parameters, signed as a *run record*, and rendered to HTML.

    {
      "title": "Open control gaps",
      "objective": "...", "procedure": "...", "conclusion": "...",   # reperformance narrative
      "parameters": [{"name": "fw", "type": "string", "label": "Framework", "default": "SOX"}],
      "blocks": [
        {"id": "intro", "type": "text", "content": "..."},
        {"id": "total", "type": "kpi", "title": "Controls", "value_column": "n",
         "query": {"dataset": "controls", "aggregates": [{"fn": "count", "column": "*", "as": "n"}],
                   "filters": [{"column": "framework", "op": "eq", "value": {"$param": "fw"}}]}},
        {"id": "by_cat", "type": "chart", "title": "By category",
         "chart": {"kind": "bar", "x": "category", "y": ["n"]}, "query": {...}},
        {"id": "list", "type": "table", "title": "Controls", "query": {...}}
      ]
    }

Reperformance: a run record stores the definition (not a reference to it), its
hash, the resolved parameters, every block's manifest (spec, SQL, params, as-of,
hash) and every block's rows, all signed. Procedure narrative (objective,
procedure, conclusion) is part of the definition so it is versioned and signed
with the work. A definition is only editable by creating a new version.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import html
import json
import re
from datetime import date, datetime
from typing import Any, Callable, Optional

import reporting_catalog as rc

DEFINITION_VERSION = 1
RUN_VERSION = 1
MAX_BLOCKS = 30
MAX_TEXT = 20_000
MAX_CHART_POINTS = 60
BLOCK_TYPES = ("text", "kpi", "table", "chart")
CHART_KINDS = ("bar", "line")
PARAM_TYPES = ("string", "number", "date")
NARRATIVE_FIELDS = ("objective", "procedure", "conclusion")

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class DefinitionError(ValueError):
    """Invalid definition, parameters or run. Safe to show to the caller."""


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def definition_hash(defn: dict) -> str:
    return hashlib.sha256(canonical(defn).encode("utf-8")).hexdigest()


def check_key(key: str) -> str:
    key = (key or "").strip().lower()
    if key == "runs":
        raise DefinitionError("'runs' is a reserved name")
    if not _KEY_RE.match(key):
        raise DefinitionError("report key must be 1-64 characters: lowercase letters, digits, _ or -, starting with a letter")
    return key


# ── parameters ──────────────────────────────────────────────────────────────

def _coerce_param(p: dict, value: Any) -> Any:
    t = p["type"]
    try:
        if t == "string":
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError
            return str(value)
        if t == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError
            f = float(value)
            if f != f or f in (float("inf"), float("-inf")):
                raise ValueError
            return int(f) if f == int(f) and not isinstance(value, float) else f
        if t == "date":
            return date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, TypeError):
        pass
    raise DefinitionError(f"parameter {p['name']!r} must be a valid {t}")


def resolve_params(defn: dict, supplied: Optional[dict]) -> dict:
    """Defaults, then supplied values; required parameters must end up set; unknown names are refused."""
    supplied = supplied or {}
    declared = {p["name"]: p for p in defn.get("parameters", [])}
    unknown = sorted(set(supplied) - set(declared))
    if unknown:
        raise DefinitionError(f"unknown parameter(s): {', '.join(unknown)}")
    out = {}
    for name, p in declared.items():
        if name in supplied and supplied[name] not in (None, ""):
            out[name] = _coerce_param(p, supplied[name])
        elif p.get("default") not in (None, ""):
            out[name] = _coerce_param(p, p["default"])
        elif p.get("required", True):
            raise DefinitionError(f"parameter {name!r} is required")
        else:
            out[name] = None
    return out


def _substitute(node: Any, params: dict) -> Any:
    if isinstance(node, dict):
        if set(node) == {"$param"}:
            name = node["$param"]
            if name not in params:
                raise DefinitionError(f"query refers to undeclared parameter {name!r}")
            return params[name]
        return {k: _substitute(v, params) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, params) for v in node]
    return node


def _param_refs(node: Any, acc: set) -> set:
    if isinstance(node, dict):
        if set(node) == {"$param"}:
            acc.add(node["$param"])
        else:
            for v in node.values():
                _param_refs(v, acc)
    elif isinstance(node, list):
        for v in node:
            _param_refs(v, acc)
    return acc


_SAMPLE = {"string": "x", "number": 0, "date": "2000-01-01"}


def compile_block(block: dict, params: dict, is_admin: bool) -> rc.CompiledQuery:
    try:
        return rc.compile_query(_substitute(block["query"], params), is_admin=is_admin)
    except rc.QueryError as exc:
        raise DefinitionError(f"block {block['id']!r}: {exc}")


# ── validation ──────────────────────────────────────────────────────────────

def validate_definition(defn: Any, is_admin: bool = False) -> dict:
    """Return the normalised definition, or raise DefinitionError saying what is wrong."""
    if not isinstance(defn, dict):
        raise DefinitionError("definition must be an object")
    title = str(defn.get("title", "")).strip()
    if not title or len(title) > 200:
        raise DefinitionError("title is required (200 characters at most)")

    params_in = defn.get("parameters") or []
    if not isinstance(params_in, list) or len(params_in) > 20:
        raise DefinitionError("parameters must be a list of at most 20")
    params = []
    for p in params_in:
        if not isinstance(p, dict):
            raise DefinitionError("each parameter must be an object")
        name = str(p.get("name", ""))
        if not _ID_RE.match(name):
            raise DefinitionError(f"invalid parameter name {name!r}")
        if name in {q["name"] for q in params}:
            raise DefinitionError(f"duplicate parameter {name!r}")
        ptype = p.get("type", "string")
        if ptype not in PARAM_TYPES:
            raise DefinitionError(f"parameter {name!r}: type must be one of {list(PARAM_TYPES)}")
        norm = {"name": name, "type": ptype, "label": str(p.get("label") or name)[:100],
                "required": bool(p.get("required", True)), "default": p.get("default")}
        if norm["default"] not in (None, ""):
            norm["default"] = _coerce_param(norm, norm["default"])
        params.append(norm)

    blocks_in = defn.get("blocks")
    if not isinstance(blocks_in, list) or not blocks_in:
        raise DefinitionError("a report needs at least one block")
    if len(blocks_in) > MAX_BLOCKS:
        raise DefinitionError(f"at most {MAX_BLOCKS} blocks")

    sample = {p["name"]: (p["default"] if p["default"] not in (None, "") else _SAMPLE[p["type"]]) for p in params}
    declared = {p["name"] for p in params}
    blocks, seen, used = [], set(), set()
    for b in blocks_in:
        if not isinstance(b, dict):
            raise DefinitionError("each block must be an object")
        bid, btype = str(b.get("id", "")), b.get("type")
        if not _ID_RE.match(bid):
            raise DefinitionError(f"invalid block id {bid!r}")
        if bid in seen:
            raise DefinitionError(f"duplicate block id {bid!r}")
        seen.add(bid)
        if btype not in BLOCK_TYPES:
            raise DefinitionError(f"block {bid!r}: type must be one of {list(BLOCK_TYPES)}")
        if btype == "text":
            content = str(b.get("content", ""))
            if not content.strip() or len(content) > MAX_TEXT:
                raise DefinitionError(f"block {bid!r}: text content is required (max {MAX_TEXT} characters)")
            blocks.append({"id": bid, "type": "text", "content": content})
            continue

        query = b.get("query")
        if not isinstance(query, dict):
            raise DefinitionError(f"block {bid!r}: a query is required")
        used |= _param_refs(query, set())
        norm = {"id": bid, "type": btype, "title": str(b.get("title", ""))[:200], "query": query}
        cq = compile_block(norm, sample, is_admin)
        names = {c["name"]: c["type"] for c in cq.output_columns}
        if btype == "kpi":
            col = b.get("value_column") or (cq.output_columns[0]["name"] if cq.output_columns else None)
            if col not in names:
                raise DefinitionError(f"block {bid!r}: value_column {col!r} is not an output column")
            norm["value_column"] = col
            norm["format"] = str(b.get("format", ""))[:20]
        elif btype == "chart":
            ch = b.get("chart")
            if not isinstance(ch, dict) or ch.get("kind") not in CHART_KINDS:
                raise DefinitionError(f"block {bid!r}: chart.kind must be one of {list(CHART_KINDS)}")
            ys = ch.get("y")
            if not isinstance(ys, list) or not ys or len(ys) > 5:
                raise DefinitionError(f"block {bid!r}: chart.y must list 1 to 5 columns")
            if ch.get("x") not in names:
                raise DefinitionError(f"block {bid!r}: chart.x {ch.get('x')!r} is not an output column")
            for y in ys:
                if names.get(y) != "number":
                    raise DefinitionError(f"block {bid!r}: chart.y column {y!r} must be a number column")
            norm["chart"] = {"kind": ch["kind"], "x": ch["x"], "y": list(ys)}
        blocks.append(norm)

    undeclared = used - declared
    if undeclared:
        raise DefinitionError(f"query refers to undeclared parameter(s): {', '.join(sorted(undeclared))}")

    out = {"definition_version": DEFINITION_VERSION, "title": title,
           "description": str(defn.get("description", ""))[:1000],
           "parameters": params, "blocks": blocks}
    for f in NARRATIVE_FIELDS:
        out[f] = str(defn.get(f, "")).strip()[:MAX_TEXT]
    return out


def is_restricted(defn: dict) -> bool:
    """True if any block reads an admin-only dataset. Such a report, its versions and
    its runs are visible and runnable by admins only."""
    for b in defn.get("blocks", []):
        if b["type"] != "text" and rc.get_dataset(b["query"]["dataset"]).admin_only:
            return True
    return False


def missing_narrative(defn: dict) -> list:
    """Which of objective/procedure/conclusion are empty. Required before approval."""
    return [f for f in NARRATIVE_FIELDS if not defn.get(f)]


# ── running ─────────────────────────────────────────────────────────────────

def run_blocks(defn: dict, params: dict, is_admin: bool, execute: Callable, as_of: datetime) -> dict:
    """Execute every data block. Returns {"manifests": {id: manifest}, "results": {id: {columns, rows}}}.
    Any block failing fails the whole run: a report is never partly produced."""
    manifests, results = {}, {}
    for b in defn["blocks"]:
        if b["type"] == "text":
            continue
        cq = compile_block(b, params, is_admin)
        rows = execute(cq)
        manifests[b["id"]] = rc.build_manifest(cq, rows, as_of=as_of, truncated=len(rows) >= cq.spec["limit"])
        results[b["id"]] = {"columns": cq.output_columns, "rows": rows}
        if b["type"] == "kpi" and len(rows) != 1:
            raise DefinitionError(f"block {b['id']!r}: a KPI query must return exactly one row (got {len(rows)})")
    return {"manifests": manifests, "results": results}


def build_run(key: str, version: int, defn: dict, params: dict, ran: dict, as_of: datetime, created_by: str,
              note: Optional[str], engagement: Optional[str], sign: Callable[[str], str]) -> dict:
    # Round-trip each block's rows through the catalog's canonical form so Decimals and
    # datetimes are plain JSON values, and so verify_run's per-block hashes match the manifests.
    rows_json = canonical({bid: json.loads(rc.canonical_rows_json(r["rows"])) for bid, r in ran["results"].items()})
    rows_sha = hashlib.sha256(rows_json.encode("utf-8")).hexdigest()
    note = (note or "").strip()[:2000] or None
    engagement = (engagement or "").strip()[:128] or None
    payload = {
        "run_version": RUN_VERSION,
        "definition": {"key": key, "version": version, "sha256": definition_hash(defn), "definition": defn},
        "parameters": params,
        "as_of": as_of.isoformat(),
        "manifests": ran["manifests"],
        "columns": {bid: r["columns"] for bid, r in ran["results"].items()},
        "result_sha256": rows_sha,
        "created_by": created_by, "note": note, "engagement": engagement,
    }
    payload_json = canonical(payload)
    return {"definition_key": key, "definition_version": version, "engagement": engagement, "note": note,
            "payload_json": payload_json, "rows_json": rows_json, "result_sha256": rows_sha,
            "signature": sign(payload_json), "created_by": created_by}


def verify_run(run: dict, sign: Callable[[str], str]) -> dict:
    """Signature, overall row hash, each block's row hash, and the embedded definition's hash."""
    try:
        payload = json.loads(run["payload_json"])
        rows = json.loads(run["rows_json"])
        declared = payload["result_sha256"]
        defn = payload["definition"]
        manifests = payload["manifests"]
    except (ValueError, KeyError, TypeError):
        return {"intact": False, "signature_valid": False, "rows_match_hash": False,
                "blocks_match": False, "definition_matches": False, "detail": "record is unreadable"}
    actual = hashlib.sha256(run["rows_json"].encode("utf-8")).hexdigest()
    rows_ok = hmac.compare_digest(actual, declared) and hmac.compare_digest(actual, run["result_sha256"])
    sig_ok = hmac.compare_digest(sign(run["payload_json"]), run["signature"])
    blocks_ok = True
    for bid, m in manifests.items():
        if bid not in rows:
            blocks_ok = False
            break
        h = hashlib.sha256(rc.canonical_rows_json(rows[bid]).encode("utf-8")).hexdigest()
        if h != m.get("result_sha256"):
            blocks_ok = False
            break
    def_ok = definition_hash(defn["definition"]) == defn["sha256"]
    problems = [name for name, ok in (("signature", sig_ok), ("result rows", rows_ok),
                                      ("block rows", blocks_ok), ("embedded definition", def_ok)) if not ok]
    return {"intact": not problems, "signature_valid": sig_ok, "rows_match_hash": rows_ok,
            "blocks_match": blocks_ok, "definition_matches": def_ok,
            "detail": "ok" if not problems else "failed: " + ", ".join(problems)}


def compare_rerun(payload: dict, fresh_manifests: dict) -> dict:
    """Per-block: does a fresh run reproduce the stored hashes? Distinguishes a changed
    dataset definition (different SQL) from moved source data (same SQL, different rows)."""
    blocks = {}
    for bid, old in payload["manifests"].items():
        new = fresh_manifests.get(bid)
        if new is None:
            blocks[bid] = {"reproduced": False, "reason": "block missing from fresh run"}
        elif new["sql"] != old["sql"]:
            blocks[bid] = {"reproduced": False, "reason": "dataset definition changed (different SQL)"}
        elif new["result_sha256"] != old["result_sha256"]:
            blocks[bid] = {"reproduced": False, "reason": "source data changed since as-of time"}
        else:
            blocks[bid] = {"reproduced": True, "reason": "identical"}
    return {"reproduced": all(b["reproduced"] for b in blocks.values()), "as_of": payload["as_of"], "blocks": blocks}


# ── rendering (HTML) ────────────────────────────────────────────────────────

def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}"
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def _e(v: Any) -> str:
    return html.escape(_fmt(v), quote=True)


def _svg_chart(kind: str, x: str, ys: list, rows: list) -> str:
    rows = rows[:MAX_CHART_POINTS]
    if not rows:
        return "<p><em>No data.</em></p>"
    w, h, pad = 640, 260, 40
    vals = [float(r.get(y) or 0) for r in rows for y in ys]
    top = max(max(vals), 0) or 1
    bottom = min(min(vals), 0)
    span = (top - bottom) or 1
    palette = ["#2563eb", "#d97706", "#059669", "#7c3aed", "#dc2626"]
    n = len(rows)
    def yy(v: float) -> float:
        return pad + (h - 2 * pad) * (1 - (v - bottom) / span)
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" width="100%" style="max-width:{w}px">',
             f'<line x1="{pad}" y1="{yy(0):.1f}" x2="{w - pad}" y2="{yy(0):.1f}" stroke="#888"/>']
    step = (w - 2 * pad) / n
    for i, r in enumerate(rows):
        cx = pad + step * (i + 0.5)
        label = _e(r.get(x))[:14]
        parts.append(f'<text x="{cx:.1f}" y="{h - 10}" font-size="9" text-anchor="middle">{label}</text>')
    for si, y in enumerate(ys):
        color = palette[si % len(palette)]
        if kind == "bar":
            bw = step * 0.8 / len(ys)
            for i, r in enumerate(rows):
                v = float(r.get(y) or 0)
                x0 = pad + step * i + step * 0.1 + bw * si
                y0, y1 = sorted((yy(v), yy(0)))
                parts.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{max(y1 - y0, 0.5):.1f}" '
                             f'fill="{color}"><title>{_e(r.get(x))}: {_e(v)}</title></rect>')
        else:
            pts = " ".join(f"{pad + step * (i + 0.5):.1f},{yy(float(r.get(y) or 0)):.1f}" for i, r in enumerate(rows))
            parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>')
    legend = " ".join(f'<span style="color:{palette[i % len(palette)]}">&#9632; {_e(y)}</span>' for i, y in enumerate(ys))
    parts.append("</svg>")
    return "".join(parts) + f'<div style="font-size:12px">{legend}</div>'


def render_html(defn: dict, params: dict, results: dict, meta: dict) -> str:
    """Self-contained HTML. Everything user-controlled is escaped; charts are inline SVG."""
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>{_e(defn['title'])}</title>"
           "<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}"
           "table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #ccc;padding:4px 8px;text-align:left}"
           ".kpi{font-size:2rem;font-weight:600}.meta{color:#555;font-size:12px}h2{margin-top:2rem}</style></head><body>",
           f"<h1>{_e(defn['title'])}</h1>"]
    if defn.get("description"):
        out.append(f"<p>{_e(defn['description'])}</p>")
    for label, key in (("Objective", "objective"), ("Procedure", "procedure")):
        if defn.get(key):
            out.append(f"<h2>{label}</h2><p>{_e(defn[key])}</p>")
    if params:
        out.append("<p class='meta'>Parameters: " + ", ".join(f"{_e(k)} = {_e(v)}" for k, v in params.items()) + "</p>")
    for b in defn["blocks"]:
        if b["type"] == "text":
            out.append("".join(f"<p>{_e(p)}</p>" for p in b["content"].split("\n\n")))
            continue
        res = results.get(b["id"], {"columns": [], "rows": []})
        cols, rows = res["columns"], res["rows"]
        if b.get("title"):
            out.append(f"<h2>{_e(b['title'])}</h2>")
        if b["type"] == "kpi":
            out.append(f"<div class='kpi'>{_e(rows[0].get(b['value_column'])) if rows else ''}</div>")
        elif b["type"] == "chart":
            ch = b["chart"]
            out.append(_svg_chart(ch["kind"], ch["x"], ch["y"], rows))
        else:
            out.append("<table><thead><tr>" + "".join(f"<th>{_e(c['label'])}</th>" for c in cols) + "</tr></thead><tbody>")
            for r in rows:
                out.append("<tr>" + "".join(f"<td>{_e(r.get(c['name']))}</td>" for c in cols) + "</tr>")
            out.append("</tbody></table>")
    if defn.get("conclusion"):
        out.append(f"<h2>Conclusion</h2><p>{_e(defn['conclusion'])}</p>")
    foot = " | ".join(f"{k}: {_e(v)}" for k, v in meta.items() if v not in (None, ""))
    out.append(f"<hr><p class='meta'>{foot}</p></body></html>")
    return "".join(out)
