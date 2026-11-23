"""
Reporting subsystem, phase 0: governed dataset catalog + safe query compiler.

Purpose: let users (and AI, and other products) ask questions of Intelligenza
data without ever writing SQL against raw tables. A *dataset* is a named,
curated, read-only view declared in code: its columns, types, labels and the
permission needed to read it. A *query spec* (plain JSON) picks columns,
filters, grouping and ordering from one dataset. compile_query() validates the
spec against the dataset and produces parameterised SQL.

Safety model (why this is not an injection risk):
  * Every identifier in the generated SQL comes from the registry, never from
    the request. Column names are validated against a strict regex when a
    dataset is registered, and a spec can only reference registered names.
  * Every value travels as a bound parameter (%s), after type coercion.
  * Operators and aggregate functions come from fixed allow-lists.
  * Row count is capped; execution (reporting_endpoints.py) runs in a
    READ ONLY transaction with a statement timeout.

Reperformance (see docs/report-builder-and-audit-automation.md): every run
yields a *manifest* — dataset and version, normalised spec, the exact SQL text
and parameters, as-of time, row count and a SHA-256 of the canonical result —
so an independent, trained person can re-run the same query and check the
result against the recorded hash. The manifest is deterministic: the same spec
always compiles to the same SQL, and the result hash ignores nothing that
affects the numbers. Source data can change after as_of, which is why a
persisted snapshot of the result rows (a later phase) is what makes a report
reperformable after the fact; the manifest alone proves *how* it was produced.

Pure module: no DB, no HTTP. Tenancy needs nothing here: the query runs on the
request's tenant-bound connection, so a dataset only ever sees one tenant.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

ENGINE_VERSION = "1"
MANIFEST_VERSION = 1

_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_TYPES = ("string", "number", "date", "datetime", "bool")

# operator -> (SQL fragment, arity). `in`/`between` handled specially.
_OPS = {
    "eq": "= %s", "ne": "<> %s", "gt": "> %s", "gte": ">= %s", "lt": "< %s", "lte": "<= %s",
    "contains": "ILIKE %s", "in": "IN", "between": "BETWEEN %s AND %s",
    "is_null": "IS NULL", "not_null": "IS NOT NULL",
}
_OPS_BY_TYPE = {
    "string":   {"eq", "ne", "contains", "in", "is_null", "not_null"},
    "number":   {"eq", "ne", "gt", "gte", "lt", "lte", "in", "between", "is_null", "not_null"},
    "date":     {"eq", "ne", "gt", "gte", "lt", "lte", "between", "is_null", "not_null"},
    "datetime": {"eq", "ne", "gt", "gte", "lt", "lte", "between", "is_null", "not_null"},
    "bool":     {"eq", "ne", "is_null", "not_null"},
}
_AGGS = {"count", "sum", "avg", "min", "max"}
_NUMERIC_AGGS = {"sum", "avg"}
MAX_IN_VALUES = 200
MAX_FILTERS = 25


class QueryError(ValueError):
    """The query spec is invalid. Safe to show to the caller."""


@dataclass(frozen=True)
class Column:
    name: str
    label: str
    type: str = "string"
    description: str = ""

    def __post_init__(self):
        if not _IDENT_RE.match(self.name):
            raise ValueError(f"invalid column name {self.name!r}")
        if self.type not in _TYPES:
            raise ValueError(f"invalid column type {self.type!r}")


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    description: str
    version: str
    # A fixed SELECT that exposes exactly `columns` (by alias). Contains no
    # user input and no %-placeholders. Declared in code, reviewed like code.
    source_sql: str
    columns: tuple
    admin_only: bool = False
    max_rows: int = 5000
    default_rows: int = 500
    # Underlying tables, recorded in the manifest so a reviewer knows what the
    # numbers came from without reading the SQL.
    source_tables: tuple = ()

    def __post_init__(self):
        if not _IDENT_RE.match(self.key):
            raise ValueError(f"invalid dataset key {self.key!r}")
        if "%" in self.source_sql:
            raise ValueError("dataset source_sql must not contain %")
        names = [c.name for c in self.columns]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate column in dataset {self.key}")

    def column(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise QueryError(f"unknown column {name!r} for dataset {self.key!r}")

    def describe(self) -> dict:
        return {
            "key": self.key, "label": self.label, "description": self.description,
            "version": self.version, "admin_only": self.admin_only, "max_rows": self.max_rows,
            "source_tables": list(self.source_tables),
            "columns": [{"name": c.name, "label": c.label, "type": c.type, "description": c.description}
                        for c in self.columns],
        }


_REGISTRY: dict[str, Dataset] = {}


def register(ds: Dataset) -> Dataset:
    if ds.key in _REGISTRY:
        raise ValueError(f"dataset {ds.key!r} already registered")
    _REGISTRY[ds.key] = ds
    return ds


def get_dataset(key: str) -> Dataset:
    ds = _REGISTRY.get(key)
    if ds is None:
        raise QueryError(f"unknown dataset {key!r}")
    return ds


def list_datasets(is_admin: bool = False) -> list[Dataset]:
    return [d for d in _REGISTRY.values() if is_admin or not d.admin_only]


# ── value coercion ────────────────────────────────────────────────────────

def _coerce(col: Column, value: Any) -> Any:
    try:
        if col.type == "string":
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise ValueError
            return str(value)
        if col.type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError
            if isinstance(value, int):
                return value
            f = float(value)
            if f != f or f in (float("inf"), float("-inf")):
                raise ValueError
            return f
        if col.type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in ("true", "false"):
                return value.lower() == "true"
            raise ValueError
        if col.type == "date":
            return date.fromisoformat(str(value)[:10]).isoformat()
        if col.type == "datetime":
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except (ValueError, TypeError):
        pass
    raise QueryError(f"value {value!r} is not a valid {col.type} for column {col.name!r}")


# ── compiler ──────────────────────────────────────────────────────────────

@dataclass
class CompiledQuery:
    sql: str
    params: list
    spec: dict          # normalised spec (what the manifest records)
    dataset: Dataset
    output_columns: list[dict] = field(default_factory=list)


def compile_query(spec: dict, is_admin: bool = False) -> CompiledQuery:
    """Validate `spec` against its dataset and build parameterised SQL.

    spec = {
      "dataset": "controls",
      "columns":    ["control_ref", "name"],                 # plain columns (or group-by columns)
      "filters":    [{"column": "framework", "op": "eq", "value": "SOX"}],
      "group_by":   ["category"],
      "aggregates": [{"fn": "count", "column": "*", "as": "n"}],
      "order_by":   [{"column": "n", "dir": "desc"}],
      "limit":      100
    }
    Either `columns` or `aggregates` (or both with group_by) must be present.
    """
    if not isinstance(spec, dict):
        raise QueryError("query spec must be an object")
    ds = get_dataset(spec.get("dataset", ""))
    if ds.admin_only and not is_admin:
        raise QueryError(f"dataset {ds.key!r} is restricted")

    columns = spec.get("columns") or []
    group_by = spec.get("group_by") or []
    aggregates = spec.get("aggregates") or []
    filters = spec.get("filters") or []
    order_by = spec.get("order_by") or []
    if not isinstance(columns, list) or not isinstance(group_by, list) or not isinstance(aggregates, list) \
            or not isinstance(filters, list) or not isinstance(order_by, list):
        raise QueryError("columns, group_by, aggregates, filters and order_by must be lists")
    if not columns and not aggregates:
        raise QueryError("select at least one column or aggregate")
    if len(filters) > MAX_FILTERS:
        raise QueryError(f"at most {MAX_FILTERS} filters")

    for name in columns + group_by:
        if not isinstance(name, str):
            raise QueryError("column names must be strings")
        ds.column(name)
    if aggregates or group_by:
        # every plain selected column must be grouped
        for name in columns:
            if name not in group_by:
                raise QueryError(f"column {name!r} must appear in group_by when aggregating")

    select_parts: list[str] = []
    out_cols: list[dict] = []
    aliases: dict[str, str] = {}  # output alias -> type
    for name in columns:
        c = ds.column(name)
        select_parts.append(f'"{c.name}"')
        out_cols.append({"name": c.name, "label": c.label, "type": c.type})
        aliases[c.name] = c.type

    norm_aggs = []
    for a in aggregates:
        if not isinstance(a, dict):
            raise QueryError("each aggregate must be an object")
        fn = str(a.get("fn", "")).lower()
        if fn not in _AGGS:
            raise QueryError(f"aggregate fn must be one of {sorted(_AGGS)}")
        col = a.get("column", "*")
        alias = a.get("as") or (f"{fn}_{col}" if col != "*" else fn)
        if not isinstance(alias, str) or not _IDENT_RE.match(alias):
            raise QueryError(f"invalid aggregate alias {alias!r}")
        if alias in aliases:
            raise QueryError(f"duplicate output name {alias!r}")
        if col == "*":
            if fn != "count":
                raise QueryError("only count may use column '*'")
            expr, typ = "COUNT(*)", "number"
        else:
            if not isinstance(col, str):
                raise QueryError("aggregate column must be a string")
            c = ds.column(col)
            if fn in _NUMERIC_AGGS and c.type != "number":
                raise QueryError(f"{fn} needs a number column; {col!r} is {c.type}")
            if fn in ("min", "max") and c.type == "bool":
                raise QueryError(f"{fn} is not valid on a bool column")
            expr = f'{fn.upper()}("{c.name}")'
            typ = "number" if fn in ("count", "sum", "avg") else c.type
        select_parts.append(f'{expr} AS "{alias}"')
        out_cols.append({"name": alias, "label": alias, "type": typ})
        aliases[alias] = typ
        norm_aggs.append({"fn": fn, "column": col, "as": alias})

    where_parts: list[str] = []
    params: list = []
    norm_filters = []
    for f in filters:
        if not isinstance(f, dict):
            raise QueryError("each filter must be an object")
        c = ds.column(str(f.get("column", "")))
        op = str(f.get("op", ""))
        if op not in _OPS or op not in _OPS_BY_TYPE[c.type]:
            raise QueryError(f"operator {op!r} is not allowed on {c.type} column {c.name!r}")
        value = f.get("value")
        if op in ("is_null", "not_null"):
            where_parts.append(f'"{c.name}" {_OPS[op]}')
            norm_filters.append({"column": c.name, "op": op})
            continue
        if op == "in":
            if not isinstance(value, list) or not value or len(value) > MAX_IN_VALUES:
                raise QueryError(f"'in' needs a list of 1 to {MAX_IN_VALUES} values")
            vals = [_coerce(c, v) for v in value]
            where_parts.append(f'"{c.name}" IN ({", ".join(["%s"] * len(vals))})')
            params.extend(vals)
            norm_filters.append({"column": c.name, "op": op, "value": vals})
        elif op == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise QueryError("'between' needs exactly two values")
            lo, hi = _coerce(c, value[0]), _coerce(c, value[1])
            where_parts.append(f'"{c.name}" {_OPS[op]}')
            params.extend([lo, hi])
            norm_filters.append({"column": c.name, "op": op, "value": [lo, hi]})
        elif op == "contains":
            v = _coerce(c, value)
            escaped = v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where_parts.append(f'"{c.name}" ILIKE %s')
            params.append(f"%{escaped}%")
            norm_filters.append({"column": c.name, "op": op, "value": v})
        else:
            v = _coerce(c, value)
            where_parts.append(f'"{c.name}" {_OPS[op]}')
            params.append(v)
            norm_filters.append({"column": c.name, "op": op, "value": v})

    order_parts, norm_order = [], []
    for o in order_by:
        if not isinstance(o, dict):
            raise QueryError("each order_by must be an object")
        col = str(o.get("column", ""))
        direction = str(o.get("dir", "asc")).lower()
        if direction not in ("asc", "desc"):
            raise QueryError("order_by dir must be asc or desc")
        if col not in aliases:
            raise QueryError(f"cannot order by {col!r}: not a selected column or aggregate")
        order_parts.append(f'"{col}" {direction.upper()}')
        norm_order.append({"column": col, "dir": direction})

    limit = spec.get("limit", ds.default_rows)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise QueryError("limit must be a positive integer")
    limit = min(limit, ds.max_rows)

    sql = f'SELECT {", ".join(select_parts)} FROM ({ds.source_sql}) AS t'
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    if group_by:
        sql += " GROUP BY " + ", ".join(f'"{g}"' for g in group_by)
    if order_parts:
        sql += " ORDER BY " + ", ".join(order_parts)
    sql += " LIMIT %s"
    params.append(limit)

    normalised = {
        "dataset": ds.key, "columns": list(columns), "filters": norm_filters,
        "group_by": list(group_by), "aggregates": norm_aggs, "order_by": norm_order, "limit": limit,
    }
    return CompiledQuery(sql=sql, params=params, spec=normalised, dataset=ds, output_columns=out_cols)


# ── manifest (reperformance record) ───────────────────────────────────────

def _json_default(o: Any):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if hasattr(o, "__float__"):
        return float(o)
    return str(o)


def canonical_rows_json(rows: list[dict]) -> str:
    """Stable serialisation the result hash is computed over."""
    return json.dumps(rows, sort_keys=True, separators=(",", ":"), default=_json_default)


def build_manifest(cq: CompiledQuery, rows: list[dict], as_of: Optional[datetime] = None,
                   truncated: bool = False) -> dict:
    as_of = as_of or datetime.now(timezone.utc)
    return {
        "manifest_version": MANIFEST_VERSION,
        "engine_version": ENGINE_VERSION,
        "dataset": {"key": cq.dataset.key, "version": cq.dataset.version,
                    "source_tables": list(cq.dataset.source_tables)},
        "spec": cq.spec,
        "sql": cq.sql,
        "params": [_json_default(p) if isinstance(p, (datetime, date)) else p for p in cq.params],
        "as_of": as_of.isoformat(),
        "row_count": len(rows),
        "truncated": truncated,
        "columns": cq.output_columns,
        "result_sha256": hashlib.sha256(canonical_rows_json(rows).encode("utf-8")).hexdigest(),
    }


# ── starter datasets ──────────────────────────────────────────────────────
# Each source_sql is reviewed code. Add datasets by calling register() here or
# from the owning product's module at import time.

register(Dataset(
    key="controls", label="Control library",
    description="Every control in the control library, with its framework, category and domain.",
    version="1",
    source_sql=("SELECT control_ref, framework, name, description, category, domain "
                "FROM controls_library"),
    columns=(
        Column("control_ref", "Control", "string"),
        Column("framework", "Framework", "string"),
        Column("name", "Name", "string"),
        Column("description", "Description", "string"),
        Column("category", "Category", "string"),
        Column("domain", "Domain", "string"),
    ),
    source_tables=("controls_library",),
))

register(Dataset(
    key="risk_scores", label="Risk scores by run",
    description="Scored risks from each pipeline run, with the company and run time.",
    version="1",
    source_sql=("SELECT s.run_id, r.ticker, r.run_at, s.risk_ref, s.risk_name, s.category, "
                "s.base_score, s.score, s.rag_status, s.velocity, s.control_env "
                "FROM risk_scores s JOIN risk_loop_runs r ON r.id = s.run_id"),
    columns=(
        Column("run_id", "Run", "number"),
        Column("ticker", "Ticker", "string"),
        Column("run_at", "Run time", "datetime"),
        Column("risk_ref", "Risk", "string"),
        Column("risk_name", "Risk name", "string"),
        Column("category", "Category", "string"),
        Column("base_score", "Base score", "number"),
        Column("score", "Score", "number"),
        Column("rag_status", "RAG", "string"),
        Column("velocity", "Velocity", "number"),
        Column("control_env", "Control environment", "string"),
    ),
    source_tables=("risk_scores", "risk_loop_runs"),
))

register(Dataset(
    key="pbc_evidence", label="PBC evidence log",
    description="Evidence items logged against controls, with period, signature and quality flags.",
    version="1",
    source_sql=("SELECT id, control_id, title, period_start, period_end, collected_date, "
                "has_signature, requires_signature, "
                "jsonb_array_length(quality_flags) AS quality_flag_count, created_by, created_at "
                "FROM observability.pbc_evidence"),
    columns=(
        Column("id", "Evidence id", "number"),
        Column("control_id", "Control", "string"),
        Column("title", "Title", "string"),
        Column("period_start", "Period start", "date"),
        Column("period_end", "Period end", "date"),
        Column("collected_date", "Collected", "date"),
        Column("has_signature", "Signed", "bool"),
        Column("requires_signature", "Signature required", "bool"),
        Column("quality_flag_count", "Quality flags", "number"),
        Column("created_by", "Logged by", "string"),
        Column("created_at", "Logged at", "datetime"),
    ),
    source_tables=("observability.pbc_evidence",),
))

register(Dataset(
    key="audit_log", label="Platform audit log",
    description="Sign-ins, access changes, refused access and privileged actions (admin only).",
    version="1", admin_only=True,
    source_sql=("SELECT id, occurred_at, category, action, actor, target, ip_address, outcome "
                "FROM observability.audit_log"),
    columns=(
        Column("id", "Entry", "number"),
        Column("occurred_at", "When", "datetime"),
        Column("category", "Category", "string"),
        Column("action", "Action", "string"),
        Column("actor", "Actor", "string"),
        Column("target", "Target", "string"),
        Column("ip_address", "IP address", "string"),
        Column("outcome", "Outcome", "string"),
    ),
    source_tables=("observability.audit_log",),
))
