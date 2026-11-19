"""Auto-suggest controls and risks that are common to more than one framework.

Pure functions (no DB / network) so they are easy to test. Similarity is TF-IDF
cosine over normalised name + description + category text. Nothing here writes
anything: callers present the results for human review, and only an accepted
suggestion is persisted (via the existing control-sharing endpoint or the
Framework Discovery flow).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

_INTERNAL_FWS = {"Internal", "Internal Risk Register", "Custom", ""}

_STOP = set("""
a an and are as at be by for from has have in into is it its of on or that the their this to
with within without across all any each per via over under not no than then they them
ensure ensures ensuring including include includes such other use used using
control controls process processes procedure procedures policy policies management managed manage
organisation organization organisational organizational system systems information
risk risks inadequate insufficient lack lacks poor unmanaged unassessed uncontrolled unmaintained
create creates allow allows enable enables expose exposes lead leads increase increases
""".split())

# Synonyms folded to one token so e.g. "authorisation"/"authorization" or
# "supplier"/"vendor" match across frameworks that word things differently.
_SYN = {
    "authorisation": "authoriz", "authorization": "authoriz", "authorised": "authoriz", "authorized": "authoriz",
    "unauthorised": "unauthoriz", "unauthorized": "unauthoriz",
    "supplier": "vendor", "suppliers": "vendor", "thirdparty": "vendor", "outsourcing": "vendor",
    "privilege": "access", "privileges": "access", "privileged": "access", "identity": "access",
    "authentication": "access", "account": "access", "accounts": "access",
    "incident": "incident", "breach": "incident", "breaches": "incident",
    "continuity": "resilience", "disaster": "resilience", "recovery": "resilience", "disruption": "resilience",
    "vulnerability": "vulnerab", "vulnerabilities": "vulnerab", "patch": "vulnerab", "patching": "vulnerab",
    "logging": "log", "logs": "log", "audit": "log", "monitoring": "monitor", "detection": "monitor",
    "configuration": "config", "misconfigured": "config", "configurations": "config",
    "encryption": "crypto", "cryptographic": "crypto", "encrypted": "crypto",
    "ai": "ai", "artificial": "ai", "model": "ai", "models": "ai",
}


def _stem(t: str) -> str:
    if t.endswith("ss"):
        return t
    for suf in ("ations", "ation", "ments", "ment", "ities", "ity", "ings", "ing", "ies", "ed", "es", "s"):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            return t[: -len(suf)] + ("y" if suf == "ies" else "")
    return t


def tokenize(text: str) -> List[str]:
    out = []
    for raw in re.findall(r"[a-z][a-z0-9]+", (text or "").lower().replace("third-party", "thirdparty")):
        if raw in _STOP:
            continue
        t = _SYN.get(raw) or _stem(raw)
        t = _SYN.get(t, t)
        if t and t not in _STOP and len(t) > 2:
            out.append(t)
    return out


class _Index:
    """TF-IDF vectors for a fixed corpus of documents."""

    def __init__(self, docs: List[str]):
        toks = [tokenize(d) for d in docs]
        df: Counter = Counter()
        for t in toks:
            df.update(set(t))
        n = max(len(docs), 1)
        self.idf = {w: math.log((1 + n) / (1 + c)) + 1.0 for w, c in df.items()}
        self.vecs = [self._vec(t) for t in toks]

    def _vec(self, tokens: List[str]) -> Dict[str, float]:
        tf = Counter(tokens)
        v = {w: c * self.idf.get(w, 1.0) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    def query(self, text: str) -> Dict[str, float]:
        return self._vec(tokenize(text))

    @staticmethod
    def cos(a: Dict[str, float], b: Dict[str, float]) -> float:
        if len(a) > len(b):
            a, b = b, a
        return sum(x * b.get(w, 0.0) for w, x in a.items())

    def shared_terms(self, i: int, j: int, k: int = 4) -> List[str]:
        a, b = self.vecs[i], self.vecs[j]
        common = [(a[w] * b[w], w) for w in a if w in b]
        return [w for _, w in sorted(common, reverse=True)[:k]]


def control_frameworks(c: Dict[str, Any]) -> List[str]:
    fws = [c.get("framework")] + list(c.get("shared_frameworks") or [])
    seen, out = set(), []
    for f in fws:
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _ctrl_text(c: Dict[str, Any]) -> str:
    # name counts double: it is the most discriminating field
    return f"{c.get('name', '')} {c.get('name', '')} {c.get('description') or c.get('desc') or ''} {c.get('category', '')}"


def suggest_shared_controls(
    controls: List[Dict[str, Any]],
    threshold: float = 0.30,
    limit: int = 60,
    target_frameworks: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """Suggest 'share control X into framework F' where a control that already
    lives in F is a close textual match for X.

    Internal/Custom-home controls are skipped as sources and targets (they
    already show in every framework column). One suggestion per (control,
    target framework), keeping its best-matching control in that framework.
    """
    targets = set(target_frameworks) if target_frameworks else None
    idx = _Index([_ctrl_text(c) for c in controls])
    best: Dict[tuple, Dict[str, Any]] = {}
    for i, a in enumerate(controls):
        a_home = a.get("framework") or ""
        if a_home in _INTERNAL_FWS:
            continue
        a_fws = set(control_frameworks(a))
        for j, b in enumerate(controls):
            if i == j or (b.get("framework") or "") in _INTERNAL_FWS:
                continue
            score = idx.cos(idx.vecs[i], idx.vecs[j])
            if score < threshold:
                continue
            for fw in control_frameworks(b):
                if fw in a_fws or fw in _INTERNAL_FWS or (targets is not None and fw not in targets):
                    continue
                key = (a["ref"], fw)
                if key not in best or score > best[key]["score"]:
                    best[key] = {
                        "id": f"ctrl:{a['ref']}:{fw}",
                        "kind": "shared_control",
                        "ref": a["ref"], "name": a.get("name", ""),
                        "from_frameworks": control_frameworks(a),
                        "target_framework": fw,
                        "match_ref": b["ref"], "match_name": b.get("name", ""),
                        "match_framework": b.get("framework"),
                        "score": round(score, 3),
                        "shared_terms": idx.shared_terms(i, j),
                    }
    return sorted(best.values(), key=lambda s: -s["score"])[:limit]


def suggest_common_risks(
    risks_by_framework: Dict[str, List[Dict[str, Any]]],
    controls: List[Dict[str, Any]],
    threshold: float = 0.28,
    limit: int = 40,
) -> List[Dict[str, Any]]:
    """Cluster risk statements that recur across two or more frameworks and
    propose the library controls that would address each cluster."""
    flat: List[Dict[str, Any]] = []
    for fw, risks in risks_by_framework.items():
        for r in risks or []:
            if r.get("name"):
                flat.append({**r, "source_framework": r.get("source_framework") or fw})
    if len(flat) < 2:
        return []
    idx = _Index([f"{r['name']} {r.get('category', '')}" for r in flat])

    parent = list(range(len(flat)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pair_scores: Dict[tuple, float] = {}
    for i in range(len(flat)):
        for j in range(i + 1, len(flat)):
            if flat[i]["source_framework"] == flat[j]["source_framework"]:
                continue
            s = idx.cos(idx.vecs[i], idx.vecs[j])
            if s >= threshold:
                pair_scores[(i, j)] = s
                parent[find(i)] = find(j)

    clusters: Dict[int, List[int]] = {}
    for i in range(len(flat)):
        clusters.setdefault(find(i), []).append(i)

    cidx = _Index([_ctrl_text(c) for c in controls]) if controls else None
    out = []
    for members in clusters.values():
        fws = sorted({flat[m]["source_framework"] for m in members})
        if len(fws) < 2:
            continue
        # representative = the member most similar to the others
        def centrality(m: int) -> float:
            return sum(idx.cos(idx.vecs[m], idx.vecs[o]) for o in members if o != m)
        rep = max(members, key=centrality)
        scores = [s for (i, j), s in pair_scores.items() if i in members and j in members]
        suggested = []
        if cidx:
            qv = cidx._vec(tokenize(" ".join(flat[m]["name"] for m in members)))
            ranked = sorted(
                ((cidx.cos(qv, v), c) for v, c in zip(cidx.vecs, controls)), key=lambda x: -x[0])
            suggested = [
                {"ref": c["ref"], "name": c.get("name", ""), "framework": c.get("framework"),
                 "score": round(s, 3)}
                for s, c in ranked[:4] if s >= 0.18
            ]
        out.append({
            "id": "risk:" + "|".join(sorted(str(flat[m].get("id") or flat[m]["name"]) for m in members)),
            "kind": "common_risk",
            "name": flat[rep]["name"],
            "category": flat[rep].get("category", ""),
            "frameworks": fws,
            "members": [
                {"id": flat[m].get("id"), "name": flat[m]["name"],
                 "framework": flat[m]["source_framework"], "category": flat[m].get("category", "")}
                for m in sorted(members, key=lambda m: flat[m]["source_framework"])
            ],
            "suggested_controls": suggested,
            "score": round(sum(scores) / len(scores), 3) if scores else 0.0,
        })
    out.sort(key=lambda s: (-len(s["frameworks"]), -s["score"]))
    return out[:limit]
