/* ============================================================
   Tick & Tie — compare two sources key by key and keep a signed result.

   Backed by tieout_endpoints.py (+ reporting snapshots as sources):
     GET  /reporting/tieouts            list
     GET  /reporting/tieouts/legend     tickmark legend
     POST /reporting/tieouts            tie two sources, store a signed result
     GET  /reporting/tieouts/{id}       full record (spec, sources, items, signature)
     GET  /reporting/tieouts/{id}/verify
     GET  /reporting/snapshots          signed snapshots usable as a source

   A source is a signed snapshot, or figures pasted in with a label saying where
   they came from. The server does the matching; this screen only builds the
   request and shows the stored record. Objective, procedure and conclusion are
   required so an independent reviewer can reperform the tie from the record alone.
   ============================================================ */

const _TO = "/api/reporting";

async function _toFetch(path, opts = {}) {
  const res = await fetch(_TO + path, {
    credentials: "include",
    ...opts,
    headers: { ...(opts.body ? { "Content-Type": "application/json" } : {}), ...(opts.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ---------- pure helpers (no React) ----------

/** Parse pasted CSV or tab-separated text with a header row into [{col: value}].
 *  Handles quoted fields, "" escapes, CRLF and blank lines. Values stay strings
 *  (the server parses amounts as exact decimals). Throws on ragged rows. */
function toParseRows(text) {
  const src = String(text || "").replace(/^﻿/, "");
  const first = src.split(/\r?\n/).find(l => l.trim()) || "";
  const delim = first.includes("\t") ? "\t" : ",";
  const records = [];
  let row = [], cell = "", q = false;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (q) {
      if (c === '"' && src[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') q = false;
      else cell += c;
    } else if (c === '"' && cell === "") q = true;
    else if (c === delim) { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && src[i + 1] === "\n") i++;
      row.push(cell); cell = "";
      if (row.some(v => v.trim() !== "")) records.push(row);
      row = [];
    } else cell += c;
  }
  row.push(cell);
  if (row.some(v => v.trim() !== "")) records.push(row);
  if (records.length < 2) throw new Error("Paste a header row and at least one data row.");
  const header = records[0].map(h => h.trim());
  if (header.some(h => !h)) throw new Error("Every column needs a header name.");
  if (new Set(header).size !== header.length) throw new Error("Column headers must be unique.");
  return records.slice(1).map((r, i) => {
    if (r.length !== header.length) throw new Error(`Row ${i + 2} has ${r.length} values but there are ${header.length} columns.`);
    return Object.fromEntries(header.map((h, j) => [h, r[j].trim()]));
  });
}

/** Column names found in pasted text ([] if it does not parse yet). */
function toHeaderOf(text) {
  try { return Object.keys(toParseRows(text)[0] || {}); } catch { return []; }
}

/** Form state -> POST body. Throws a plain-language Error for anything missing. */
function toBuildRequest(f) {
  const need = (v, what) => { if (!String(v || "").trim()) throw new Error(`${what} is required.`); return String(v).trim(); };
  const side = (s, name) => {
    need(s.key, `${name} key column`); need(s.amount, `${name} amount column`); need(s.label, `${name} label`);
    if (s.type === "snapshot") {
      const id = Number(s.snapshotId);
      if (!Number.isInteger(id) || id <= 0) throw new Error(`Pick a snapshot for the ${name.toLowerCase()} source.`);
      return { source: { type: "snapshot", id, label: s.label.trim() } };
    }
    return { source: { type: "inline", label: s.label.trim(), rows: toParseRows(s.text) } };
  };
  const L = side(f.left, "Left"), R = side(f.right, "Right");
  const tol = {};
  if (String(f.tolAbs || "").trim()) tol.abs = String(f.tolAbs).trim();
  if (String(f.tolPct || "").trim()) tol.pct = String(f.tolPct).trim();
  return {
    spec: {
      left: { label: f.left.label.trim(), key: f.left.key.trim(), amount: f.left.amount.trim() },
      right: { label: f.right.label.trim(), key: f.right.key.trim(), amount: f.right.amount.trim() },
      tolerance: tol,
      objective: need(f.objective, "Objective"), procedure: need(f.procedure, "Procedure"),
    },
    left_source: L.source, right_source: R.source,
    conclusion: need(f.conclusion, "Conclusion"),
    engagement: String(f.engagement || "").trim() || null,
    note: String(f.note || "").trim() || null,
  };
}

const TO_MARK_STYLE = {
  T: ["var(--green-soft)", "var(--green-ink)"],
  R: ["var(--amber-soft)", "var(--amber-ink)"],
  D: ["var(--red-soft)", "var(--red-ink)"],
  L: ["var(--red-soft)", "var(--red-ink)"],
  X: ["var(--red-soft)", "var(--red-ink)"],
};

/** Items to show: all, or only the exceptions (D, L, X). */
function toFilterItems(items, exceptionsOnly) {
  return exceptionsOnly ? items.filter(i => ["D", "L", "X"].includes(i.mark)) : items;
}

function toEmptyForm() {
  const side = (label) => ({ type: "inline", label, key: "", amount: "", snapshotId: "", text: "" });
  return { engagement: "", note: "", objective: "", procedure: "", conclusion: "", tolAbs: "", tolPct: "",
    left: side(""), right: side("") };
}

// ---------- small UI pieces ----------

const _toInput = { fontSize: 12, padding: "5px 8px", border: "1px solid var(--line)", borderRadius: 5, background: "var(--surface)", color: "var(--ink)", width: "100%", boxSizing: "border-box" };
const _toLabel = { fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600, marginBottom: 3, display: "block" };
const _toCard = { border: "1px solid var(--line)", borderRadius: 8, padding: 14, background: "var(--surface)", marginBottom: 12 };
const _toBtn = (primary) => ({
  fontSize: 12, padding: "5px 12px", borderRadius: 5, cursor: "pointer", fontWeight: 600,
  border: primary ? "1px solid var(--ink)" : "1px solid var(--line)",
  background: primary ? "var(--ink)" : "var(--surface)", color: primary ? "var(--surface)" : "var(--ink)",
});

function ToField({ label, children, style }) {
  return <div style={style}><label style={_toLabel}>{label}</label>{children}</div>;
}

function ToMark({ mark }) {
  const m = TO_MARK_STYLE[mark] || ["var(--surface-2)", "var(--ink-3)"];
  return <span style={{ display: "inline-block", minWidth: 20, textAlign: "center", fontSize: 11, fontWeight: 800, padding: "1px 6px", borderRadius: 4, background: m[0], color: m[1] }}>{mark}</span>;
}

function ToOutcome({ outcome }) {
  const ok = outcome === "tied";
  return <span style={{ fontSize: 9.5, fontWeight: 700, padding: "2px 7px", borderRadius: 999, textTransform: "uppercase",
    background: ok ? "var(--green-soft)" : "var(--red-soft)", color: ok ? "var(--green-ink)" : "var(--red-ink)" }}>{ok ? "tied" : "exceptions"}</span>;
}

// ---------- new tie-out form ----------

function ToSourceEditor({ title, value, onChange, snapshots }) {
  const set = (p) => onChange({ ...value, ...p });
  const cols = value.type === "inline" ? toHeaderOf(value.text) : [];
  const colInput = (field, placeholder) => cols.length
    ? <select style={_toInput} value={value[field]} onChange={e => set({ [field]: e.target.value })}>
        <option value="">— pick —</option>{cols.map(c => <option key={c}>{c}</option>)}
      </select>
    : <input style={_toInput} placeholder={placeholder} value={value[field]} onChange={e => set({ [field]: e.target.value })} />;
  return (
    <div style={{ ..._toCard, flex: 1, minWidth: 300, marginBottom: 0 }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>{title}</div>
      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        {["inline", "snapshot"].map(t => (
          <button key={t} style={_toBtn(value.type === t)} onClick={() => set({ type: t })}>
            {t === "inline" ? "Paste figures" : "Signed snapshot"}
          </button>
        ))}
      </div>
      <ToField label="Label (what is this and where did it come from?)">
        <input style={_toInput} value={value.label} placeholder={value.type === "inline" ? "e.g. Bank statement, Oct 2026, downloaded 3 Nov" : "e.g. GL trial balance"} onChange={e => set({ label: e.target.value })} />
      </ToField>
      {value.type === "snapshot" ? (
        <ToField label="Snapshot" style={{ marginTop: 8 }}>
          <select style={_toInput} value={value.snapshotId} onChange={e => set({ snapshotId: e.target.value })}>
            <option value="">— pick a snapshot —</option>
            {(snapshots || []).map(s => <option key={s.id} value={s.id}>#{s.id} · {s.dataset_key} · {s.row_count} rows{s.engagement ? ` · ${s.engagement}` : ""}</option>)}
          </select>
        </ToField>
      ) : (
        <ToField label="Figures (paste CSV or tab-separated, with a header row)" style={{ marginTop: 8 }}>
          <textarea style={{ ..._toInput, height: 120, fontFamily: "var(--mono, monospace)", fontSize: 11.5 }} spellCheck={false}
            placeholder={"ref,amount\nINV-1,100.00\nINV-2,250.50"} value={value.text} onChange={e => set({ text: e.target.value })} />
        </ToField>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <ToField label="Key column" style={{ flex: 1 }}>{colInput("key", "e.g. ref")}</ToField>
        <ToField label="Amount column" style={{ flex: 1 }}>{colInput("amount", "e.g. amount")}</ToField>
      </div>
    </div>
  );
}

function ToForm({ snapshots, onCreated }) {
  const [f, setF] = React.useState(toEmptyForm);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState(null);
  const patch = (p) => setF(v => ({ ...v, ...p }));

  async function submit() {
    setBusy(true); setErr(null);
    try {
      const body = toBuildRequest(f);
      const saved = await _toFetch("/tieouts", { method: "POST", body: JSON.stringify(body) });
      onCreated(saved.id);
      setF(toEmptyForm());
    } catch (e) { setErr(e.message || String(e)); } finally { setBusy(false); }
  }

  return (
    <div>
      <div style={_toCard}>
        <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 8 }}>
          Say what the test is for and how it is performed. An independent reviewer must be able to reperform it from the saved record alone.
        </div>
        <div style={{ display: "grid", gap: 8 }}>
          <ToField label="Objective"><textarea style={{ ..._toInput, height: 44 }} value={f.objective} onChange={e => patch({ objective: e.target.value })} placeholder="e.g. Confirm October cash per the GL agrees to the bank statement." /></ToField>
          <ToField label="Procedure"><textarea style={{ ..._toInput, height: 44 }} value={f.procedure} onChange={e => patch({ procedure: e.target.value })} placeholder="e.g. Matched each line by reference; compared amounts; investigated differences over the tolerance." /></ToField>
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
          <ToField label="Engagement" style={{ width: 220 }}><input style={_toInput} value={f.engagement} onChange={e => patch({ engagement: e.target.value })} placeholder="optional" /></ToField>
          <ToField label="Tolerance: absolute" style={{ width: 150 }}><input style={_toInput} value={f.tolAbs} onChange={e => patch({ tolAbs: e.target.value })} placeholder="0 (exact)" /></ToField>
          <ToField label="Tolerance: % of left" style={{ width: 150 }}><input style={_toInput} value={f.tolPct} onChange={e => patch({ tolPct: e.target.value })} placeholder="0" /></ToField>
          <ToField label="Note" style={{ flex: 1, minWidth: 200 }}><input style={_toInput} value={f.note} onChange={e => patch({ note: e.target.value })} placeholder="optional" /></ToField>
        </div>
        <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 6 }}>
          A difference is within tolerance if it is within either amount. Leave both blank to tie exact figures only.
        </div>
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
        <ToSourceEditor title="Left source" value={f.left} onChange={left => patch({ left })} snapshots={snapshots} />
        <ToSourceEditor title="Right source" value={f.right} onChange={right => patch({ right })} snapshots={snapshots} />
      </div>

      <div style={_toCard}>
        <ToField label="Conclusion (what does the result mean for the audit?)">
          <textarea style={{ ..._toInput, height: 44 }} value={f.conclusion} onChange={e => patch({ conclusion: e.target.value })} />
        </ToField>
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button style={_toBtn(true)} disabled={busy} onClick={submit}>{busy ? "Working…" : "Tie out and sign"}</button>
        <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>The result is permanent once saved.</span>
      </div>
      {err && <div className="mono" style={{ fontSize: 11.5, color: "var(--red-ink)", marginTop: 10, whiteSpace: "pre-wrap" }}>{err}</div>}
    </div>
  );
}

// ---------- stored record ----------

function ToRecord({ id }) {
  const [rec, setRec] = React.useState(null);
  const [check, setCheck] = React.useState(null);
  const [exOnly, setExOnly] = React.useState(false);
  const [err, setErr] = React.useState(null);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    setRec(null); setCheck(null); setErr(null);
    _toFetch(`/tieouts/${id}`).then(setRec).catch(e => setErr(e.message));
  }, [id]);

  async function verify() {
    setBusy(true); setErr(null);
    try { setCheck(await _toFetch(`/tieouts/${id}/verify`)); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  if (err && !rec) return <div className="mono" style={{ fontSize: 11.5, color: "var(--red-ink)" }}>{err}</div>;
  if (!rec) return <div style={{ fontSize: 12, color: "var(--ink-3)" }}>Loading…</div>;

  const p = rec.payload, s = p.summary, items = toFilterItems(rec.items, exOnly);
  const cell = { padding: "4px 8px", borderBottom: "1px solid var(--line)", fontSize: 12 };
  const num = { ...cell, textAlign: "right", fontFamily: "var(--mono, monospace)" };

  return (
    <div>
      <div style={_toCard}>
        <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 6 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>Tie-out #{rec.id}</div>
          <ToOutcome outcome={rec.outcome} />
          <div style={{ flex: 1 }} />
          <button style={_toBtn(false)} disabled={busy} onClick={verify}>{busy ? "Checking…" : "Verify"}</button>
        </div>
        <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
          {p.created_by} · {String(rec.created_at).slice(0, 16).replace("T", " ")}{p.engagement ? ` · ${p.engagement}` : ""}
        </div>
        {check && (
          <div style={{ marginTop: 8, fontSize: 12, color: check.intact ? "var(--green-ink)" : "var(--red-ink)", fontWeight: 600 }}>
            {check.intact
              ? "Verified: signature, item hash, sources and the arithmetic all check out."
              : `Does not verify: ${check.detail}`}
          </div>
        )}
        {err && <div className="mono" style={{ fontSize: 11.5, color: "var(--red-ink)", marginTop: 6 }}>{err}</div>}
        <div style={{ display: "grid", gap: 6, marginTop: 10, fontSize: 12 }}>
          <div><b>Objective.</b> {p.spec.objective}</div>
          <div><b>Procedure.</b> {p.spec.procedure}</div>
          <div><b>Tolerance.</b> {p.tolerance_statement}</div>
          <div><b>Conclusion.</b> {p.conclusion}</div>
          {p.note && <div><b>Note.</b> {p.note}</div>}
        </div>
      </div>

      <div style={_toCard}>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Sources</div>
        {p.sources.map(src => (
          <div key={src.role} style={{ fontSize: 12, marginBottom: 4 }}>
            <b>{src.role === "left" ? "Left" : "Right"}:</b> {src.label} — {src.type === "snapshot"
              ? `signed snapshot #${src.snapshot_id} (${src.dataset}, as of ${String(src.as_of).slice(0, 16).replace("T", " ")})`
              : `figures supplied by ${src.entered_by}; not independently sourced`}
            {" · "}{src.row_count} rows
            <span className="mono" style={{ color: "var(--ink-4)", fontSize: 10.5 }}> · sha256 {src.rows_sha256.slice(0, 12)}…</span>
          </div>
        ))}
      </div>

      <div style={_toCard}>
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, marginBottom: 8 }}>
          <span><b>Left total</b> <span className="mono">{s.left_total}</span></span>
          <span><b>Right total</b> <span className="mono">{s.right_total}</span></span>
          <span><b>Net difference</b> <span className="mono">{s.net_difference}</span></span>
          <span><b>Items</b> {s.items}</span>
          <span><b>Exceptions</b> {s.exceptions}</span>
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
          {Object.entries(p.legend).map(([m, text]) => (
            <div key={m} style={{ fontSize: 11, color: "var(--ink-3)", display: "flex", gap: 6, alignItems: "baseline", maxWidth: 360 }}>
              <ToMark mark={m} /> <span><b style={{ color: "var(--ink)" }}>{s.counts[m]}</b> · {text}</span>
            </div>
          ))}
        </div>
        <label style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center", marginBottom: 8 }}>
          <input type="checkbox" checked={exOnly} onChange={e => setExOnly(e.target.checked)} /> Exceptions only
        </label>
        <div style={{ maxHeight: 460, overflow: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ position: "sticky", top: 0, background: "var(--surface-2)" }}>
                {["Mark", "Key", p.spec.left.label, p.spec.right.label, "Difference"].map((h, i) => (
                  <th key={h} style={{ ...cell, textAlign: i >= 2 ? "right" : "left", fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--ink-3)" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map(i => (
                <tr key={i.key}>
                  <td style={cell}><ToMark mark={i.mark} /></td>
                  <td style={cell}>{i.key}{(i.left_lines > 1 || i.right_lines > 1) && <span style={{ color: "var(--ink-4)", fontSize: 10.5 }}> ({i.left_lines}+{i.right_lines} lines)</span>}</td>
                  <td style={num}>{i.left ?? "—"}</td>
                  <td style={num}>{i.right ?? "—"}</td>
                  <td style={num}>{i.difference ?? "—"}</td>
                </tr>
              ))}
              {!items.length && <tr><td style={{ ...cell, color: "var(--ink-3)" }} colSpan={5}>{exOnly ? "No exceptions." : "No items."}</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ---------- screen ----------

function TieOutScreen() {
  const [list, setList] = React.useState(null);
  const [snapshots, setSnapshots] = React.useState([]);
  const [selected, setSelected] = React.useState("new");     // "new" | id
  const [error, setError] = React.useState(null);

  const load = React.useCallback(() =>
    _toFetch("/tieouts").then(d => { setList(d.tieouts || []); setError(null); }).catch(e => setError(e.message)), []);

  React.useEffect(() => {
    load();
    _toFetch("/snapshots").then(d => setSnapshots(d.snapshots || [])).catch(() => {});
  }, [load]);

  return (
    <div className="scope-screen" data-screen-label="Tick & Tie">
      <div className="panel-head">
        <div>
          <div className="kicker">Audit &amp; Compliance · Tick &amp; Tie</div>
          <div className="panel-title mt-8">Tick &amp; Tie</div>
          <div className="panel-sub">
            Compare two sources item by item. Every item gets a tickmark and the result is a signed, permanent record with its
            legend, tolerance, sources, procedure and conclusion, so an independent reviewer can reperform and verify it.
          </div>
        </div>
      </div>

      {error && <div className="mono" style={{ fontSize: 11.5, color: "var(--red-ink)", marginBottom: 12 }}>{error}</div>}

      <div style={{ display: "grid", gridTemplateColumns: "230px 1fr", gap: 16, alignItems: "start" }}>
        <div>
          <button style={{ ..._toBtn(true), width: "100%", marginBottom: 10 }} onClick={() => setSelected("new")}>+ New tie-out</button>
          {list === null ? "Loading…" : !list.length ? <div style={{ fontSize: 12, color: "var(--ink-3)" }}>No tie-outs yet.</div> : list.map(t => (
            <div key={t.id} onClick={() => setSelected(t.id)} style={{ ..._toCard, padding: 10, marginBottom: 6, cursor: "pointer",
              borderColor: selected === t.id ? "var(--ink)" : "var(--line)" }}>
              <div style={{ display: "flex", gap: 6, alignItems: "center", justifyContent: "space-between" }}>
                <b style={{ fontSize: 12 }}>#{t.id}</b><ToOutcome outcome={t.outcome} />
              </div>
              <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 3 }}>{t.engagement || "no engagement"}</div>
              <div style={{ fontSize: 10.5, color: "var(--ink-4)" }}>{t.created_by} · {String(t.created_at).slice(0, 10)}</div>
            </div>
          ))}
        </div>
        <div>
          {selected === "new"
            ? <ToForm snapshots={snapshots} onCreated={(id) => { load(); setSelected(id); }} />
            : <ToRecord id={selected} key={selected} />}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { TieOutScreen, toParseRows, toBuildRequest, toFilterItems, toHeaderOf });
