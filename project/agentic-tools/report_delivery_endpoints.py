"""
Report Delivery — push a Loop Report or Audit Evidence Pack to an external
webhook / API endpoint (a GRC platform, SIEM, ticketing system, Slack/Teams
channel, or any custom receiver), instead of only being printed or downloaded
by hand from the Assess Risk screen.

Two concerns kept deliberately separate:

  * report_destinations — saved, reusable send targets (URL, auth, payload
    shape). Configured once, reused from the Loop Report / Evidence Pack
    modals' "Send to..." picker. Credentials are Fernet-encrypted the same
    way poll_connectors' are (db.encrypt_credentials/decrypt_credentials) —
    this table holds OUR outbound credential to THEM, the same direction as
    poll_connectors, not the ingest_api_key direction of monitored_systems.

  * report_deliveries — every send attempt, successful or not. The
    artifacts being sent ARE audit evidence, so "who sent this run's
    evidence pack, to which system, when, and did it land" must itself be
    reconstructable — a fire-and-forget POST with no record would quietly
    destroy that.

Outbound requests are guarded against SSRF the same way rss_proxy in
api_server.py guards feed fetches: private/loopback/link-local hosts are
rejected before any request is made. A saved destination's URL is normally
entered once by a trusted editor, but the "test" and "send" actions still
make a live outbound POST driven by that value on every call, so the guard
applies there too rather than only at creation time.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import db
from auth_endpoints import require_screen_permission

logger = logging.getLogger("ubo.report_delivery")

router = APIRouter(prefix="/report-delivery", tags=["Report Delivery"])

# Loop Report and Evidence Pack both live on the Assess Risk (pipeline)
# screen — same screen gate as evidence_pack_endpoints.router.
_SCREEN_ID = "pipeline"

_VALID_ARTIFACT_TYPES = {"loop_report", "evidence_pack"}
_VALID_AUTH_TYPES = {"none", "bearer", "api_key", "basic", "hmac"}
_VALID_PAYLOAD_FORMATS = {"raw", "slack", "msteams"}
_VALID_METHODS = {"POST", "PUT"}

_REQUEST_TIMEOUT_S = 15
_RESPONSE_EXCERPT_MAX = 2000

# Same blocklist as api_server.py's rss-proxy _PRIVATE_HOST_RE — duplicated
# rather than imported because api_server.py imports this module's router
# (importing back would be circular). Blocks RFC-1918, loopback, link-local
# (cloud metadata endpoints), the 0.x.x.x range, and IPv6 loopback/ULA/
# link-local equivalents.
_PRIVATE_HOST_RE = re.compile(
    r"^("
    r"localhost"
    r"|127\."
    r"|10\."
    r"|172\.(1[6-9]|2\d|3[01])\."
    r"|192\.168\."
    r"|169\.254\."
    r"|0\."
    r"|::1$"
    r"|fc[0-9a-f]{2}:"
    r"|fe[89ab][0-9a-f]:"
    r")",
    re.IGNORECASE,
)


def _validate_destination_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=422, detail="url must be http or https")
    host = parsed.hostname or ""
    if not host or _PRIVATE_HOST_RE.match(host):
        raise HTTPException(status_code=422, detail=f"url host is not allowed: {host or '(empty)'}")


# ── Request/response models ─────────────────────────────────────────────────

class DestinationCredentials(BaseModel):
    # Shapes vary by auth_type — validated in _validate_credentials below
    # rather than as separate Pydantic models, since only one of these
    # fields is meaningful per auth_type and the union would otherwise
    # need a discriminator the frontend has no reason to think about.
    token: Optional[str] = None          # bearer
    header_name: Optional[str] = None    # api_key (default X-API-Key)
    api_key: Optional[str] = None        # api_key
    username: Optional[str] = None       # basic
    password: Optional[str] = None       # basic
    secret: Optional[str] = None         # hmac — signs the request body


def _validate_credentials(auth_type: str, creds: Optional[Dict[str, Any]]) -> None:
    if auth_type == "none":
        return
    creds = creds or {}
    if auth_type == "bearer" and not creds.get("token"):
        raise HTTPException(status_code=422, detail="bearer auth requires credentials.token")
    if auth_type == "api_key" and not creds.get("api_key"):
        raise HTTPException(status_code=422, detail="api_key auth requires credentials.api_key")
    if auth_type == "basic" and not (creds.get("username") and creds.get("password")):
        raise HTTPException(status_code=422, detail="basic auth requires credentials.username and credentials.password")
    if auth_type == "hmac" and not creds.get("secret"):
        raise HTTPException(status_code=422, detail="hmac auth requires credentials.secret")


class CreateDestinationRequest(BaseModel):
    display_name: str
    url: str
    description: Optional[str] = None
    artifact_types: List[str] = Field(default_factory=lambda: ["loop_report", "evidence_pack"])
    http_method: str = "POST"
    auth_type: str = "none"
    credentials: Optional[DestinationCredentials] = None
    headers: Optional[Dict[str, str]] = None
    payload_format: str = "raw"


class UpdateDestinationRequest(BaseModel):
    display_name: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    artifact_types: Optional[List[str]] = None
    http_method: Optional[str] = None
    auth_type: Optional[str] = None
    credentials: Optional[DestinationCredentials] = None
    headers: Optional[Dict[str, str]] = None
    payload_format: Optional[str] = None
    active: Optional[bool] = None


class SendReportRequest(BaseModel):
    artifact_type: str
    payload: Dict[str, Any]
    # Either send to a saved destination...
    destination_id: Optional[int] = None
    # ...or as an ad-hoc one-off send with the target inlined (nothing saved).
    url: Optional[str] = None
    http_method: str = "POST"
    auth_type: str = "none"
    credentials: Optional[DestinationCredentials] = None
    headers: Optional[Dict[str, str]] = None
    payload_format: str = "raw"
    # Context stamped onto the delivery log — best-effort, not required.
    run_id: Optional[int] = None
    ticker: Optional[str] = None


def _validate_artifact_types(types: List[str]) -> None:
    bad = set(types) - _VALID_ARTIFACT_TYPES
    if bad:
        raise HTTPException(status_code=422, detail=f"artifact_types contains invalid values: {sorted(bad)}")
    if not types:
        raise HTTPException(status_code=422, detail="artifact_types must not be empty")


def _validate_common(url: str, http_method: str, auth_type: str, payload_format: str,
                      credentials: Optional[DestinationCredentials]) -> None:
    _validate_destination_url(url)
    if http_method.upper() not in _VALID_METHODS:
        raise HTTPException(status_code=422, detail=f"http_method must be one of {sorted(_VALID_METHODS)}")
    if auth_type not in _VALID_AUTH_TYPES:
        raise HTTPException(status_code=422, detail=f"auth_type must be one of {sorted(_VALID_AUTH_TYPES)}")
    if payload_format not in _VALID_PAYLOAD_FORMATS:
        raise HTTPException(status_code=422, detail=f"payload_format must be one of {sorted(_VALID_PAYLOAD_FORMATS)}")
    _validate_credentials(auth_type, credentials.model_dump() if credentials else None)


# ── Destination CRUD ─────────────────────────────────────────────────────────

@router.get("/destinations")
def list_destinations(current_user: dict = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        return {"destinations": []}
    return {"destinations": db.list_report_destinations()}


@router.post("/destinations")
def create_destination(req: CreateDestinationRequest,
                        current_user: dict = Depends(require_screen_permission(_SCREEN_ID, edit=True))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    _validate_artifact_types(req.artifact_types)
    _validate_common(req.url, req.http_method, req.auth_type, req.payload_format, req.credentials)

    dest_id = db.create_report_destination(
        display_name=req.display_name.strip(), url=req.url, description=req.description,
        artifact_types=req.artifact_types, http_method=req.http_method.upper(),
        auth_type=req.auth_type,
        credentials=req.credentials.model_dump(exclude_none=True) if req.credentials else None,
        headers=req.headers, payload_format=req.payload_format,
        created_by=current_user.get("username"),
    )
    if not dest_id:
        raise HTTPException(status_code=500, detail="Failed to create destination")
    return {"id": dest_id}


@router.put("/destinations/{destination_id}")
def update_destination(destination_id: int, req: UpdateDestinationRequest,
                        current_user: dict = Depends(require_screen_permission(_SCREEN_ID, edit=True))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    existing = db.get_report_destination(destination_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Destination not found")

    if req.artifact_types is not None:
        _validate_artifact_types(req.artifact_types)
    if req.url is not None or req.http_method is not None or req.auth_type is not None or req.payload_format is not None:
        _validate_common(
            req.url if req.url is not None else existing["url"],
            req.http_method if req.http_method is not None else existing["http_method"],
            req.auth_type if req.auth_type is not None else existing["auth_type"],
            req.payload_format if req.payload_format is not None else existing["payload_format"],
            req.credentials,
        )

    ok = db.update_report_destination(
        destination_id,
        display_name=req.display_name, description=req.description,
        artifact_types=req.artifact_types, url=req.url,
        http_method=req.http_method, auth_type=req.auth_type,
        credentials=req.credentials.model_dump(exclude_none=True) if req.credentials else None,
        headers=req.headers, payload_format=req.payload_format, active=req.active,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="No fields to update")
    return {"updated": True}


@router.delete("/destinations/{destination_id}")
def delete_destination(destination_id: int,
                        current_user: dict = Depends(require_screen_permission(_SCREEN_ID, edit=True))):
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    ok = db.delete_report_destination(destination_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Destination not found")
    return {"deleted": True}


@router.get("/deliveries")
def list_deliveries(destination_id: Optional[int] = None, run_id: Optional[int] = None, limit: int = 50,
                     current_user: dict = Depends(require_screen_permission(_SCREEN_ID))):
    if not db.is_available():
        return {"deliveries": []}
    return {"deliveries": db.list_report_deliveries(destination_id=destination_id, run_id=run_id, limit=limit)}


# ── Payload shaping ──────────────────────────────────────────────────────────

def _shape_payload(payload_format: str, artifact_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap the raw report/evidence-pack JSON into the shape a given
    receiver expects. 'raw' passes it through untouched — the default,
    and the right choice for any custom system or generic webhook
    receiver; 'slack'/'msteams' produce a human-readable summary card for
    the chat-ops case, since neither platform renders an arbitrary JSON
    blob usefully."""
    if payload_format == "raw":
        return payload

    label = "Loop Report" if artifact_type == "loop_report" else "Audit Evidence Pack"
    entity = payload.get("entity") or payload.get("run", {}).get("company_name") or payload.get("ticker") or ""
    summary_bits = []
    if artifact_type == "loop_report":
        risks = payload.get("risks") or []
        appetite = (payload.get("riskAppetite") or {}).get("status")
        summary_bits = [f"{len(risks)} risks" if risks else None, appetite]
    else:
        summary_bits = [
            f"{len(payload.get('risk_scores') or [])} risk scores",
            f"{len(payload.get('approval_tasks') or [])} sign-offs",
        ]
    summary = " · ".join(b for b in summary_bits if b)
    text = f"Dendrai {label} — {entity}" + (f" ({summary})" if summary else "")

    if payload_format == "slack":
        return {
            "text": text,
            "attachments": [{
                "color": "#2e7d32",
                "title": text,
                "fields": [{"title": "Artifact", "value": label, "short": True}],
                "footer": "Dendrai Intelligenza",
            }],
        }
    if payload_format == "msteams":
        return {"@type": "MessageCard", "@context": "http://schema.org/extensions",
                "summary": text, "themeColor": "2e7d32", "title": label, "text": summary or text}
    return payload


def _build_auth_headers(auth_type: str, creds: Optional[Dict[str, Any]], body_bytes: bytes) -> Dict[str, str]:
    creds = creds or {}
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {creds.get('token', '')}"}
    if auth_type == "api_key":
        return {(creds.get("header_name") or "X-API-Key"): creds.get("api_key", "")}
    if auth_type == "hmac":
        secret = (creds.get("secret") or "").encode("utf-8")
        sig = hmac_mod.new(secret, body_bytes, hashlib.sha256).hexdigest()
        return {"X-Dendrai-Signature-256": f"sha256={sig}"}
    return {}


def _basic_auth(auth_type: str, creds: Optional[Dict[str, Any]]):
    if auth_type == "basic":
        creds = creds or {}
        return (creds.get("username", ""), creds.get("password", ""))
    return None


def _dispatch(*, url: str, http_method: str, auth_type: str, credentials: Optional[Dict[str, Any]],
              extra_headers: Optional[Dict[str, str]], payload_format: str,
              artifact_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Make the outbound call and return a result dict — never raises for a
    failed delivery (network error, non-2xx, timeout); those are captured
    as status='error' so the caller can log and report them uniformly,
    same as _post_webhook_alert's swallow-and-log posture but surfaced to
    the caller instead of just logged."""
    _validate_destination_url(url)
    shaped = _shape_payload(payload_format, artifact_type, payload)
    body_bytes = json.dumps(shaped, default=str).encode("utf-8")
    headers = {"Content-Type": "application/json", **(extra_headers or {})}
    headers.update(_build_auth_headers(auth_type, credentials, body_bytes))

    started = time.monotonic()
    try:
        resp = requests.request(
            http_method.upper(), url, data=body_bytes, headers=headers,
            auth=_basic_auth(auth_type, credentials), timeout=_REQUEST_TIMEOUT_S,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        ok = 200 <= resp.status_code < 300
        return {
            "status": "ok" if ok else "error",
            "http_status": resp.status_code,
            "error": None if ok else f"HTTP {resp.status_code}",
            "response_excerpt": (resp.text or "")[:_RESPONSE_EXCERPT_MAX],
            "content_sha256": hashlib.sha256(body_bytes).hexdigest(),
            "content_bytes": len(body_bytes),
            "duration_ms": duration_ms,
        }
    except requests.Timeout:
        return {
            "status": "error", "http_status": None, "error": f"Request timed out after {_REQUEST_TIMEOUT_S}s",
            "response_excerpt": None, "content_sha256": hashlib.sha256(body_bytes).hexdigest(),
            "content_bytes": len(body_bytes), "duration_ms": int((time.monotonic() - started) * 1000),
        }
    except requests.RequestException as exc:
        return {
            "status": "error", "http_status": None, "error": str(exc)[:500],
            "response_excerpt": None, "content_sha256": hashlib.sha256(body_bytes).hexdigest(),
            "content_bytes": len(body_bytes), "duration_ms": int((time.monotonic() - started) * 1000),
        }


@router.post("/destinations/{destination_id}/test")
def test_destination(destination_id: int,
                      current_user: dict = Depends(require_screen_permission(_SCREEN_ID, edit=True))):
    """Send a small synthetic ping payload — verifies auth/connectivity
    without requiring a real pipeline run."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    dest = db.get_report_destination(destination_id, include_credentials=True)
    if not dest:
        raise HTTPException(status_code=404, detail="Destination not found")

    test_payload = {
        "test": True,
        "source": "Dendrai Intelligenza",
        "sent_by": current_user.get("username"),
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "message": "This is a connectivity test from the Dendrai Report Delivery configuration screen.",
    }
    result = _dispatch(
        url=dest["url"], http_method=dest["http_method"], auth_type=dest["auth_type"],
        credentials=dest.get("credentials"), extra_headers=dest.get("headers"),
        payload_format=dest["payload_format"], artifact_type="test", payload=test_payload,
    )
    db.record_report_delivery(
        destination_id=destination_id, destination_name=dest["display_name"], artifact_type="test",
        status=result["status"], url=dest["url"], http_status=result["http_status"], error=result["error"],
        response_excerpt=result["response_excerpt"], content_sha256=result["content_sha256"],
        content_bytes=result["content_bytes"], duration_ms=result["duration_ms"],
        sent_by=current_user.get("username"),
    )
    return result


@router.post("/send")
def send_report(req: SendReportRequest, current_user: dict = Depends(require_screen_permission(_SCREEN_ID, edit=True))):
    """Send a Loop Report or Evidence Pack payload — to a saved destination
    (destination_id) or an ad-hoc one-off target (url + auth inlined,
    nothing persisted as a destination). Every attempt is logged to
    report_deliveries regardless of which path was used or whether it
    succeeded."""
    if req.artifact_type not in _VALID_ARTIFACT_TYPES:
        raise HTTPException(status_code=422, detail=f"artifact_type must be one of {sorted(_VALID_ARTIFACT_TYPES)}")
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    if req.destination_id is not None:
        dest = db.get_report_destination(req.destination_id, include_credentials=True)
        if not dest:
            raise HTTPException(status_code=404, detail="Destination not found")
        if not dest["active"]:
            raise HTTPException(status_code=422, detail="Destination is inactive")
        url, http_method, auth_type = dest["url"], dest["http_method"], dest["auth_type"]
        credentials, extra_headers, payload_format = dest.get("credentials"), dest.get("headers"), dest["payload_format"]
        dest_name = dest["display_name"]
    else:
        if not req.url:
            raise HTTPException(status_code=422, detail="Either destination_id or url is required")
        _validate_common(req.url, req.http_method, req.auth_type, req.payload_format, req.credentials)
        url, http_method, auth_type = req.url, req.http_method, req.auth_type
        credentials = req.credentials.model_dump(exclude_none=True) if req.credentials else None
        extra_headers, payload_format = req.headers, req.payload_format
        dest_name = None

    result = _dispatch(
        url=url, http_method=http_method, auth_type=auth_type, credentials=credentials,
        extra_headers=extra_headers, payload_format=payload_format,
        artifact_type=req.artifact_type, payload=req.payload,
    )
    db.record_report_delivery(
        destination_id=req.destination_id, destination_name=dest_name, artifact_type=req.artifact_type,
        status=result["status"], url=url, run_id=req.run_id, ticker=req.ticker,
        http_status=result["http_status"], error=result["error"], response_excerpt=result["response_excerpt"],
        content_sha256=result["content_sha256"], content_bytes=result["content_bytes"],
        duration_ms=result["duration_ms"], sent_by=current_user.get("username"),
    )
    if result["status"] == "error":
        logger.warning("Report delivery failed: dest=%s artifact=%s error=%s",
                        dest_name or url, req.artifact_type, result["error"])
    else:
        logger.info("Report delivered: dest=%s artifact=%s run_id=%s", dest_name or url, req.artifact_type, req.run_id)
    return result
