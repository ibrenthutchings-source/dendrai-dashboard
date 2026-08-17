/* ============================================================
   User Configuration — add / change / remove local accounts,
   plus a per-user Screen Access permission matrix, as two tabs
   of one admin screen.

   Users tab: passwords can be set manually (validated against the
   same complexity rules as self-service change-password) or system-
   generated. A generated or admin-set password is shown exactly
   once right after the request succeeds — it is never stored or
   retrievable in plaintext again, and the account is forced to
   change it at next login. All writes land in the same auth.users
   table as every other account (see auth_db.py / auth_endpoints.py).
   Role / manager / active-status toggles live inline in the table;
   Add/Edit modals cover profile fields and password reset.

   Screen Access tab: pick a user, then set Read/Edit per screen,
   grouped by nav section exactly as the nav renders it. Checking/
   unchecking a section's Read or Edit box toggles every screen in
   that section; each screen can also be toggled individually. Every
   non-admin user gets their own independent matrix — 'admin' accounts
   always have full access and aren't listed, so an admin can never
   lock every admin out (see auth_endpoints.py require_admin). Saved
   to auth.screen_permissions, keyed by user_id (auth_db.py). A screen
   with no saved row for that user is allowed by default, so screens
   added after this was last saved aren't silently hidden.
   ============================================================ */

const PW_RULES = [
  { re: /.{8,}/,        label: "8+ characters" },
  { re: /[A-Z]/,        label: "Uppercase" },
  { re: /[a-z]/,        label: "Lowercase" },
  { re: /\d/,           label: "Number" },
  { re: /[^A-Za-z0-9]/, label: "Special character" },
];

function PasswordStrengthRow({ pw }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 10px", marginTop: 6 }}>
      {PW_RULES.map(({ re, label }) => {
        const pass = re.test(pw);
        return (
          <span key={label} style={{ fontSize: 10, color: pass ? "var(--green-ink)" : "var(--ink-4)", display: "flex", alignItems: "center", gap: 3 }}>
            {pass ? "✓" : "○"} {label}
          </span>
        );
      })}
    </div>
  );
}

function PasswordModeFields({ mode, setMode, password, setPassword }) {
  return (
    <div className="ar-field" style={{ marginTop: 12 }}>
      <label className="ar-label">Password</label>
      <div style={{ display: "flex", gap: 14, marginBottom: 8 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11.5, cursor: "pointer" }}>
          <input type="radio" checked={mode === "generate"} onChange={() => { setMode("generate"); setPassword(""); }} />
          Generate automatically
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11.5, cursor: "pointer" }}>
          <input type="radio" checked={mode === "manual"} onChange={() => setMode("manual")} />
          Set manually
        </label>
      </div>
      {mode === "manual" && (
        <>
          <input type="password" className="fi-input" value={password} onChange={e => setPassword(e.target.value)}
            placeholder="Enter a password" autoComplete="new-password" />
          <PasswordStrengthRow pw={password} />
        </>
      )}
      {mode === "generate" && (
        <div style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
          A random password meeting all complexity rules will be generated and shown once after saving.
        </div>
      )}
      <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 6 }}>
        The account must change this password at next login.
      </div>
    </div>
  );
}

function GeneratedPasswordReveal({ username, password, onClose }) {
  const [copied, setCopied] = React.useState(false);
  useEscapeToClose(true, onClose);
  function copy() {
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(password).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    }).catch(() => {});
  }
  return (
    <div className="modal open">
      <div className="modal-box" style={{ width: 440 }}>
        <div className="modal-head">
          <div>
            <div className="modal-title">Password for @{username}</div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12} /></button>
        </div>
        <div className="modal-body">
          <div style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.55, marginBottom: 12 }}>
            This is shown once. Copy it now and share it with the user through a secure channel —
            Dendrai does not store or display it again. They'll be required to set their own password at first login.
          </div>
          <div className="mono" style={{
            fontSize: 15, letterSpacing: "0.03em", padding: "12px 14px", borderRadius: 6,
            background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)",
            userSelect: "all", wordBreak: "break-all",
          }}>
            {password}
          </div>
        </div>
        <div className="modal-foot">
          <span />
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn btn-sm" onClick={copy}>{copied ? "Copied!" : "Copy"}</button>
            <button className="btn btn-sm btn-primary" onClick={onClose}>Done</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function AddUserModal({ open, onClose, onCreated, roles = [] }) {
  const [username, setUsername] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [displayName, setDisplayName] = React.useState("");
  const [role, setRole] = React.useState("user");
  const [pwMode, setPwMode] = React.useState("generate");
  const [password, setPassword] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState(null);

  React.useEffect(() => {
    if (open) {
      setUsername(""); setEmail(""); setDisplayName(""); setRole("user");
      setPwMode("generate"); setPassword(""); setErr(null);
    }
  }, [open]);
  useEscapeToClose(open, onClose);

  if (!open) return null;

  const usernameValid = /^[a-z0-9._-]{3,64}$/.test(username.trim().toLowerCase());
  const pwValid = pwMode === "generate" || PW_RULES.every(r => r.re.test(password));
  const valid = usernameValid && pwValid;

  async function submit() {
    setBusy(true); setErr(null);
    try {
      const res = await fetch("/auth/admin/users", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: username.trim(), email: email.trim() || null, display_name: displayName.trim() || null,
          role, generate_password: pwMode === "generate", password: pwMode === "manual" ? password : null,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to create user");
      onCreated(data.user, data.generated_password);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal open">
      <div className="modal-box" style={{ width: 480 }}>
        <div className="modal-head">
          <div className="modal-title">Add User</div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12} /></button>
        </div>
        <div className="modal-body">
          <div className="ar-field">
            <label className="ar-label">Username</label>
            <input type="text" className="fi-input" value={username} onChange={e => setUsername(e.target.value)}
              placeholder="jsmith" autoFocus />
            {username && !usernameValid && (
              <div className="mono" style={{ fontSize: 10, color: "var(--red-ink)", marginTop: 3 }}>
                3-64 characters: lowercase letters, numbers, dot, underscore, hyphen.
              </div>
            )}
          </div>
          <div className="ar-field" style={{ marginTop: 10, display: "flex", gap: 12 }}>
            <div style={{ flex: 1 }}>
              <label className="ar-label">Display Name</label>
              <input type="text" className="fi-input" value={displayName} onChange={e => setDisplayName(e.target.value)} placeholder="Jane Smith" />
            </div>
            <div style={{ flex: 1 }}>
              <label className="ar-label">Email</label>
              <input type="email" className="fi-input" value={email} onChange={e => setEmail(e.target.value)} placeholder="jane@company.com" />
            </div>
          </div>
          <div className="ar-field" style={{ marginTop: 10 }}>
            <label className="ar-label">Role</label>
            <select className="fi-input" value={role} onChange={e => setRole(e.target.value)}>
              {(roles.length ? roles : [{ name: "user" }, { name: "admin" }]).map(r => (
                <option key={r.name} value={r.name}>{r.name}</option>
              ))}
            </select>
          </div>
          <PasswordModeFields mode={pwMode} setMode={setPwMode} password={password} setPassword={setPassword} />
          {err && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginTop: 10 }}>{err}</div>}
        </div>
        <div className="modal-foot">
          <span />
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn btn-sm" onClick={onClose}>Cancel</button>
            <button className="btn btn-sm btn-primary" disabled={!valid || busy} onClick={submit}>
              {busy ? "Creating…" : "Create User"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function EditUserModal({ open, user, onClose, onSaved, onPasswordSet }) {
  const [email, setEmail] = React.useState("");
  const [displayName, setDisplayName] = React.useState("");
  const [resetPw, setResetPw] = React.useState(false);
  const [pwMode, setPwMode] = React.useState("generate");
  const [password, setPassword] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState(null);

  React.useEffect(() => {
    if (open && user) {
      setEmail(user.email || ""); setDisplayName(user.display_name || "");
      setResetPw(false); setPwMode("generate"); setPassword(""); setErr(null);
    }
  }, [open, user?.id]);
  useEscapeToClose(open, onClose);

  if (!open || !user) return null;

  const pwValid = !resetPw || pwMode === "generate" || PW_RULES.every(r => r.re.test(password));

  async function submit() {
    setBusy(true); setErr(null);
    try {
      const profRes = await fetch(`/auth/admin/users/${user.id}`, {
        method: "PUT", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim() || null, display_name: displayName.trim() || null }),
      });
      const profData = await profRes.json();
      if (!profRes.ok) throw new Error(profData.detail || "Failed to update profile");

      let generatedPw = null;
      if (resetPw) {
        const pwRes = await fetch(`/auth/admin/users/${user.id}/password`, {
          method: "PUT", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ generate: pwMode === "generate", password: pwMode === "manual" ? password : null }),
        });
        const pwData = await pwRes.json();
        if (!pwRes.ok) throw new Error(pwData.detail || "Failed to reset password");
        generatedPw = pwData.generated_password;
      }

      onSaved(profData.user);
      if (generatedPw) onPasswordSet(user.username, generatedPw);
      onClose();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal open">
      <div className="modal-box" style={{ width: 480 }}>
        <div className="modal-head">
          <div>
            <div className="modal-title">Edit User</div>
            <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 3 }}>@{user.username}</div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}><Icon name="x" size={12} /></button>
        </div>
        <div className="modal-body">
          <div className="ar-field" style={{ display: "flex", gap: 12 }}>
            <div style={{ flex: 1 }}>
              <label className="ar-label">Display Name</label>
              <input type="text" className="fi-input" value={displayName} onChange={e => setDisplayName(e.target.value)} />
            </div>
            <div style={{ flex: 1 }}>
              <label className="ar-label">Email</label>
              <input type="email" className="fi-input" value={email} onChange={e => setEmail(e.target.value)} />
            </div>
          </div>
          <div className="ar-field" style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--line)" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11.5, cursor: "pointer" }}>
              <input type="checkbox" checked={resetPw} onChange={e => setResetPw(e.target.checked)} />
              Reset password
            </label>
          </div>
          {resetPw && (
            <PasswordModeFields mode={pwMode} setMode={setPwMode} password={password} setPassword={setPassword} />
          )}
          {err && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginTop: 10 }}>{err}</div>}
        </div>
        <div className="modal-foot">
          <span />
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn btn-sm" onClick={onClose}>Cancel</button>
            <button className="btn btn-sm btn-primary" disabled={!pwValid || busy} onClick={submit}>
              {busy ? "Saving…" : "Save Changes"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function UserConfigRow({ u, users, roles = [], isSelf, onSetManager, onSetRole, onSetActive, onEdit, onRemove }) {
  const [busy, setBusy] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [confirmingRemove, setConfirmingRemove] = React.useState(false);

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
        <div className="mono" style={{ fontSize: 10, color: "var(--ink-4)" }}>@{u.username}{u.email ? ` · ${u.email}` : ""}</div>
      </td>
      <td>
        <select
          className="fi-input" style={{ fontSize: 11.5, minWidth: 100 }}
          value={u.role} disabled={busy === "role" || (isSelf && u.role === "admin")}
          onChange={e => run("role", () => onSetRole(u.id, e.target.value))}
        >
          {(roles.length ? roles : [{ name: u.role }]).map(r => (
            <option key={r.name} value={r.name}>{r.name}</option>
          ))}
        </select>
      </td>
      <td>
        <select
          className="fi-input" style={{ fontSize: 11.5, minWidth: 150 }}
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
      <td>
        {confirmingRemove ? (
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10.5 }}>
            <span style={{ color: "var(--red-ink)" }}>Remove permanently?</span>
            <button className="btn btn-sm" style={{ color: "var(--red-ink)" }} onClick={() => onRemove(u.id)}>Yes</button>
            <button className="btn btn-sm" onClick={() => setConfirmingRemove(false)}>Cancel</button>
          </div>
        ) : (
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn btn-sm" onClick={() => onEdit(u)}><Icon name="edit" size={11} /> Edit</button>
            {!isSelf && (
              <button className="btn btn-sm" style={{ color: "var(--red-ink)" }} onClick={() => setConfirmingRemove(true)}>
                <Icon name="x" size={11} /> Remove
              </button>
            )}
          </div>
        )}
        {err && <div className="mono" style={{ fontSize: 10, color: "var(--red-ink)", marginTop: 4 }}>{err}</div>}
      </td>
    </tr>
  );
}

function UsersTab() {
  const auth = window.useAuth ? window.useAuth() : null;

  const [users, setUsers] = React.useState([]);
  const [roles, setRoles] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [addOpen, setAddOpen] = React.useState(false);
  const [editUser, setEditUser] = React.useState(null);
  const [reveal, setReveal] = React.useState(null); // { username, password }

  const reload = React.useCallback(async () => {
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
  }, []);

  React.useEffect(() => { reload(); }, [reload]);

  // Roles for the two role <select>s below (Add User modal + inline row
  // editor) — fetched once here rather than by each, same list either way.
  React.useEffect(() => {
    fetch("/auth/admin/roles", { credentials: "include" })
      .then(r => r.ok ? r.json() : { roles: [] })
      .then(d => setRoles(d.roles || []))
      .catch(() => {});
  }, []);

  async function putAndParse(url, body) {
    const res = await fetch(url, {
      method: "PUT", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Request failed");
    return data;
  }

  async function handleSetManager(userId, managerId) {
    await putAndParse(`/auth/admin/users/${userId}/manager`, { manager_id: managerId });
    setUsers(prev => prev.map(u => u.id === userId ? { ...u, manager_id: managerId } : u));
  }

  async function handleSetRole(userId, role) {
    await putAndParse(`/auth/admin/users/${userId}/role`, { role });
    setUsers(prev => prev.map(u => u.id === userId ? { ...u, role } : u));
  }

  async function handleSetActive(userId, isActive) {
    await putAndParse(`/auth/admin/users/${userId}/active`, { is_active: isActive });
    setUsers(prev => prev.map(u => u.id === userId ? { ...u, is_active: isActive } : u));
  }

  async function handleRemove(userId) {
    setError(null);
    try {
      const res = await fetch(`/auth/admin/users/${userId}`, { method: "DELETE", credentials: "include" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Failed to remove user");
      setUsers(prev => prev.filter(u => u.id !== userId));
    } catch (e) {
      setError(e.message);
    }
  }

  function handleCreated(user, generatedPassword) {
    setUsers(prev => [...prev, user].sort((a, b) => a.username.localeCompare(b.username)));
    setAddOpen(false);
    if (generatedPassword) setReveal({ username: user.username, password: generatedPassword });
  }

  function handleSaved(user) {
    setUsers(prev => prev.map(u => u.id === user.id ? user : u));
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginBottom: 14 }}>
        <button className="btn btn-sm" onClick={reload} disabled={loading}>
          <Icon name="reset" size={11} /> Refresh
        </button>
        <button className="btn btn-sm btn-primary" onClick={() => setAddOpen(true)}>
          <Icon name="plus" size={11} /> Add User
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
                <UserConfigRow
                  key={u.id} u={u} users={users} roles={roles} isSelf={u.id === auth.user.id}
                  onSetManager={handleSetManager} onSetRole={handleSetRole} onSetActive={handleSetActive}
                  onEdit={setEditUser} onRemove={handleRemove}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <AddUserModal open={addOpen} onClose={() => setAddOpen(false)} onCreated={handleCreated} roles={roles} />
      <EditUserModal
        open={!!editUser} user={editUser} onClose={() => setEditUser(null)}
        onSaved={handleSaved} onPasswordSet={(username, password) => setReveal({ username, password })}
      />
      {reveal && (
        <GeneratedPasswordReveal username={reveal.username} password={reveal.password} onClose={() => setReveal(null)} />
      )}
    </div>
  );
}

// ── Screen Access tab ────────────────────────────────────────────────────────

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

// Reusable Read/Edit matrix editor, pointed at either a per-user endpoint
// (/auth/admin/screen-permissions/{userId}) or a per-role endpoint
// (/auth/admin/roles/{roleId}/permissions) — both return/accept the same
// {permissions: [...]} shape. Pass a `key` that changes with the selected
// user/role so React remounts (and thus reloads) rather than needing an
// extra effect keyed off an external id.
function PermissionMatrixEditor({ permsUrl, disabled, disabledNote }) {
  const [perms, setPerms] = React.useState({});
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [saved, setSaved] = React.useState(false);

  const sections = React.useMemo(getPermissionSections, []);

  const load = React.useCallback(async () => {
    if (!permsUrl) { setPerms({}); setLoading(false); return; }
    setLoading(true); setError(null); setSaved(false);
    try {
      const res = await fetch(permsUrl, { credentials: "include" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setPerms(data.permissions || {});
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [permsUrl]);

  React.useEffect(() => { load(); }, [load]);

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
    if (!permsUrl) return;
    setSaving(true); setError(null); setSaved(false);
    try {
      const permissions = sections.flatMap(sec => sec.screens.map(s => ({
        screen_id: s.id,
        can_read: perms[s.id]?.can_read !== false,
        can_edit: perms[s.id]?.can_edit !== false,
      })));
      const res = await fetch(permsUrl, {
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

  if (disabled) {
    return <Empty>{disabledNote || "Not configurable."}</Empty>;
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginBottom: 10 }}>
        <button className="btn btn-sm" onClick={load} disabled={loading || saving}>
          <Icon name="reset" size={11} /> Revert
        </button>
        <button className="btn btn-sm btn-primary" onClick={save} disabled={loading || saving}>
          {saving ? "Saving…" : saved ? "Saved ✓" : "Save Changes"}
        </button>
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

function ScreenAccessTab() {
  const [users, setUsers] = React.useState([]);
  const [usersLoading, setUsersLoading] = React.useState(true);
  const [selectedUserId, setSelectedUserId] = React.useState(null);

  // Admins always have full access and aren't configurable here.
  React.useEffect(() => {
    (async () => {
      setUsersLoading(true);
      try {
        const res = await fetch("/auth/admin/users", { credentials: "include" });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        const nonAdmin = (data.users || []).filter(u => u.role !== "admin");
        setUsers(nonAdmin);
        setSelectedUserId(prev => prev ?? (nonAdmin[0]?.id ?? null));
      } catch (e) {
        // Surfaced inline below via the empty-state; no separate error UI needed here.
      } finally {
        setUsersLoading(false);
      }
    })();
  }, []);

  const selectedUser = users.find(u => u.id === selectedUserId);

  return (
    <div>
      <div style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.5, maxWidth: 760, marginBottom: 14 }}>
        Per-user overrides on top of the user's role default (see the Roles tab). Check a section's box to set
        every screen inside it, or set screens individually. Admins always have full access and aren't listed
        here. A screen left unconfigured here falls back to the role's default, and if the role has no default
        either, stays visible/editable — same as before roles existed.
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
        <label className="ar-label" style={{ marginBottom: 0 }}>User</label>
        <select className="fi-input" style={{ fontSize: 11.5, minWidth: 220 }}
          value={selectedUserId || ""} disabled={usersLoading || users.length === 0}
          onChange={e => setSelectedUserId(e.target.value ? Number(e.target.value) : null)}>
          {users.length === 0 && <option value="">— no non-admin users —</option>}
          {users.map(u => (
            <option key={u.id} value={u.id}>{u.display_name || u.username}{u.is_active ? "" : " (inactive)"}</option>
          ))}
        </select>
        {selectedUser && (
          <span className="mono" style={{ fontSize: 10, color: "var(--ink-4)" }}>
            role: <strong>{selectedUser.role}</strong>
          </span>
        )}
      </div>

      {!usersLoading && users.length === 0 ? (
        <Empty>No non-admin accounts yet — add one in the Users tab first.</Empty>
      ) : (
        <PermissionMatrixEditor
          key={selectedUserId}
          permsUrl={selectedUserId ? `/auth/admin/screen-permissions/${selectedUserId}` : null}
        />
      )}
    </div>
  );
}

// ── Roles tab ──────────────────────────────────────────────────────────────
// A role carries a default screen permission set (same matrix editor as the
// Screen Access tab, just pointed at a role instead of a user) — every user
// assigned to a role inherits its defaults, unless they have their own
// per-user override in the Screen Access tab.

function RolesTab() {
  const [roles, setRoles] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [selectedRoleId, setSelectedRoleId] = React.useState(null);
  const [error, setError] = React.useState(null);
  const [addOpen, setAddOpen] = React.useState(false);
  const [newName, setNewName] = React.useState("");
  const [newDesc, setNewDesc] = React.useState("");
  const [creating, setCreating] = React.useState(false);
  const [createErr, setCreateErr] = React.useState(null);
  const [deleteErr, setDeleteErr] = React.useState(null);
  useEscapeToClose(addOpen, () => setAddOpen(false));

  const loadRoles = React.useCallback(async (selectId) => {
    setLoading(true); setError(null);
    try {
      const res = await fetch("/auth/admin/roles", { credentials: "include" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      const list = data.roles || [];
      setRoles(list);
      setSelectedRoleId(prev => selectId ?? prev ?? (list[0]?.id ?? null));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { loadRoles(); }, [loadRoles]);

  const selectedRole = roles.find(r => r.id === selectedRoleId);

  async function createRole() {
    setCreating(true); setCreateErr(null);
    try {
      const res = await fetch("/auth/admin/roles", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName.trim(), description: newDesc.trim() || null }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Failed to create role");
      setAddOpen(false); setNewName(""); setNewDesc("");
      await loadRoles(data.role_id);
    } catch (e) {
      setCreateErr(e.message);
    } finally {
      setCreating(false);
    }
  }

  async function deleteRole(role) {
    if (!window.confirm(`Delete role "${role.name}"? This cannot be undone.`)) return;
    setDeleteErr(null);
    try {
      const res = await fetch(`/auth/admin/roles/${role.id}`, { method: "DELETE", credentials: "include" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Failed to delete role");
      if (selectedRoleId === role.id) setSelectedRoleId(null);
      await loadRoles();
    } catch (e) {
      setDeleteErr(e.message);
    }
  }

  return (
    <div style={{ display: "flex", gap: 24 }}>
      <div style={{ width: 220, flexShrink: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <div className="ar-label" style={{ marginBottom: 0 }}>Roles</div>
          <button className="btn btn-sm" onClick={() => setAddOpen(true)}>+ Add</button>
        </div>
        {loading ? (
          <div style={{ fontSize: 11, color: "var(--ink-3)" }}>Loading…</div>
        ) : (
          roles.map(r => (
            <div key={r.id}
              onClick={() => setSelectedRoleId(r.id)}
              style={{
                display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6,
                padding: "7px 10px", borderRadius: 6, cursor: "pointer", marginBottom: 3,
                background: selectedRoleId === r.id ? "var(--surface-2, var(--surface))" : "transparent",
                border: "1px solid " + (selectedRoleId === r.id ? "var(--line)" : "transparent"),
              }}>
              <div>
                <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ink)", display: "flex", alignItems: "center", gap: 5 }}>
                  {r.name}
                  {r.is_system && (
                    <span className="mono" style={{
                      fontSize: 8.5, fontWeight: 700, padding: "1px 5px", borderRadius: 999,
                      background: "var(--surface-2)", color: "var(--ink-3)", border: "1px solid var(--line)",
                    }}>system</span>
                  )}
                </div>
                <div style={{ fontSize: 9.5, color: "var(--ink-4)" }}>{r.user_count} user{r.user_count !== 1 ? "s" : ""}</div>
              </div>
              {!r.is_system && (
                <button className="btn btn-sm btn-ghost" title="Delete role"
                  onClick={e => { e.stopPropagation(); deleteRole(r); }}>
                  <Icon name="x" size={11} />
                </button>
              )}
            </div>
          ))
        )}
        {deleteErr && <div className="mono" style={{ fontSize: 10, color: "var(--red-ink)", marginTop: 6 }}>{deleteErr}</div>}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.5, maxWidth: 620, marginBottom: 14 }}>
          Each role's Read/Edit matrix is the default every assigned user inherits — set it once here instead of
          configuring users one by one. A user's own Screen Access overrides (previous tab) still win over
          their role's default when both are set for the same screen.
        </div>
        {error && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginBottom: 12 }}>{error}</div>}
        {!selectedRole ? <Empty>Select a role, or add a new one.</Empty> : (
          <>
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>{selectedRole.name}</div>
              {selectedRole.description && <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}>{selectedRole.description}</div>}
            </div>
            <PermissionMatrixEditor
              key={selectedRole.id}
              permsUrl={`/auth/admin/roles/${selectedRole.id}/permissions`}
              disabled={selectedRole.name === "admin"}
              disabledNote="Admins always have full access — this role's permission matrix has no effect and isn't configurable."
            />
          </>
        )}
      </div>

      {addOpen && (
        <div className="modal open">
          <div className="modal-box" style={{ width: 400 }}>
            <div className="modal-head">
              <div className="modal-title">Add Role</div>
              <button className="btn btn-sm btn-ghost" onClick={() => setAddOpen(false)}><Icon name="x" size={12} /></button>
            </div>
            <div className="modal-body">
              <div className="ar-field">
                <label className="ar-label">Name</label>
                <input type="text" className="fi-input" value={newName} onChange={e => setNewName(e.target.value)}
                  placeholder="auditor" autoFocus />
                <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", marginTop: 3 }}>
                  Lowercase letters, numbers, underscore, hyphen. 2-32 characters.
                </div>
              </div>
              <div className="ar-field" style={{ marginTop: 10 }}>
                <label className="ar-label">Description</label>
                <input type="text" className="fi-input" value={newDesc} onChange={e => setNewDesc(e.target.value)} placeholder="Optional" />
              </div>
              {createErr && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginTop: 10 }}>{createErr}</div>}
            </div>
            <div className="modal-foot">
              <span />
              <div style={{ display: "flex", gap: 6 }}>
                <button className="btn btn-sm" onClick={() => setAddOpen(false)}>Cancel</button>
                <button className="btn btn-sm btn-primary" disabled={!newName.trim() || creating} onClick={createRole}>
                  {creating ? "Creating…" : "Create Role"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Screen shell (tab switcher) ───────────────────────────────────────────────

function UserConfigScreen() {
  const auth = window.useAuth ? window.useAuth() : null;
  const isAdmin = auth?.user?.role === "admin";
  const [tab, setTab] = React.useState("users");

  if (!isAdmin) {
    return (
      <div className="scope-screen" data-screen-label="User Configuration">
        <Empty>Admin role required to view this screen.</Empty>
      </div>
    );
  }

  const tabs = [
    { id: "users", label: "Users" },
    { id: "roles", label: "Roles" },
    { id: "access", label: "Screen Access" },
  ];

  return (
    <div className="scope-screen" data-screen-label="User Configuration">
      <div className="panel-head">
        <div>
          <div className="kicker">Governance · Configuration</div>
          <div className="panel-title mt-8">User Configuration</div>
          <div className="panel-sub">
            Add, change, or permanently remove local accounts; define roles with default screen permissions; and set per-user overrides.
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--line)", marginBottom: 16 }}>
        {tabs.map(t => (
          <button key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              padding: "8px 16px", fontSize: 11, fontWeight: tab === t.id ? 600 : 400,
              background: "none", border: "none", borderBottom: tab === t.id ? "2px solid var(--acc-ink, var(--ink))" : "2px solid transparent",
              color: tab === t.id ? "var(--ink)" : "var(--ink-4)", cursor: "pointer",
            }}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "users" ? <UsersTab /> : tab === "roles" ? <RolesTab /> : <ScreenAccessTab />}
    </div>
  );
}

Object.assign(window, { UserConfigScreen });
