/* ============================================================
   Screen Access — per-role Read/Edit permission matrix.
   Lists every screen in the left nav, grouped by section exactly
   as the nav renders it. Checking/unchecking a section's Read or
   Edit box toggles every screen in that section; each screen can
   also be toggled individually. Applies to the 'user' role only —
   'admin' always has full access so an admin can never lock every
   admin out (see auth_endpoints.py require_admin / admin routes).
   Saved to auth.screen_permissions (auth_db.py). A screen with no
   saved row is allowed by default, so screens added after this was
   last saved aren't silently hidden from existing accounts.
   ============================================================ */

const CONFIGURABLE_ROLE = "user";

// Mirrors LeftNav's rendering: one row per unique screen id per section,
// admin-only nav entries excluded (they're gated by role, not this matrix),
// and multi-tab screens (e.g. Governance Intelligence's 5 govTab entries,
// all id="gov") collapsed to a single row representing the whole screen.
function getPermissionSections() {
  const navSections = window.NAV_SECTIONS || [];
  return navSections
    .map(section => {
      const seen = new Set();
      const screens = [];
      section.items.forEach(item => {
        if (item.adminOnly || seen.has(item.id)) return;
        seen.add(item.id);
        screens.push({ id: item.id, label: item.govTab ? section.label : item.l, icon: item.icon });
      });
      return { label: section.label, screens };
    })
    .filter(s => s.screens.length > 0);
}

function SectionHeaderCheckbox({ checked, indeterminate, onChange, title }) {
  const ref = React.useRef(null);
  React.useEffect(() => { if (ref.current) ref.current.indeterminate = indeterminate; }, [indeterminate]);
  return <input ref={ref} type="checkbox" checked={checked} onChange={onChange} title={title} />;
}

function ScreenAccessSection({ section, perms, onToggleScreen, onToggleColumn }) {
  const readStates = section.screens.map(s => perms[s.id]?.can_read !== false);
  const editStates = section.screens.map(s => perms[s.id]?.can_edit !== false);
  const readAll = readStates.every(Boolean), readNone = readStates.every(v => !v);
  const editAll = editStates.every(Boolean), editNone = editStates.every(v => !v);

  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 14, padding: "8px 10px",
        background: "var(--surface-2, var(--surface))", borderRadius: "6px 6px 0 0", border: "1px solid var(--line)",
      }}>
        <div style={{ flex: 1, fontSize: 11.5, fontWeight: 600, color: "var(--ink)" }}>{section.label}</div>
        <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 10, color: "var(--ink-3)", cursor: "pointer", width: 60, justifyContent: "center" }}>
          <SectionHeaderCheckbox checked={readAll} indeterminate={!readAll && !readNone}
            onChange={() => onToggleColumn(section, "can_read", !readAll)} title="Toggle Read for every screen in this section" />
          Read
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 10, color: "var(--ink-3)", cursor: "pointer", width: 60, justifyContent: "center" }}>
          <SectionHeaderCheckbox checked={editAll} indeterminate={!editAll && !editNone}
            onChange={() => onToggleColumn(section, "can_edit", !editAll)} title="Toggle Edit for every screen in this section" />
          Edit
        </label>
      </div>
      <div style={{ border: "1px solid var(--line)", borderTop: "none", borderRadius: "0 0 6px 6px" }}>
        {section.screens.map((s, i) => {
          const p = perms[s.id] || { can_read: true, can_edit: true };
          return (
            <div key={s.id} style={{
              display: "flex", alignItems: "center", gap: 14, padding: "7px 10px",
              borderTop: i > 0 ? "1px solid var(--line)" : "none",
            }}>
              <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 7, fontSize: 11.5, color: "var(--ink-2)" }}>
                <Icon name={s.icon} size={12} className="muted" /> {s.label}
              </div>
              <div style={{ width: 60, textAlign: "center" }}>
                <input type="checkbox" checked={p.can_read !== false}
                  onChange={e => onToggleScreen(s.id, "can_read", e.target.checked)} />
              </div>
              <div style={{ width: 60, textAlign: "center" }}>
                <input type="checkbox" checked={p.can_edit !== false}
                  disabled={p.can_read === false}
                  onChange={e => onToggleScreen(s.id, "can_edit", e.target.checked)} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AdminConfigScreen() {
  const auth = window.useAuth ? window.useAuth() : null;
  const isAdmin = auth?.user?.role === "admin";

  const [perms, setPerms] = React.useState({});   // { screen_id: { can_read, can_edit } }
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [saved, setSaved] = React.useState(false);

  const sections = React.useMemo(getPermissionSections, []);

  const reload = React.useCallback(async () => {
    if (!isAdmin) return;
    setLoading(true); setError(null); setSaved(false);
    try {
      const res = await fetch(`/auth/admin/screen-permissions/${CONFIGURABLE_ROLE}`, { credentials: "include" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setPerms(data.permissions || {});
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

  React.useEffect(() => { reload(); }, [reload]);

  function toggleScreen(screenId, field, value) {
    setSaved(false);
    setPerms(prev => {
      const cur = prev[screenId] || { can_read: true, can_edit: true };
      const next = { ...cur, [field]: value };
      // Read off implies Edit off — can't edit a screen you can't see.
      if (field === "can_read" && !value) next.can_edit = false;
      return { ...prev, [screenId]: next };
    });
  }

  function toggleColumn(section, field, value) {
    setSaved(false);
    setPerms(prev => {
      const next = { ...prev };
      section.screens.forEach(s => {
        const cur = next[s.id] || { can_read: true, can_edit: true };
        const updated = { ...cur, [field]: value };
        if (field === "can_read" && !value) updated.can_edit = false;
        next[s.id] = updated;
      });
      return next;
    });
  }

  async function save() {
    setSaving(true); setError(null); setSaved(false);
    try {
      const permissions = sections.flatMap(sec => sec.screens.map(s => ({
        screen_id: s.id,
        can_read: perms[s.id]?.can_read !== false,
        can_edit: perms[s.id]?.can_edit !== false,
      })));
      const res = await fetch(`/auth/admin/screen-permissions/${CONFIGURABLE_ROLE}`, {
        method: "PUT", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ permissions }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Failed to save");
      setPerms(data.permissions || {});
      setSaved(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  if (!isAdmin) {
    return (
      <div className="scope-screen" data-screen-label="Screen Access">
        <Empty>Admin role required to view this screen.</Empty>
      </div>
    );
  }

  return (
    <div className="scope-screen" data-screen-label="Screen Access">
      <div className="panel-head">
        <div>
          <div className="kicker">Governance · Configuration</div>
          <div className="panel-title mt-8">Screen Access</div>
          <div className="panel-sub">
            Read and Edit access per screen for the <b>User</b> role, grouped by nav section — check a section's
            box to set every screen inside it, or set screens individually. Admins always have full access.
            A screen left unconfigured stays visible/editable by default.
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-sm" onClick={reload} disabled={loading || saving}>
            <Icon name="reset" size={11} /> Revert
          </button>
          <button className="btn btn-sm btn-primary" onClick={save} disabled={loading || saving}>
            {saving ? "Saving…" : saved ? "Saved ✓" : "Save Changes"}
          </button>
        </div>
      </div>

      {error && (
        <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", background: "var(--red-soft)", padding: "6px 10px", borderRadius: 4, marginBottom: 12 }}>{error}</div>
      )}

      {loading ? (
        <div style={{ padding: "24px 0", textAlign: "center", color: "var(--ink-3)", fontSize: 12 }}>Loading…</div>
      ) : (
        <div>
          {sections.map(section => (
            <ScreenAccessSection
              key={section.label} section={section} perms={perms}
              onToggleScreen={toggleScreen} onToggleColumn={toggleColumn}
            />
          ))}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { AdminConfigScreen });
