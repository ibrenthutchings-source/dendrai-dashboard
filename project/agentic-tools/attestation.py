#!/usr/bin/env python3
"""
Pipeline provenance/attestation — OIDC identity claims, SLSA provenance,
environment-variable-hash drift detection, Cosign/Sigstore bundle structural
validation, and SBOM (CycloneDX/SPDX) parsing with license-risk flagging.

Pure functions, no DB, no network calls. Honest scope: this validates the
STRUCTURAL completeness of what a CI system submits (which required fields
are present, whether they hang together) and optionally shells out to a real
`cosign` binary if one is on PATH — it does NOT reimplement Sigstore's
trust-root/Rekor verification from scratch (that needs live network access to
Rekor and a full x509/transparency-log verification stack, which is a
materially larger undertaking than this module claims to be). Every function
here reports what it can and cannot confirm rather than fabricating a verdict.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from typing import Optional

_REQUIRED_SLSA_FIELDS = ("predicateType", "subject", "predicate")

_COPYLEFT_PREFIXES = ("GPL-", "AGPL-", "LGPL-", "MPL-")


# ── Environment variable hash (detects an injected SKIP_TESTS=true / DISABLE_SAST=1) ──

def hash_env_vars(env_vars: dict) -> str:
    """SHA256 of sorted key=value pairs. Never stores the raw values in the
    record — the point is detecting that the environment *changed*, not
    archiving secrets. Compare the returned hash against a known-good
    baseline the same way scm_connectors.diff_compliance compares
    branch-protection state: a changed hash is drift worth looking at, not
    automatically a hard failure (a real pipeline's environment legitimately
    changes sometimes)."""
    canonical = "\n".join(f"{k}={env_vars[k]}" for k in sorted(env_vars))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── SLSA provenance (structural validation + coarse level estimate) ──────────

def validate_slsa_provenance(statement: dict) -> dict:
    """Structural check only: reports which required in-toto/SLSA fields are
    present and a coarse 0-3 SLSA level ESTIMATE based on that shape. This is
    not a substitute for verifying the statement's signature against a real
    builder identity — it tells you whether what was submitted is complete
    enough to even be a genuine SLSA provenance statement."""
    missing = [f for f in _REQUIRED_SLSA_FIELDS if f not in statement]
    if missing:
        return {"valid": False, "level": 0, "missing_fields": missing}

    predicate = statement.get("predicate") or {}
    builder_id = (predicate.get("builder") or {}).get("id")
    build_type = predicate.get("buildType")
    has_materials = bool(predicate.get("materials") or predicate.get("buildDefinition"))
    subjects = statement.get("subject") or []
    has_digest = bool(subjects) and all(bool(s.get("digest")) for s in subjects)

    level = 0
    if statement.get("predicateType") and has_digest:
        level = 1
    if level >= 1 and builder_id and build_type:
        level = 2
    if level >= 2 and has_materials:
        level = 3

    return {
        "valid": True,
        "level": level,
        "builder_id": builder_id,
        "build_type": build_type,
        "subject_count": len(subjects),
        "missing_fields": [],
    }


# ── Cosign / Sigstore bundle (structural validation + optional real check) ───

def _find_cosign_binary() -> Optional[str]:
    env_path = os.environ.get("COSIGN_BINARY", "").strip()
    if env_path and os.path.isfile(env_path):
        return env_path
    return shutil.which("cosign")


def verify_cosign_bundle(bundle: dict) -> dict:
    """Structural validation of a Cosign/Sigstore bundle — checks that
    signature + certificate material and (ideally) a Rekor transparency-log
    entry are present. Accepts either the legacy cosign bundle shape
    (base64Signature/cert) or the newer sigstore-python Bundle JSON shape
    (verificationMaterial/messageSignature).

    Reports "verified": "true"/"false"/"unknown" — "unknown" whenever real
    cryptographic verification can't actually be performed here (no `cosign`
    binary, or the signed artifact bytes weren't submitted alongside the
    bundle), rather than guessing."""
    has_legacy = "base64Signature" in bundle and "cert" in bundle
    has_new = "verificationMaterial" in bundle and "messageSignature" in bundle
    if not (has_legacy or has_new):
        return {"verified": "false", "reason": "bundle missing signature/certificate material", "has_rekor_entry": False}

    has_rekor = bool(
        bundle.get("rekorBundle")
        or (bundle.get("verificationMaterial") or {}).get("tlogEntries")
    )

    if not _find_cosign_binary():
        return {
            "verified": "unknown",
            "reason": "cosign binary not available on this host — structural check only",
            "has_rekor_entry": has_rekor,
        }

    # Real `cosign verify-blob`/`verify` needs the signed artifact's bytes or
    # digest, which this endpoint doesn't receive (only the bundle itself) —
    # report structural completeness plus binary availability rather than
    # fabricating a pass/fail.
    return {
        "verified": "unknown",
        "reason": "cosign binary present, but verification requires the signed artifact (not submitted)",
        "has_rekor_entry": has_rekor,
    }


# ── SBOM parsing (CycloneDX / SPDX) + license risk ────────────────────────────

def _is_copyleft(license_id: Optional[str]) -> bool:
    if not license_id:
        return False
    return license_id.strip().upper().startswith(_COPYLEFT_PREFIXES)


def parse_sbom(document: dict, fmt: str) -> dict:
    """Extract {name, version, licenses} per component from a CycloneDX or
    SPDX JSON SBOM document, and flag GPL/AGPL/LGPL/MPL ("copyleft") licenses
    as a legal risk needing review alongside the vulnerability findings."""
    fmt = (fmt or "").strip().lower()
    components: list = []

    if fmt == "cyclonedx":
        for c in document.get("components") or []:
            licenses = []
            for lic in c.get("licenses") or []:
                lic_obj = lic.get("license") or {}
                lic_id = lic_obj.get("id") or lic_obj.get("name")
                if lic_id:
                    licenses.append(lic_id)
            components.append({"name": c.get("name"), "version": c.get("version"), "licenses": licenses})
    elif fmt == "spdx":
        for pkg in document.get("packages") or []:
            lic = pkg.get("licenseConcluded") or pkg.get("licenseDeclared")
            licenses = [lic] if lic and lic not in ("NOASSERTION", "NONE") else []
            components.append({"name": pkg.get("name"), "version": pkg.get("versionInfo"), "licenses": licenses})
    else:
        return {"components": [], "component_count": 0, "license_risk": False,
                "copyleft_components": [], "error": f"unsupported SBOM format '{fmt}' (expected cyclonedx or spdx)"}

    copyleft = [c for c in components if any(_is_copyleft(l) for l in c["licenses"])]
    return {
        "components": components,
        "component_count": len(components),
        "license_risk": bool(copyleft),
        "copyleft_components": copyleft,
    }
