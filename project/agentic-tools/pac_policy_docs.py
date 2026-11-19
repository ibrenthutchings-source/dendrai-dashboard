#!/usr/bin/env python3
"""
Plain-language policy intake + human-in-the-loop conversion to Policy-as-Code.

The gap this closes: today the only way prose becomes Rego is sync_github's
Markdown->Rego step, which converts a file, writes the result straight into
pac_policy_modules, and throws the source prose away. Nobody reviews the
generated rules before they are live, and an auditor asking "which written
policy does this deny rule implement?" has nothing to look at.

This module is the reviewed path:

    upload/paste  ->  pac_policy_documents   (prose, stored verbatim, immutable)
        convert   ->  pac_policy_conversions (generated_rego + editable draft)
        review    ->  a human edits the draft, then approves or rejects it
        approve   ->  pac_policy_modules     (a normal versioned module)

Nothing here can publish without a decision: db.save_pac_policy_conversion has
no parameter that creates an already-approved row, and publishing happens only
inside the /decision endpoint.

Router prefix: /pac

    POST   /pac/policy-docs/upload          Upload a policy file (md/txt/pdf/docx/rego/...)
    POST   /pac/policy-docs                 Paste policy text directly
    GET    /pac/policy-docs                 List documents (?process=&status=)
    GET    /pac/policy-docs/{id}            One document + its conversion attempts
    DELETE /pac/policy-docs/{id}            Delete a document (conversions cascade)
    POST   /pac/policy-docs/{id}/convert    Draft Rego from the prose -> pending_review
    GET    /pac/conversions                 HITL review queue (?status=&process=)
    GET    /pac/conversions/{id}            One conversion
    PUT    /pac/conversions/{id}/draft      Reviewer edits the draft Rego
    POST   /pac/conversions/{id}/decision   approve | request_changes | reject
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import zipfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

import claude_client
import db
import pac_endpoints
from auth_endpoints import require_screen_permission

logger = logging.getLogger(__name__)

# Same router-level screen gate as pac_endpoints.router — these routes expose
# the same Policy-as-Code data under the same /pac prefix, so they must not be
# reachable by anyone who can't read that screen.
router = APIRouter(prefix="/pac", tags=["pac"], dependencies=[Depends(require_screen_permission("policycode"))])

# Guardrails on intake. A policy document is prose, not a data dump: 4 MB of
# raw upload and ~600k characters of extracted text is already far past any
# real SOP, and the conversion prompt can't use more than a fraction of it.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_TEXT_CHARS = 600_000

# Sent to the model. Well past a normal policy document, but bounded so a
# pathological upload can't blow the context window (and the cost) — the
# response tells the user when this kicked in rather than silently truncating.
CONVERSION_INPUT_CHARS = 120_000

# Extensions we can turn into text. Anything else is rejected at upload with a
# message naming what IS accepted, rather than being stored as mojibake.
_TEXT_EXTS = {"md", "markdown", "txt", "text", "rst", "rego", "json", "yaml", "yml", "csv", "log", ""}
_BINARY_EXTS = {"pdf", "docx"}


# ─────────────────────────────────────────────────────────────────────────────
# Text extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_docx_text(raw: bytes) -> str:
    """Minimal .docx -> text. A .docx is a zip whose word/document.xml holds
    the body; paragraphs are <w:p> and runs are <w:t>. Done with zipfile+regex
    rather than python-docx purely to avoid adding a dependency for what is
    ~10 lines — policy documents are flowing prose, so dropping tables to
    their cell text (which this does) loses nothing the conversion needs.
    """
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        try:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
        except KeyError:
            raise ValueError("not a Word document (no word/document.xml inside)")
    # Paragraph and line breaks first, so the text doesn't collapse into one blob.
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:br\b[^>]*/?>", "\n", xml)
    xml = re.sub(r"<w:tab\b[^>]*/?>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        text = text.replace(entity, char)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_pdf_text(raw: bytes) -> str:
    try:
        import pdfplumber
    except ImportError:
        raise ValueError("PDF support needs pdfplumber — run: pip install pdfplumber")
    pages: List[str] = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    text = "\n\n".join(pages).strip()
    if not text:
        # A scanned/image-only PDF extracts to nothing. Saying so beats saving
        # an empty document that then "converts" into hallucinated rules.
        raise ValueError("no extractable text — this looks like a scanned image PDF, not a text PDF")
    return text


def _extract_text(filename: str, raw: bytes) -> str:
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext == "pdf":
        return _extract_pdf_text(raw)
    if ext == "docx":
        return _extract_docx_text(raw)
    if ext == "doc":
        raise ValueError("legacy .doc is not supported — save as .docx, .pdf, or .md and re-upload")
    if ext not in _TEXT_EXTS:
        accepted = ", ".join(sorted(e for e in _TEXT_EXTS | _BINARY_EXTS if e))
        raise ValueError(f"unsupported file type '.{ext}' — accepted: {accepted}")
    return raw.decode("utf-8", errors="replace").strip()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _title_from_filename(filename: str) -> str:
    stem = (filename or "policy").rsplit("/", 1)[-1]
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    return re.sub(r"[_-]+", " ", stem).strip().title() or "Untitled policy"


def _require_db():
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database not configured — policy documents cannot be stored")


def _require_process(process: str) -> str:
    key = pac_endpoints._norm_process_key(process or "")
    valid = pac_endpoints._valid_processes()
    if key not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown process '{process}'. Valid: {sorted(valid)}")
    return key


def _analyze(rego: str) -> Dict[str, Any]:
    """Syntax + control-ID facts about a draft, recomputed on every write so
    the review queue never shows a stale verdict next to edited text."""
    ok, errors = pac_endpoints._validate_rego_syntax(rego)
    return {
        "syntax_valid": ok,
        "syntax_errors": errors,
        "control_ids": _control_ids(rego),
        "rule_coverage": pac_endpoints._rule_coverage(rego),
    }


def _control_ids(rego: str) -> List[str]:
    """Control IDs the draft would contribute to the shared catalog vocabulary.
    Surfaced during review because an ID clash or a missing prefix is exactly
    the kind of thing a generated module gets wrong and a human can spot.

    Delegates to pac_endpoints so a draft queued by the GitHub sync and one
    created by an upload are described identically — two copies of this regex
    would drift, and the drift would show up as a review queue whose control
    IDs disagree with the module's depending on where the draft came from."""
    return pac_endpoints.extract_control_ids_from_rego(rego)


def _next_version(process: str) -> str:
    """Minor-bump the process's current module version. Publishing a reviewed
    conversion is a real revision of the live policy, so it gets its own
    version rather than overwriting whatever '1.0' happened to be there."""
    latest = db.get_latest_pac_module(process) if db.is_available() else None
    current = (latest or {}).get("version") or "1.0"
    m = re.match(r"^(\d+)\.(\d+)", current.strip())
    if not m:
        return "1.1"
    return f"{m.group(1)}.{int(m.group(2)) + 1}"


# ─────────────────────────────────────────────────────────────────────────────
# Document intake
# ─────────────────────────────────────────────────────────────────────────────

def _store_document(process: str, title: str, text: str, *, filename: Optional[str],
                    source: str, byte_size: int, uploaded_by: Optional[str]) -> Dict[str, Any]:
    if not text.strip():
        raise HTTPException(status_code=422, detail="Document is empty — nothing to store")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Extracted text is {len(text):,} characters — the limit is {MAX_TEXT_CHARS:,}. "
                   "Split the document into per-topic policies and upload them separately.",
        )

    digest = _sha256(text)
    duplicate = db.find_pac_policy_document_by_hash(process, digest)

    doc_id = db.save_pac_policy_document(
        process, title.strip() or "Untitled policy", text,
        filename=filename, source=source, byte_size=byte_size,
        sha256=digest, uploaded_by=uploaded_by,
    )
    if not doc_id:
        raise HTTPException(status_code=500, detail="Failed to save policy document")

    return {
        "saved": True,
        "document_id": doc_id,
        "process": process,
        "title": title,
        "filename": filename,
        "text_length": len(text),
        "sha256": digest,
        # Reported, not enforced: re-uploading the same text to re-convert it
        # with a newer model is legitimate, so the caller decides what to do.
        "duplicate_of": duplicate,
    }


@router.post("/policy-docs/upload")
async def upload_policy_doc(
    file: UploadFile = File(...),
    process: str = Form(...),
    title: Optional[str] = Form(None),
    uploaded_by: Optional[str] = Form(None),
):
    """Upload a plain-language policy file and store it verbatim.

    Accepts Markdown/text/Rego-ish text files, PDFs (text-layer only), and
    .docx. Conversion is a separate, explicit step — uploading never spends an
    LLM call or touches a live module.
    """
    _require_db()
    key = _require_process(process)

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is {len(raw) / 1024 / 1024:.1f} MB — the limit is {MAX_UPLOAD_BYTES // 1024 // 1024} MB",
        )

    try:
        text = _extract_text(file.filename or "", raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.warning("policy doc extraction failed for %r: %s", file.filename, exc)
        raise HTTPException(status_code=400, detail=f"Could not read '{file.filename}': {exc}")

    return _store_document(
        key, title or _title_from_filename(file.filename or ""), text,
        filename=file.filename, source="upload", byte_size=len(raw), uploaded_by=uploaded_by,
    )


class PastePolicyRequest(BaseModel):
    process: str
    title: str
    text: str
    uploaded_by: Optional[str] = None


@router.post("/policy-docs")
async def create_policy_doc(req: PastePolicyRequest):
    """Store policy text pasted directly into the UI (the common case for a
    single paragraph of standard nobody keeps in a file)."""
    _require_db()
    key = _require_process(req.process)
    return _store_document(
        key, req.title, req.text,
        filename=None, source="paste",
        byte_size=len(req.text.encode("utf-8")), uploaded_by=req.uploaded_by,
    )


@router.get("/policy-docs")
async def list_policy_docs(process: Optional[str] = None, status: Optional[str] = None, limit: int = 200):
    """Uploaded policy documents, newest first, each with a text preview and a
    count of conversions awaiting review."""
    if not db.is_available():
        return {"documents": [], "note": "Database not configured"}
    return {"documents": db.list_pac_policy_documents(process=process, status=status, limit=limit)}


@router.get("/policy-docs/{doc_id}")
async def get_policy_doc(doc_id: int):
    """Full source text plus every conversion attempt against it."""
    _require_db()
    doc = db.get_pac_policy_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Policy document {doc_id} not found")
    return doc


@router.delete("/policy-docs/{doc_id}")
async def delete_policy_doc(doc_id: int):
    """Delete a document and its conversion attempts. A module already
    published from one of those conversions is left alone — it is live policy
    at that point, not a draft."""
    _require_db()
    if not db.delete_pac_policy_document(doc_id):
        raise HTTPException(status_code=404, detail=f"Policy document {doc_id} not found")
    return {"deleted": True, "document_id": doc_id}


# ─────────────────────────────────────────────────────────────────────────────
# Conversion (prose -> draft Rego), always landing in the review queue
# ─────────────────────────────────────────────────────────────────────────────

class ConvertRequest(BaseModel):
    # Extra steer appended to the conversion prompt, e.g. "only the segregation
    # of duties section" or "input events come from the Oracle AP feed".
    guidance: Optional[str] = None


@router.post("/policy-docs/{doc_id}/convert")
async def convert_policy_doc(doc_id: int, req: Optional[ConvertRequest] = None):
    """Draft Rego from a stored policy document and queue it for human review.

    The draft is NEVER written to the live module — it lands in
    pac_policy_conversions at status 'pending_review'. Syntax-invalid output is
    still saved (with its errors) rather than discarded: a reviewer fixing a
    near-miss draft by hand is the normal case, and the sync_github path's
    habit of silently dropping failures is what made those failures so hard to
    diagnose.
    """
    _require_db()
    doc = db.get_pac_policy_document(doc_id, include_conversions=False)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Policy document {doc_id} not found")

    process = doc["process"]
    source_text = doc["doc_text"]
    truncated = len(source_text) > CONVERSION_INPUT_CHARS
    if truncated:
        source_text = source_text[:CONVERSION_INPUT_CHARS]

    guidance = (req.guidance if req else None) or ""
    prose = source_text
    if guidance.strip():
        prose = f"{source_text}\n\n---\nReviewer guidance for this conversion: {guidance.strip()}"

    db.set_pac_policy_document_status(doc_id, "converting")
    try:
        completion = await asyncio.to_thread(
            pac_endpoints._convert_markdown_to_rego,
            process, doc.get("filename") or doc["title"], prose,
        )
    except Exception as exc:
        logger.warning("policy doc %s conversion failed: %s", doc_id, exc)
        db.set_pac_policy_document_status(doc_id, "failed")
        raise HTTPException(status_code=502, detail=f"Conversion failed: {exc}")

    rego = pac_endpoints._strip_code_fence(completion)
    analysis = _analyze(rego)

    conversion_id = db.save_pac_policy_conversion(
        doc_id, process, rego,
        model=getattr(claude_client, "MODEL", None),
        syntax_valid=analysis["syntax_valid"],
        syntax_errors=analysis["syntax_errors"],
        control_ids=analysis["control_ids"],
    )
    if not conversion_id:
        db.set_pac_policy_document_status(doc_id, "failed")
        raise HTTPException(status_code=500, detail="Conversion succeeded but could not be saved")

    db.set_pac_policy_document_status(doc_id, "in_review")

    return {
        "converted": True,
        "document_id": doc_id,
        "conversion_id": conversion_id,
        "process": process,
        "rego_content": rego,
        "status": "pending_review",
        "source_truncated": truncated,
        **analysis,
    }


@router.get("/conversions")
async def list_conversions(process: Optional[str] = None, status: Optional[str] = "pending_review",
                           limit: int = 100):
    """The HITL review queue. Defaults to conversions still awaiting a
    decision; pass status= (or status=all) to see closed ones."""
    if not db.is_available():
        return {"conversions": [], "note": "Database not configured"}
    rows = db.list_pac_policy_conversions(
        process=process,
        status=None if status in (None, "", "all") else status,
        limit=limit,
    )
    return {"conversions": rows, "count": len(rows)}


@router.get("/conversions/{conversion_id}")
async def get_conversion(conversion_id: int):
    """One conversion, with both the untouched model output and the reviewer's
    current draft so the UI can diff them."""
    _require_db()
    row = db.get_pac_policy_conversion(conversion_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Conversion {conversion_id} not found")
    row["rule_coverage"] = pac_endpoints._rule_coverage(row.get("draft_rego") or "")
    return row


class UpdateDraftRequest(BaseModel):
    rego_content: str


@router.put("/conversions/{conversion_id}/draft")
async def update_conversion_draft(conversion_id: int, req: UpdateDraftRequest):
    """Save the reviewer's edits, re-validating as we go so the queue's
    syntax verdict always describes the text currently there.

    Rejected with 409 once a decision has been recorded — editing after
    approval would mean the reviewed text and the published text differ.
    """
    _require_db()
    if not req.rego_content.strip():
        raise HTTPException(status_code=422, detail="rego_content must not be empty")

    existing = db.get_pac_policy_conversion(conversion_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Conversion {conversion_id} not found")

    analysis = _analyze(req.rego_content)
    ok = db.update_pac_policy_conversion_draft(
        conversion_id, req.rego_content,
        syntax_valid=analysis["syntax_valid"],
        syntax_errors=analysis["syntax_errors"],
        control_ids=analysis["control_ids"],
    )
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"Conversion {conversion_id} is '{existing['status']}' and can no longer be edited",
        )
    return {"saved": True, "conversion_id": conversion_id, **analysis}


class DecisionRequest(BaseModel):
    decision: str                       # approve | request_changes | reject
    reviewer: str
    reviewer_role: Optional[str] = None
    notes: Optional[str] = None
    # Approve only. Omit to minor-bump the process's current module version.
    version: Optional[str] = None


_DECISION_STATUS = {
    "approve": "approved",
    "request_changes": "changes_requested",
    "reject": "rejected",
}


@router.post("/conversions/{conversion_id}/decision")
async def decide_conversion(conversion_id: int, req: DecisionRequest):
    """Record the human decision — the only path by which generated Rego
    becomes live policy.

    approve         publishes draft_rego as a new pac_policy_modules version
                    (source_format 'llm_converted') and marks the document
                    published. The module still needs its own approver
                    sign-off in the Rego Editor; this decision attests that
                    the Rego faithfully implements the prose, which is a
                    different question from whether the policy is approved.
    request_changes leaves it in the queue, editable, with the reason attached.
    reject          closes it. The source document goes back to 'uploaded' so
                    it can be re-converted (e.g. with guidance) rather than
                    being stranded in review with no open conversion.
    """
    _require_db()

    status = _DECISION_STATUS.get((req.decision or "").strip().lower())
    if not status:
        raise HTTPException(
            status_code=422,
            detail=f"decision must be one of: {', '.join(_DECISION_STATUS)}",
        )
    if not (req.reviewer or "").strip():
        raise HTTPException(status_code=422, detail="reviewer is required — this is the human-in-the-loop record")

    conv = db.get_pac_policy_conversion(conversion_id)
    if not conv:
        raise HTTPException(status_code=404, detail=f"Conversion {conversion_id} not found")
    if conv["status"] in ("approved", "rejected"):
        raise HTTPException(
            status_code=409,
            detail=f"Conversion {conversion_id} was already {conv['status']} by {conv.get('reviewer') or 'a reviewer'}",
        )

    module_id: Optional[int] = None
    version: Optional[str] = None
    if status == "approved":
        draft = conv.get("draft_rego") or ""
        # Blocking here, unlike the module-approval gate's advisory negative
        # tests: that gate warns because several built-in modules legitimately
        # fail the input-schema contract, whereas Rego that doesn't even parse
        # cannot be evaluated by OPA at all and would publish a dead module.
        ok, errors = pac_endpoints._validate_rego_syntax(draft)
        if not ok:
            raise HTTPException(
                status_code=422,
                detail=f"Draft does not pass Rego validation, so it cannot be published: {'; '.join(errors)}. "
                       "Fix the draft (or choose 'request changes') first.",
            )
        version = (req.version or "").strip() or _next_version(conv["process"])
        module_name = f"controls.oracle_fusion.{conv['process']}"
        module_id = db.save_pac_module(
            conv["process"], module_name, draft, version, source_format="llm_converted",
        )
        if not module_id:
            raise HTTPException(status_code=500, detail="Approved, but publishing the module failed — nothing was saved")
        pac_endpoints.embed_pac_module(module_id, conv["process"], draft)

    saved = db.record_pac_conversion_decision(
        conversion_id, status, req.reviewer.strip(),
        reviewer_role=(req.reviewer_role or "").strip() or None,
        review_notes=(req.notes or "").strip() or None,
        published_module_id=module_id,
    )
    if not saved:
        raise HTTPException(status_code=500, detail="Failed to record the review decision")

    # Rejecting the newest draft returns the document to the queue-free state so
    # it can be re-converted — but only back to 'uploaded' if nothing was ever
    # published from it. A document whose earlier conversion IS live stays
    # 'published': rejecting a later re-conversion doesn't unpublish anything.
    if status == "rejected":
        prior = db.get_pac_policy_document(conv["document_id"]) or {}
        published_before = any(
            c["id"] != conversion_id and c.get("published_module_id")
            for c in prior.get("conversions", [])
        )
        doc_status = "published" if published_before else "uploaded"
    else:
        doc_status = "published" if status == "approved" else "in_review"
    db.set_pac_policy_document_status(conv["document_id"], doc_status)

    return {
        "recorded": True,
        "conversion_id": conversion_id,
        "status": status,
        "reviewer": req.reviewer.strip(),
        "document_id": conv["document_id"],
        "document_status": doc_status,
        "published_module_id": module_id,
        "published_version": version,
        "process": conv["process"],
    }
