/* ============================================================
   Workflow Admin — org chart + role administration (Phase 2).
   Lets an admin set anyone's manager (routing for HITL gate
   adjustments), promote/demote admins, and activate/deactivate
   accounts. Self-service org-chart assignment (Header dropdown)
   and this screen write through the same auth_db.set_manager
   path — this just adds an admin override + a wider blast radius.
   ============================================================ */

function AdminUserRow({ u, users, isSelf, onSetManager, onSetRole, onSetActive }) {
  const [busy, setBusy] = React.useState(null); // 'manager' | 'role' | 'active' | null
  const [err, setErr] = React.useState(null);

  async function run(field, fn) {
    setBusy(field); setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  }

  const managerOptions = users.filter(o => o.id !== u.id && o.is_active);

  return (
    <tr>
      <td>
        <div style={{ fontWeight: 600, color: "var(--ink)" }}>{u.display_name || u.username}</div>
        <div className="mono" style={{ fontSize: 10, color: "var(--ink-4)" }}>@{u.username}</div>
      </td>
      <td>
        <select
          className="fi-input" style={{ fontSize: 11.5, minWidth: 100 }}
          value={u.role} disabled={busy === "role" || (isSelf && u.role === "admin")}
          onChange={e => run("role", () => onSetRole(u.id, e.target.value))}
        >
          <option value="user">User</option>
          <option value="admin">Admin</option>
        </select>
      </td>
      <td>
        <select
          className="fi-input" style={{ fontSize: 11.5, minWidth: 160 }}
          value={u.manager_id || ""} disabled={busy === "manager"}
          onChange={e => run("manager", () => onSetManager(u.id, e.target.value ? Number(e.target.value) : null))}
        >
          <option value="">— none set —</option>
          {managerOptions.map(o => (
            <option key={o.id} value={o.id}>{o.display_name || o.username}</option>
          ))}
        </select>
      </td>
      <td>
        <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: isSelf ? "default" : "pointer" }}>
          <input
            type="checkbox" checked={u.is_active} disabled={busy === "active" || isSelf}
            onChange={e => run("active", () => onSetActive(u.id, e.target.checked))}
          />
          <span style={{ fontSize: 11, color: u.is_active ? "var(--green-ink)" : "var(--ink-4)" }}>
            {u.is_active ? "Active" : "Inactive"}
          </span>
        </label>
      </td>
      <td style={{ maxWidth: 200 }}>
        {err && <span className="mono" style={{ fontSize: 10, color: "var(--red-ink)" }}>{err}</span>}
      </td>
    </tr>
  );
}

function AdminConfigScreen() {
  const auth = window.useAuth ? window.useAuth() : null;
  const isAdmin = auth?.user?.role === "admin";

  const [users, setUsers] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);

  const reload = React.useCallback(async () => {
    if (!isAdmin) return;
    setLoading(true); setError(null);
    try {
      const res = await fetch("/auth/admin/users", { credentials: "include" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setUsers(data.users || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

  React.useEffect(() => { reload(); }, [reload]);

  async function putAndReload(url, body) {
    const res = await fetch(url, {
      method: "PUT", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error((await res.text()) || "Request failed");
    const updated = await res.json();
    return updated;
  }

  async function handleSetManager(userId, managerId) {
    await putAndReload(`/auth/admin/users/${userId}/manager`, { manager_id: managerId });
    setUsers(prev => prev.map(u => u.id === userId ? { ...u, manager_id: managerId } : u));
  }

  async function handleSetRole(userId, role) {
    await putAndReload(`/auth/admin/users/${userId}/role`, { role });
    setUsers(prev => prev.map(u => u.id === userId ? { ...u, role } : u));
  }

  async function handleSetActive(userId, isActive) {
    await putAndReload(`/auth/admin/users/${userId}/active`, { is_active: isActive });
    setUsers(prev => prev.map(u => u.id === userId ? { ...u, is_active: isActive } : u));
  }

  if (!isAdmin) {
    return (
      <div className="scope-screen" data-screen-label="Workflow Admin">
        <Empty>Admin role required to view this screen.</Empty>
      </div>
    );
  }

  return (
    <div className="scope-screen" data-screen-label="Workflow Admin">
      <div className="panel-head">
        <div>
          <div className="kicker">Governance · Configuration</div>
          <div className="panel-title mt-8">Workflow Admin</div>
          <div className="panel-sub">
            Org chart and role administration for the Enterprise Risk and SOX approval workflow.
            The manager assigned here is who a user's HITL gate adjustments route to for review —
            it overrides whatever the user has set for themselves in their own account menu.
          </div>
        </div>
        <button className="btn btn-sm" onClick={reload} disabled={loading}>
          <Icon name="reset" size={11} /> Refresh
        </button>
      </div>

      {error && (
        <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", background: "var(--red-soft)", padding: "6px 10px", borderRadius: 4, marginBottom: 12 }}>{error}</div>
      )}

      {loading ? (
        <div style={{ padding: "24px 0", textAlign: "center", color: "var(--ink-3)", fontSize: 12 }}>Loading…</div>
      ) : users.length === 0 ? (
        <Empty>No accounts found.</Empty>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="rtable">
            <thead>
              <tr>
                <th>User</th>
                <th>Role</th>
                <th>Manager</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {users.map(u => (
                <AdminUserRow
                  key={u.id} u={u} users={users} isSelf={u.id === auth.user.id}
                  onSetManager={handleSetManager} onSetRole={handleSetRole} onSetActive={handleSetActive}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

Object.assign(window, { AdminConfigScreen });
