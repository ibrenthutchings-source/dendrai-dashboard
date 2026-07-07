#!/usr/bin/env python3
"""
auth_endpoints.py — FastAPI authentication router.

Routes
------
POST /auth/login                   Local username + password
POST /auth/logout                  Revoke session cookie
GET  /auth/me                      Current user info
POST /auth/change-password         Change own password (with history check)
GET  /auth/sso/providers           List enabled SSO providers
GET  /auth/sso/{provider}/start    Redirect to provider OAuth screen
GET  /auth/sso/{provider}/callback Handle OAuth callback + JIT provisioning
GET  /auth/users                   List active accounts (manager picker)
PUT  /auth/users/me/manager        Self-service manager assignment
GET  /auth/admin/users             List all accounts, admin only
PUT  /auth/admin/users/{id}/manager  Set any user's manager, admin only
PUT  /auth/admin/users/{id}/role     Promote/demote a user, admin only
PUT  /auth/admin/users/{id}/active   Activate/deactivate a user, admin only

Dependencies (pip install):
    passlib[bcrypt]     password hashing
    PyJWT               JWT encode/decode
    httpx               async HTTP for SSO token exchange
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import time
import uuid
from base64 import urlsafe_b64encode
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

import auth_db

logger = logging.getLogger("auth.endpoints")

router = APIRouter(prefix="/auth", tags=["Authentication"])

# ── Optional deps — degrade gracefully ───────────────────────────────────────

try:
    from passlib.context import CryptContext
    _pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    _HAS_PASSLIB = True
except ImportError:
    _pwd_ctx = None  # type: ignore[assignment]
    _HAS_PASSLIB = False
    logger.warning("passlib not installed — local login disabled. pip install passlib[bcrypt]")

try:
    import jwt as _pyjwt
    _HAS_JWT = True
except ImportError:
    _pyjwt = None  # type: ignore[assignment]
    _HAS_JWT = False
    logger.warning("PyJWT not installed — auth disabled. pip install PyJWT")

# ── JWT / cookie config ───────────────────────────────────────────────────────

_JWT_SECRET    = os.environ.get("AUTH_JWT_SECRET") or secrets.token_hex(32)
_JWT_ALGORITHM = "HS256"
_COOKIE_NAME   = "dendrai_session"
_SESSION_HOURS = int(os.environ.get("AUTH_SESSION_TTL_HOURS", "24"))
_COOKIE_SECURE = os.environ.get("AUTH_COOKIE_SECURE", "true").lower() != "false"

# ── Rate limiting (in-memory, per client IP) ──────────────────────────────────

_RL_WINDOW_S  = 900   # 15 minutes
_RL_MAX_TRIES = 5
_rl_store: dict[str, deque] = defaultdict(deque)


def _rate_check(ip: str) -> bool:
    """Return True if the request is allowed; False if rate-limited."""
    now = time.monotonic()
    dq  = _rl_store[ip]
    while dq and now - dq[0] > _RL_WINDOW_S:
        dq.popleft()
    if len(dq) >= _RL_MAX_TRIES:
        return False
    dq.append(now)
    return True


def _rate_clear(ip: str) -> None:
    _rl_store.pop(ip, None)


# ── Password complexity ───────────────────────────────────────────────────────

def _validate_password(pw: str) -> Optional[str]:
    if len(pw) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Z]", pw):
        return "Password must include at least one uppercase letter."
    if not re.search(r"[a-z]", pw):
        return "Password must include at least one lowercase letter."
    if not re.search(r"\d", pw):
        return "Password must include at least one number."
    if not re.search(r"[!@#$%^&*()\-_=+\[\]{};:'\",.<>/?\\|`~]", pw):
        return "Password must include at least one special character."
    return None


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _create_jwt(user: dict, jti: str) -> str:
    if not _HAS_JWT:
        raise HTTPException(status_code=503, detail="PyJWT not installed")
    payload = {
        "sub":      str(user["id"]),
        "username": user["username"],
        "role":     user["role"],
        "jti":      jti,
        "iat":      datetime.now(timezone.utc),
        "exp":      datetime.now(timezone.utc) + timedelta(hours=_SESSION_HOURS),
    }
    return _pyjwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def decode_jwt(token: str) -> Optional[dict]:
    if not _HAS_JWT or not token:
        return None
    try:
        return _pyjwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
    except Exception:
        return None


# ── Cookie helpers ────────────────────────────────────────────────────────────

def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_COOKIE_NAME, value=token,
        httponly=True, secure=_COOKIE_SECURE,
        samesite="strict", max_age=_SESSION_HOURS * 3600, path="/",
    )


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(key=_COOKIE_NAME, path="/")


# ── FastAPI auth dependency ───────────────────────────────────────────────────

def get_current_user(
    request: Request,
    dendrai_session: Optional[str] = Cookie(default=None, alias=_COOKIE_NAME),
) -> dict:
    """Raise 401 if cookie is missing, expired, or revoked."""
    if not dendrai_session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_jwt(dendrai_session)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid session token")
    jti = payload.get("jti", "")
    user_id = auth_db.validate_session(jti)
    if not user_id:
        raise HTTPException(status_code=401, detail="Session expired or revoked")
    user = auth_db.get_user_by_id(user_id)
    if not user or not user.get("is_active"):
        raise HTTPException(status_code=401, detail="Account inactive")
    return user


def get_optional_user(
    request: Request,
    dendrai_session: Optional[str] = Cookie(default=None, alias=_COOKIE_NAME),
) -> Optional[dict]:
    try:
        return get_current_user(request, dendrai_session)
    except HTTPException:
        return None


# ── SSO provider registry ──────────────────────────────────────────────────────

def _sso_providers() -> dict[str, dict]:
    """Build provider configs from env vars. Only includes configured providers."""
    p: dict[str, dict] = {}

    if os.environ.get("AZURE_CLIENT_ID"):
        tenant = os.environ.get("AZURE_TENANT_ID", "common")
        p["microsoft"] = {
            "label":         "Microsoft",
            "client_id":     os.environ["AZURE_CLIENT_ID"],
            "client_secret": os.environ.get("AZURE_CLIENT_SECRET", ""),
            "discovery_url": f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration",
            "scopes":        "openid email profile",
        }

    if os.environ.get("GOOGLE_CLIENT_ID"):
        p["google"] = {
            "label":         "Google",
            "client_id":     os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "discovery_url": "https://accounts.google.com/.well-known/openid-configuration",
            "scopes":        "openid email profile",
        }

    if os.environ.get("GITHUB_CLIENT_ID"):
        p["github"] = {
            "label":         "GitHub",
            "client_id":     os.environ["GITHUB_CLIENT_ID"],
            "client_secret": os.environ.get("GITHUB_CLIENT_SECRET", ""),
            "auth_url":      "https://github.com/login/oauth/authorize",
            "token_url":     "https://github.com/login/oauth/access_token",
            "scopes":        "read:user user:email",
        }

    if os.environ.get("OKTA_CLIENT_ID"):
        domain = os.environ.get("OKTA_DOMAIN", "")
        p["okta"] = {
            "label":         "Okta",
            "client_id":     os.environ["OKTA_CLIENT_ID"],
            "client_secret": os.environ.get("OKTA_CLIENT_SECRET", ""),
            "discovery_url": f"https://{domain}/oauth2/default/.well-known/openid-configuration",
            "scopes":        "openid email profile",
        }

    return p


# PKCE state store (in-memory; acceptable for single-process deployments)
_sso_state: dict[str, dict] = {}  # state -> {provider, code_verifier, created_at}


def _pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge_S256)."""
    verifier   = secrets.token_urlsafe(64)
    digest     = hashlib.sha256(verifier.encode()).digest()
    challenge  = urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _redirect_uri(request: Request, provider: str) -> str:
    base = os.environ.get("PUBLIC_URL", str(request.base_url).rstrip("/"))
    return f"{base}/auth/sso/{provider}/callback"


async def _oidc_config(discovery_url: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(discovery_url)
        r.raise_for_status()
        return r.json()


async def _oidc_token_exchange(
    token_url: str, code: str, redirect_uri: str,
    client_id: str, client_secret: str, verifier: str,
) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(token_url, data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  redirect_uri,
            "client_id":     client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        })
        r.raise_for_status()
        return r.json()


async def _github_user_info(access_token: str) -> tuple[str, Optional[str], Optional[str]]:
    """Returns (provider_id, primary_email, display_name)."""
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=10) as c:
        ur = await c.get("https://api.github.com/user", headers=headers)
        ur.raise_for_status()
        u = ur.json()
        er = await c.get("https://api.github.com/user/emails", headers=headers)
        emails = er.json() if er.is_success else []
    primary = next(
        (e["email"] for e in emails if e.get("primary") and e.get("verified")),
        u.get("email"),
    )
    return str(u["id"]), primary, u.get("name") or u.get("login")


def _finish_login(user: dict, request: Request, response: Response) -> dict:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    jti   = auth_db.create_session(user["id"], ip, ua)
    token = _create_jwt(user, jti)
    auth_db.update_last_login(user["id"])
    _set_cookie(response, token)
    return user


# ── Pydantic models ───────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password:     str


class SetManagerRequest(BaseModel):
    manager_id: Optional[int] = None  # None clears it


class AdminSetRoleRequest(BaseModel):
    role: str  # 'user' | 'admin'


class AdminSetActiveRequest(BaseModel):
    is_active: bool


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return current_user


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/sso/providers", summary="List enabled SSO providers")
def list_providers():
    return {
        "providers": [
            {"key": k, "label": v["label"]}
            for k, v in _sso_providers().items()
        ]
    }


@router.post("/login", summary="Local username/password login")
async def local_login(req: LoginRequest, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"

    if not _rate_check(ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again in 15 minutes.")

    if not _HAS_PASSLIB:
        raise HTTPException(status_code=503, detail="passlib[bcrypt] not installed on server")

    user = auth_db.get_user_by_username(req.username)

    # Constant-time path — always verify something to prevent timing attacks
    if not user or not user.get("password_hash"):
        _pwd_ctx.dummy_verify()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.get("is_active"):
        _pwd_ctx.dummy_verify()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not _pwd_ctx.verify(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    _rate_clear(ip)
    _finish_login(user, request, response)

    return {
        "ok":   True,
        "user": {
            "id":            user["id"],
            "username":      user["username"],
            "role":          user["role"],
            "display_name":  user.get("display_name"),
            "must_change_pw": user["must_change_pw"],
        },
    }


@router.post("/logout", summary="Revoke current session")
def logout(
    response: Response,
    dendrai_session: Optional[str] = Cookie(default=None, alias=_COOKIE_NAME),
):
    if dendrai_session:
        payload = decode_jwt(dendrai_session)
        if payload and payload.get("jti"):
            auth_db.revoke_session(payload["jti"])
    _clear_cookie(response)
    return {"ok": True}


@router.get("/me", summary="Current authenticated user")
def me(current_user: dict = Depends(get_current_user)):
    return {"user": current_user}


@router.get("/users", summary="List active user accounts")
def list_users(current_user: dict = Depends(get_current_user)):
    """
    Active accounts (id, username, display_name, role, manager_id) — powers
    the manager picker and name resolution for the approval workflow.
    Any authenticated user can list this (no sensitive fields returned);
    it is not an admin-only directory.
    """
    return {"users": auth_db.list_active_users()}


@router.put("/users/me/manager", summary="Set your own manager (self-service)")
def set_my_manager(req: SetManagerRequest, current_user: dict = Depends(get_current_user)):
    """
    Self-service reporting-line assignment used to route HITL gate
    adjustments to the right person for review. No admin role required —
    there is no admin console for this yet, so this is intentionally
    self-service. A user cannot set themselves as their own manager.
    """
    if req.manager_id is not None:
        manager = auth_db.get_user_by_id(req.manager_id)
        if not manager or not manager.get("is_active"):
            raise HTTPException(status_code=404, detail="Manager account not found or inactive")
    ok = auth_db.set_manager(current_user["id"], req.manager_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Could not set manager (cannot be yourself)")
    return {"ok": True, "manager_id": req.manager_id}


# ── Admin workflow configuration (Phase 2) ────────────────────────────────────
# Org-chart and role administration for the approval workflow. Every write
# here re-derives identity from the session (require_admin), never trusts a
# role claim from the request body, and blocks an admin from locking
# themselves out (self-demotion / self-deactivation).

@router.get("/admin/users", summary="List all accounts (admin)")
def admin_list_users(current_user: dict = Depends(require_admin)):
    return {"users": auth_db.list_all_users()}


@router.put("/admin/users/{user_id}/manager", summary="Set any user's manager (admin)")
def admin_set_manager(user_id: int, req: SetManagerRequest, current_user: dict = Depends(require_admin)):
    if req.manager_id is not None:
        manager = auth_db.get_user_by_id(req.manager_id)
        if not manager or not manager.get("is_active"):
            raise HTTPException(status_code=404, detail="Manager account not found or inactive")
    ok = auth_db.set_manager(user_id, req.manager_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Could not set manager (cannot be their own manager)")
    return {"ok": True, "manager_id": req.manager_id}


@router.put("/admin/users/{user_id}/role", summary="Set a user's role (admin)")
def admin_set_role(user_id: int, req: AdminSetRoleRequest, current_user: dict = Depends(require_admin)):
    if req.role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="role must be 'user' or 'admin'")
    if user_id == current_user["id"] and req.role != "admin":
        raise HTTPException(status_code=400, detail="You cannot remove your own admin role")
    target = auth_db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not auth_db.set_role(user_id, req.role):
        raise HTTPException(status_code=500, detail="Failed to update role")
    return {"ok": True, "role": req.role}


@router.put("/admin/users/{user_id}/active", summary="Activate or deactivate a user (admin)")
def admin_set_active(user_id: int, req: AdminSetActiveRequest, current_user: dict = Depends(require_admin)):
    if user_id == current_user["id"] and not req.is_active:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
    target = auth_db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not auth_db.set_active(user_id, req.is_active):
        raise HTTPException(status_code=500, detail="Failed to update active status")
    if not req.is_active:
        auth_db.revoke_all_user_sessions(user_id)
    return {"ok": True, "is_active": req.is_active}


@router.post("/change-password", summary="Change own password")
def change_password(
    req: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
):
    if not _HAS_PASSLIB:
        raise HTTPException(status_code=503, detail="passlib[bcrypt] not installed")

    full = auth_db.get_user_by_username(current_user["username"])
    if not full or not full.get("password_hash"):
        raise HTTPException(status_code=400, detail="No local password configured on this account.")

    if not _pwd_ctx.verify(req.current_password, full["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    err = _validate_password(req.new_password)
    if err:
        raise HTTPException(status_code=422, detail=err)

    for old in auth_db.get_password_history(current_user["id"]):
        if _pwd_ctx.verify(req.new_password, old):
            raise HTTPException(
                status_code=422,
                detail="New password matches one of your last 3 passwords. Choose a different one.",
            )

    new_hash = _pwd_ctx.hash(req.new_password)
    if not auth_db.update_password(current_user["id"], new_hash):
        raise HTTPException(status_code=500, detail="Password update failed.")

    auth_db.revoke_all_user_sessions(current_user["id"])
    return {"ok": True, "message": "Password updated. Please log in again."}


@router.get("/sso/{provider}/start", summary="Begin OAuth / OIDC flow")
async def sso_start(provider: str, request: Request):
    providers = _sso_providers()
    if provider not in providers:
        raise HTTPException(status_code=404, detail=f"SSO provider '{provider}' not configured")

    cfg      = providers[provider]
    state    = secrets.token_urlsafe(32)
    verifier, challenge = _pkce()

    _sso_state[state] = {
        "provider":      provider,
        "code_verifier": verifier,
        "created_at":    time.monotonic(),
    }

    redir = _redirect_uri(request, provider)

    if provider == "github":
        from urllib.parse import urlencode
        params = {
            "client_id":    cfg["client_id"],
            "redirect_uri": redir,
            "scope":        cfg["scopes"],
            "state":        state,
        }
        return RedirectResponse(f"{cfg['auth_url']}?{urlencode(params)}", status_code=302)

    oidc = await _oidc_config(cfg["discovery_url"])
    from urllib.parse import urlencode
    params = {
        "client_id":             cfg["client_id"],
        "response_type":         "code",
        "redirect_uri":          redir,
        "scope":                 cfg["scopes"],
        "state":                 state,
        "nonce":                 secrets.token_urlsafe(16),
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }
    return RedirectResponse(f"{oidc['authorization_endpoint']}?{urlencode(params)}", status_code=302)


@router.get("/sso/{provider}/callback", summary="OAuth callback + JIT provisioning")
async def sso_callback(
    provider: str, request: Request, response: Response,
    code: str = "", state: str = "", error: str = "",
):
    if error:
        return RedirectResponse(f"/?auth_error={error}", status_code=302)

    state_data = _sso_state.pop(state, None)
    if not state_data or state_data["provider"] != provider:
        return RedirectResponse("/?auth_error=invalid_state", status_code=302)
    if time.monotonic() - state_data["created_at"] > 600:
        return RedirectResponse("/?auth_error=state_expired", status_code=302)

    providers = _sso_providers()
    if provider not in providers:
        return RedirectResponse("/?auth_error=unknown_provider", status_code=302)

    cfg      = providers[provider]
    redir    = _redirect_uri(request, provider)
    verifier = state_data["code_verifier"]

    try:
        if provider == "github":
            async with httpx.AsyncClient(timeout=15) as c:
                tr = await c.post(cfg["token_url"], data={
                    "client_id":     cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "code":          code,
                    "redirect_uri":  redir,
                }, headers={"Accept": "application/json"})
                tr.raise_for_status()
                tokens = tr.json()
            provider_id, email, display_name = await _github_user_info(tokens["access_token"])

        else:
            oidc   = await _oidc_config(cfg["discovery_url"])
            tokens = await _oidc_token_exchange(
                oidc["token_endpoint"], code, redir,
                cfg["client_id"], cfg["client_secret"], verifier,
            )
            id_token = tokens.get("id_token", "")
            # Decode without signature verification — HTTPS transport already verified the provider.
            claims       = _pyjwt.decode(id_token, options={"verify_signature": False}) if _HAS_JWT else {}
            provider_id  = claims.get("sub", "")
            email        = claims.get("email", "")
            display_name = claims.get("name") or claims.get("preferred_username", "")

    except Exception as exc:
        logger.warning("SSO token exchange failed (%s): %s", provider, exc)
        return RedirectResponse("/?auth_error=sso_failed", status_code=302)

    if not provider_id:
        return RedirectResponse("/?auth_error=no_subject", status_code=302)

    # 1. Look up by SSO identity
    user = auth_db.get_user_by_sso(provider, provider_id)

    # 2. Match by email, or JIT-provision a new account
    if not user and email:
        user = auth_db.get_user_by_email(email)
        if user:
            auth_db.link_sso_identity(user["id"], provider, provider_id, email)
        else:
            raw_base = (email.split("@")[0] if email else display_name or provider_id)[:60]
            base     = re.sub(r"[^a-z0-9._-]", "", raw_base.lower()) or "user"
            uid = auth_db.create_user(
                username=base, email=email, display_name=display_name, sso_only=True,
            )
            if uid:
                auth_db.link_sso_identity(uid, provider, provider_id, email)
                user = auth_db.get_user_by_id(uid)

    if not user:
        return RedirectResponse("/?auth_error=provisioning_failed", status_code=302)
    if not user.get("is_active"):
        return RedirectResponse("/?auth_error=account_inactive", status_code=302)

    _finish_login(user, request, response)
    return RedirectResponse("/", status_code=302)
