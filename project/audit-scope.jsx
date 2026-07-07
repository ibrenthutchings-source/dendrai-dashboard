/* ============================================================
   Audit Scope monitor — Gantt + Kanban views, organized by sprint.
   Driven by the objectives the loop produces (output.s3.objectives).
   ============================================================ */

const SCOPE_COLUMNS = [
  { id: "planned",   l: "Planned" },
  { id: "fieldwork", l: "In Fieldwork" },
  { id: "review",    l: "Review" },
  { id: "closed",    l: "Closed" },
];

function scopePriColor(p) {
  return p === "P1" ? "var(--red)" : p === "P2" ? "var(--amber)" : "var(--green)";
}
function scopePriInk(p) {
  return p === "P1" ? "var(--red-ink)" : p === "P2" ? "var(--amber-ink)" : "var(--green-ink)";
}
function scopePriSoft(p) {
  return p === "P1" ? "var(--red-soft)" : p === "P2" ? "var(--amber-soft)" : "var(--green-soft)";
}

function linkedMapsFor(obj, maps) {
  const ids = obj.linked_risks || (obj.linked_risk ? [obj.linked_risk] : []);
  return (maps || []).filter(m => ids.includes(m.linked_risk));
}

// Deterministic status from linked-MAP completion (or an explicit obj.status).
function scopeStatus(obj, maps) {
  if (obj.status) return obj.status;
  const lm = linkedMapsFor(obj, maps);
  if (!lm.length) return "planned";
  const pcts = lm.map(m => m.completion_pct || 0);
  const max = Math.max(...pcts);
  if (pcts.every(p => p >= 100)) return "closed";
  if (pcts.some(p => p >= 100)) return "review";
  if (max > 0) return "fieldwork";
  return "planned";
}

function ScopeCard({ obj, maps, risk }) {
  const lm = linkedMapsFor(obj, maps);
  const avg = lm.length ? Math.round(lm.reduce((s, m) => s + (m.completion_pct || 0), 0) / lm.length) : 0;
  return (
    <div className="scope-card" style={{borderLeftColor: scopePriColor(obj.priority)}}>
      <div className="scope-card-top">
        <span className="scope-pri" style={{background: scopePriSoft(obj.priority), color: scopePriInk(obj.priority)}}>{obj.priority}</span>
        <span className="mono scope-card-id">{obj.id}</span>
      </div>
      <div className="scope-card-title">{obj.objective}</div>
      <div className="scope-card-meta mono">
        {(obj.linked_risk || (obj.linked_risks || [])[0] || "—")}
        {risk ? ` · ${risk.name}` : ""}
        {" · "}{obj.hours || 0}h
      </div>
      {lm.length > 0 && (
        <div className="scope-card-bar"><div style={{width: `${avg}%`}} /></div>
      )}
    </div>
  );
}

// ---------- KANBAN ----------
function ScopeKanban({ objectives, maps, risks, sprintFilter }) {
  const filtered = sprintFilter === "all" ? objectives : objectives.filter(o => String(o.sprint || 1) === sprintFilter);
  const byStatus = SCOPE_COLUMNS.reduce((acc, c) => { acc[c.id] = []; return acc; }, {});
  filtered.forEach(o => { byStatus[scopeStatus(o, maps)].push(o); });

  return (
    <div className="scope-kanban">
      {SCOPE_COLUMNS.map(col => (
        <div className="scope-col" key={col.id}>
          <div className="scope-col-head">
            <span>{col.l}</span>
            <span className="mono scope-col-count">{byStatus[col.id].length}</span>
          </div>
          <div className="scope-col-body">
            {byStatus[col.id].length === 0
              ? <div className="scope-col-empty">—</div>
              : byStatus[col.id].map(o => (
                  <ScopeCard key={o.id} obj={o} maps={maps}
                    risk={(risks || []).find(r => r.id === (o.linked_risk || (o.linked_risks || [])[0]))} />
                ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------- GANTT ----------
function ScopeGantt({ objectives, maps, sprintFilter }) {
  const WEEKS_PER_SPRINT = 2;
  const maxSprint = Math.max(1, ...objectives.map(o => o.sprint || 1));
  const weeks = maxSprint * WEEKS_PER_SPRINT;

  const sprints = [];
  for (let s = 1; s <= maxSprint; s++) {
    if (sprintFilter !== "all" && String(s) !== sprintFilter) continue;
    const items = objectives.filter(o => (o.sprint || 1) === s);
    if (items.length) sprints.push({ sprint: s, items });
  }

  const cols = `minmax(180px, 220px) repeat(${weeks}, 1fr)`;

  return (
    <div className="scope-gantt">
      <div className="scope-gantt-row scope-gantt-head" style={{gridTemplateColumns: cols}}>
        <div className="scope-gantt-label mono">Objective</div>
        {Array.from({length: weeks}, (_, i) => (
          <div key={i} className="scope-gantt-wk mono">W{i + 1}</div>
        ))}
      </div>

      {sprints.map(({ sprint, items }) => (
        <React.Fragment key={sprint}>
          <div className="scope-gantt-sprint" style={{gridTemplateColumns: cols}}>
            <div className="scope-gantt-sprint-lbl">Fiscal Quarter {sprint}</div>
            <div style={{gridColumn: `2 / span ${weeks}`}} className="scope-gantt-sprint-rule" />
          </div>
          {items.map(o => {
            const start = (o.sprint - 1) * WEEKS_PER_SPRINT;
            const dur = Math.max(1, Math.min(weeks - start, Math.round((o.hours || 40) / 20)));
            const pct = scopeStatus(o, maps) === "closed" ? 100
              : (() => { const lm = linkedMapsFor(o, maps); return lm.length ? Math.round(lm.reduce((s,m)=>s+(m.completion_pct||0),0)/lm.length) : 0; })();
            return (
              <div className="scope-gantt-row" key={o.id} style={{gridTemplateColumns: cols}}>
                <div className="scope-gantt-label">
                  <span className="mono scope-gantt-id">{o.id}</span>
                  <span className="scope-gantt-name">{o.objective}</span>
                </div>
                <div className="scope-gantt-bar-cell" style={{gridColumn: `${start + 2} / span ${dur}`}}>
                  <div className="scope-gantt-bar" style={{background: scopePriSoft(o.priority), borderColor: scopePriColor(o.priority)}}>
                    <div className="scope-gantt-bar-fill" style={{width: `${pct}%`, background: scopePriColor(o.priority)}} />
                    <span className="scope-gantt-bar-lbl" style={{color: scopePriInk(o.priority)}}>{o.priority} · {o.hours || 0}h</span>
                  </div>
                </div>
              </div>
            );
          })}
        </React.Fragment>
      ))}
    </div>
  );
}

function AuditScopeScreen({ objectives, maps, risks, hasRun }) {
  const [view, setView] = useState("kanban");      // kanban | gantt
  const [sprintFilter, setSprintFilter] = useState("all");

  const objs = objectives || [];
  const sprintIds = [...new Set(objs.map(o => o.sprint || 1))].sort((a, b) => a - b);
  const totalHours = objs.reduce((s, o) => s + (o.hours || 0), 0);
  const byPri = objs.reduce((acc, o) => { acc[o.priority] = (acc[o.priority] || 0) + 1; return acc; }, {});

  return (
    <div className="scope-screen" data-screen-label="Audit Scope">
      <div className="panel-head">
        <div>
          <div className="kicker">Execution · Audit Plan</div>
          <div className="panel-title mt-8">Audit Scope</div>
          <div className="panel-sub">Risk-linked audit objectives, organized by fiscal quarter. Switch between board and timeline views.</div>
        </div>
        <div className="scope-toolbar">
          <select className="input scope-sprint-sel" value={sprintFilter} onChange={e => setSprintFilter(e.target.value)}>
            <option value="all">All fiscal quarters</option>
            {sprintIds.map(s => <option key={s} value={String(s)}>Fiscal Quarter {s}</option>)}
          </select>
          <div className="scope-view-toggle">
            <button className={"btn btn-sm" + (view === "gantt" ? " btn-primary" : " btn-ghost")} onClick={() => setView("gantt")}>
              <Icon name="list" size={11}/> Gantt
            </button>
            <button className={"btn btn-sm" + (view === "kanban" ? " btn-primary" : " btn-ghost")} onClick={() => setView("kanban")}>
              <Icon name="grid" size={11}/> Kanban
            </button>
          </div>
        </div>
      </div>

      {objs.length === 0 ? (
        <Empty>Audit objectives populate after Stage 3 of the loop. Run the loop from the Pipeline screen.</Empty>
      ) : (
        <>
          <div className="scope-stats">
            <div className="scope-stat"><div className="v">{objs.length}</div><div className="l">Objectives</div></div>
            <div className="scope-stat"><div className="v">{sprintIds.length}</div><div className="l">Fiscal Quarters</div></div>
            <div className="scope-stat"><div className="v">{totalHours}</div><div className="l">Planned hours</div></div>
            <div className="scope-stat"><div className="v" style={{color:"var(--red-ink)"}}>{byPri.P1 || 0}</div><div className="l">P1 items</div></div>
          </div>
          {view === "kanban"
            ? <ScopeKanban objectives={objs} maps={maps} risks={risks} sprintFilter={sprintFilter} />
            : <ScopeGantt objectives={objs} maps={maps} sprintFilter={sprintFilter} />}
        </>
      )}
    </div>
  );
}

Object.assign(window, { AuditScopeScreen });
