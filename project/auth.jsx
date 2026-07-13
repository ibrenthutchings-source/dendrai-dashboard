/* ============================================================
   auth.jsx — Authentication context, login screen, password-change screen.
   Exports AuthProvider and useAuth to window for use in main.jsx.
   ============================================================ */

const { useState, useEffect, useContext, createContext, useCallback } = React;

// ── Context ───────────────────────────────────────────────────────────────────

const AuthContext = createContext(null);

function useAuth() {
  return useContext(AuthContext);
}

// ── AuthProvider ──────────────────────────────────────────────────────────────

function AuthProvider({ children }) {
  const [user,      setUser]      = useState(undefined); // undefined = loading
  const [providers, setProviders] = useState([]);

  const loadSession = useCallback(() => {
    fetch("/auth/me", { credentials: "include" })
      .then(r => (r.ok ? r.json() : null))
      .then(d  => setUser(d?.user || null))
      .catch(() => setUser(null));
  }, []);

  useEffect(() => {
    loadSession();
    fetch("/auth/sso/providers", { credentials: "include" })
      .then(r => (r.ok ? r.json() : { providers: [] }))
      .then(d  => setProviders(d.providers || []))
      .catch(() => setProviders([]));
  }, [loadSession]);

  const logout = useCallback(() => {
    fetch("/auth/logout", { method: "POST", credentials: "include" })
      .finally(() => setUser(null));
  }, []);

  if (user === undefined) {
    return (
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "center",
        height: "100vh", background: "var(--bg, #f8f8f8)",
      }}>
        <div style={{ fontSize: 12, color: "#888", letterSpacing: "0.05em" }}>
          Authenticating…
        </div>
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ user, setUser, logout, providers, loadSession }}>
      {user
        ? (user.must_change_pw
            ? <ChangePasswordScreen onDone={() => setUser({ ...user, must_change_pw: false })} />
            : children)
        : <LoginScreen />}
    </AuthContext.Provider>
  );
}

// ── Shared field component ────────────────────────────────────────────────────

function AuthField({ label, type = "text", value, onChange, placeholder, autoFocus, disabled }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <label style={{ fontSize: 10.5, fontWeight: 600, color: "var(--ink-3, #888)", letterSpacing: "0.04em" }}>
        {label}
      </label>
      <input
        type={type} value={value} onChange={e => onChange(e.target.value)}
        placeholder={placeholder} autoFocus={autoFocus} disabled={disabled}
        style={{
          padding: "9px 12px", fontSize: 13, border: "1px solid var(--line, #e0e0e0)",
          borderRadius: 8, background: "var(--surface, #fff)", color: "var(--ink, #111)",
          outline: "none", width: "100%", boxSizing: "border-box",
          fontFamily: "inherit", transition: "border-color 0.15s",
        }}
        onFocus={e  => { e.target.style.borderColor = "var(--acc, #6366f1)"; }}
        onBlur={e   => { e.target.style.borderColor = "var(--line, #e0e0e0)"; }}
      />
    </div>
  );
}

// ── Login Screen ──────────────────────────────────────────────────────────────

function LoginScreen() {
  const { setUser, providers } = useContext(AuthContext);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error,    setError]    = useState("");
  const [loading,  setLoading]  = useState(false);

  async function handleLogin(e) {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setLoading(true); setError("");
    try {
      const r = await fetch("/auth/login", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      const d = await r.json();
      if (!r.ok) { setError(d.detail || "Invalid credentials"); return; }
      setUser(d.user);
    } catch {
      setError("Unable to reach the server. Check your connection.");
    } finally {
      setLoading(false);
    }
  }

  const hasSso = providers.length > 0;

  const SSO_ICONS = {
    microsoft: (
      <svg width={16} height={16} viewBox="0 0 21 21" fill="none">
        <rect x="1"  y="1"  width="9" height="9" fill="#f25022"/>
        <rect x="11" y="1"  width="9" height="9" fill="#7fba00"/>
        <rect x="1"  y="11" width="9" height="9" fill="#00a4ef"/>
        <rect x="11" y="11" width="9" height="9" fill="#ffb900"/>
      </svg>
    ),
    google: (
      <svg width={16} height={16} viewBox="0 0 24 24">
        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
      </svg>
    ),
    github: (
      <svg width={16} height={16} viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
      </svg>
    ),
    okta: (
      <svg width={16} height={16} viewBox="0 0 24 24" fill="#007DC1">
        <circle cx="12" cy="12" r="4.5"/>
        <path d="M12 0C5.37 0 0 5.37 0 12s5.37 12 12 12 12-5.37 12-12S18.63 0 12 0zm0 18c-3.31 0-6-2.69-6-6s2.69-6 6-6 6 2.69 6 6-2.69 6-6 6z"/>
      </svg>
    ),
  };

  return (
    <div className="auth-shell">
      <div className="auth-card">
        {/* Logo / brand */}
        <div className="auth-brand">
          <div className="auth-logo">
            <svg width={28} height={28} viewBox="0 0 32 32" fill="none">
              <rect width={32} height={32} rx={8} fill="var(--acc, #6366f1)"/>
              <circle cx={16} cy={11} r={4} fill="#fff" opacity={0.9}/>
              <circle cx={9}  cy={22} r={3} fill="#fff" opacity={0.7}/>
              <circle cx={23} cy={22} r={3} fill="#fff" opacity={0.7}/>
              <line x1={16} y1={15} x2={9}  y2={19} stroke="#fff" strokeWidth={1.5} opacity={0.6}/>
              <line x1={16} y1={15} x2={23} y2={19} stroke="#fff" strokeWidth={1.5} opacity={0.6}/>
              <line x1={9}  y1={22} x2={23} y2={22} stroke="#fff" strokeWidth={1}   opacity={0.4}/>
            </svg>
          </div>
          <div>
            <div className="auth-brand-name">Dendrai Intelligenza™</div>
          </div>
        </div>

        <h2 className="auth-heading">Sign in to your account</h2>

        {/* Local login form */}
        <form onSubmit={handleLogin} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <AuthField
            label="Username" value={username} onChange={setUsername}
            placeholder="Enter your username" autoFocus disabled={loading}
          />
          <AuthField
            label="Password" type="password" value={password} onChange={setPassword}
            placeholder="Enter your password" disabled={loading}
          />
          {error && (
            <div className="auth-error" role="alert">{error}</div>
          )}
          <button type="submit" className="auth-btn-primary" disabled={loading || !username.trim() || !password}>
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>

        {/* SSO section */}
        {hasSso && (
          <>
            <div className="auth-divider">
              <span>or continue with</span>
            </div>
            <div className="auth-sso-grid">
              {providers.map(p => (
                <a key={p.key} href={`/auth/sso/${p.key}/start`} className="auth-sso-btn">
                  {SSO_ICONS[p.key] || null}
                  {p.label}
                </a>
              ))}
            </div>
          </>
        )}

        <p className="auth-footer">
          Protected by Dendrai Auth · JWT + HTTP-only cookies
        </p>
      </div>
    </div>
  );
}

// ── Change Password Screen (shown on must_change_pw = true) ──────────────────

function ChangePasswordScreen({ onDone }) {
  const { user, logout } = useContext(AuthContext);
  const [cur,    setCur]    = useState("");
  const [next,   setNext]   = useState("");
  const [confirm,setConfirm]= useState("");
  const [error,  setError]  = useState("");
  const [ok,     setOk]     = useState(false);
  const [loading,setLoading]= useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    if (next !== confirm) { setError("Passwords do not match."); return; }
    setLoading(true); setError("");
    try {
      const r = await fetch("/auth/change-password", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: cur, new_password: next }),
      });
      const d = await r.json();
      if (!r.ok) { setError(d.detail || "Password change failed."); return; }
      setOk(true);
      setTimeout(() => logout(), 2000);
    } catch {
      setError("Server error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  const PW_RULES = [
    { re: /.{8,}/,        label: "At least 8 characters" },
    { re: /[A-Z]/,        label: "One uppercase letter" },
    { re: /[a-z]/,        label: "One lowercase letter" },
    { re: /\d/,           label: "One number" },
    { re: /[^A-Za-z0-9]/, label: "One special character" },
  ];

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <div className="auth-brand">
          <div className="auth-logo">
            <svg width={28} height={28} viewBox="0 0 32 32" fill="none">
              <rect width={32} height={32} rx={8} fill="var(--acc, #6366f1)"/>
              <circle cx={16} cy={11} r={4} fill="#fff" opacity={0.9}/>
              <circle cx={9}  cy={22} r={3} fill="#fff" opacity={0.7}/>
              <circle cx={23} cy={22} r={3} fill="#fff" opacity={0.7}/>
              <line x1={16} y1={15} x2={9}  y2={19} stroke="#fff" strokeWidth={1.5} opacity={0.6}/>
              <line x1={16} y1={15} x2={23} y2={19} stroke="#fff" strokeWidth={1.5} opacity={0.6}/>
            </svg>
          </div>
          <div>
            <div className="auth-brand-name">Password Change Required</div>
            <div className="auth-brand-sub">Signed in as {user?.username}</div>
          </div>
        </div>

        <p style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 16, lineHeight: 1.6 }}>
          Your account requires a new password before you can continue.
          Choose a strong password — it cannot match your last 3 passwords.
        </p>

        {ok ? (
          <div style={{ textAlign: "center", padding: "20px 0", color: "var(--acc)", fontWeight: 600, fontSize: 13 }}>
            ✓ Password updated. Signing you out…
          </div>
        ) : (
          <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <AuthField label="Current Password" type="password" value={cur} onChange={setCur}
              placeholder="Your current password" autoFocus disabled={loading} />
            <AuthField label="New Password" type="password" value={next} onChange={setNext}
              placeholder="New password" disabled={loading} />
            <AuthField label="Confirm New Password" type="password" value={confirm} onChange={setConfirm}
              placeholder="Repeat new password" disabled={loading} />

            {/* Password rules checklist */}
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 12px", marginTop: 2 }}>
              {PW_RULES.map(({ re, label }) => {
                const pass = re.test(next);
                return (
                  <span key={label} style={{
                    fontSize: 10, color: pass ? "#10b981" : "var(--ink-3)",
                    display: "flex", alignItems: "center", gap: 3,
                  }}>
                    {pass ? "✓" : "○"} {label}
                  </span>
                );
              })}
            </div>

            {error && <div className="auth-error" role="alert">{error}</div>}

            <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
              <button type="button" className="auth-btn-secondary" onClick={logout} disabled={loading}>
                Cancel
              </button>
              <button type="submit" className="auth-btn-primary" style={{ flex: 1 }}
                disabled={loading || !cur || !next || !confirm}>
                {loading ? "Updating…" : "Set New Password"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { AuthProvider, useAuth, LoginScreen, ChangePasswordScreen });
