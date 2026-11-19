#!/usr/bin/env python3
"""
Tests for mcp_governance.py's passive shadow-AI detection:
_extract_ai_tool_name, the shadow_ai_tool_detected flag in
_detect_system_flags, and _ingest_system_event's post-insert candidate
upsert hook.

    pytest test_mcp_governance_shadow_ai.py -v
"""
from __future__ import annotations

from unittest.mock import patch

import mcp_governance as mg


class TestExtractAiToolName:
    def test_matches_entitlement_field(self):
        assert mg._extract_ai_tool_name({"entitlement": "OPENAI_ENTERPRISE_ACCESS"}, "") == "OpenAI"

    def test_matches_are_case_insensitive(self):
        assert mg._extract_ai_tool_name({"entitlement": "openai_enterprise_access"}, "") == "OpenAI"

    def test_matches_copilot(self):
        assert mg._extract_ai_tool_name({"entitlement": "GITHUB_COPILOT_SEAT"}, "") == "GitHub Copilot"

    def test_matches_claude_and_anthropic_to_the_same_display_name(self):
        assert mg._extract_ai_tool_name({"entitlement": "ANTHROPIC_CLAUDE_API"}, "") == "Anthropic Claude"
        assert mg._extract_ai_tool_name({"x": "claude-access"}, "") == "Anthropic Claude"

    def test_matches_resource_field_too(self):
        assert mg._extract_ai_tool_name({}, "midjourney-team-workspace") == "Midjourney"

    def test_no_match_for_ordinary_erp_entitlement(self):
        assert mg._extract_ai_tool_name({"entitlement": "AP_INVOICE_ENTRY"}, "") is None

    def test_no_match_on_empty_payload_and_resource(self):
        assert mg._extract_ai_tool_name({}, "") is None

    def test_ignores_non_string_payload_values(self):
        assert mg._extract_ai_tool_name({"sod_conflict_detected": True, "amount": 500}, "") is None


class TestDetectSystemFlagsShadowAi:
    def _event(self, entitlement=None, resource=""):
        payload = {"entitlement": entitlement} if entitlement else {}
        return {"action": "", "resource": resource, "severity": "INFO",
                "event_type": "IAM_ACCESS_REQUEST_EVENT", "payload": payload}

    def test_flag_set_when_entitlement_names_an_ai_tool(self):
        flags = mg._detect_system_flags(self._event(entitlement="OPENAI_ENTERPRISE_ACCESS"))
        assert "shadow_ai_tool_detected" in flags

    def test_flag_not_set_for_ordinary_erp_entitlement(self):
        flags = mg._detect_system_flags(self._event(entitlement="PO_BUYER"))
        assert "shadow_ai_tool_detected" not in flags

    def test_flag_not_set_when_payload_has_no_entitlement_at_all(self):
        flags = mg._detect_system_flags(self._event())
        assert "shadow_ai_tool_detected" not in flags


class TestIngestSystemEventCandidateHook:
    def _base_kwargs(self, **over):
        kw = dict(
            server_name="sailpoint:Test", system_type="sailpoint", event_type="IAM_ACCESS_REQUEST_EVENT",
            event_id="evt-1", actor="jsmith@acme-corp.com", action="access_requested",
            resource="", severity="INFO", risk_flags=[], raw_payload=None, source_ip=None,
        )
        kw.update(over)
        return kw

    def test_upserts_a_candidate_when_flag_present_and_insert_is_new(self):
        with patch.object(mg.db, "is_available", return_value=True), \
             patch.object(mg.db, "get_conn") as mock_conn, \
             patch.object(mg.db, "upsert_ai_shadow_candidate") as mock_upsert:
            cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = (99,)
            mg._ingest_system_event(**self._base_kwargs(
                risk_flags=["shadow_ai_tool_detected"],
                raw_payload={"entitlement": "OPENAI_ENTERPRISE_ACCESS"},
            ))
        mock_upsert.assert_called_once_with("OpenAI", "OPENAI_ENTERPRISE_ACCESS", "jsmith@acme-corp.com")

    def test_no_candidate_upsert_when_flag_absent(self):
        with patch.object(mg.db, "is_available", return_value=True), \
             patch.object(mg.db, "get_conn") as mock_conn, \
             patch.object(mg.db, "upsert_ai_shadow_candidate") as mock_upsert:
            cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = (99,)
            mg._ingest_system_event(**self._base_kwargs(
                risk_flags=[], raw_payload={"entitlement": "PO_BUYER"},
            ))
        mock_upsert.assert_not_called()

    def test_no_candidate_upsert_when_insert_was_a_dedup_noop(self):
        """A duplicate (server_name, event_id) hits ON CONFLICT DO NOTHING —
        fetchone() returns None, meaning this isn't a genuinely new event.
        No candidate should be proposed off it a second time."""
        with patch.object(mg.db, "is_available", return_value=True), \
             patch.object(mg.db, "get_conn") as mock_conn, \
             patch.object(mg.db, "upsert_ai_shadow_candidate") as mock_upsert:
            cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = None
            mg._ingest_system_event(**self._base_kwargs(
                risk_flags=["shadow_ai_tool_detected"],
                raw_payload={"entitlement": "OPENAI_ENTERPRISE_ACCESS"},
            ))
        mock_upsert.assert_not_called()

    def test_candidate_upsert_failure_does_not_break_the_primary_insert(self):
        with patch.object(mg.db, "is_available", return_value=True), \
             patch.object(mg.db, "get_conn") as mock_conn, \
             patch.object(mg.db, "upsert_ai_shadow_candidate", side_effect=RuntimeError("boom")):
            cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = (99,)
            result = mg._ingest_system_event(**self._base_kwargs(
                risk_flags=["shadow_ai_tool_detected"],
                raw_payload={"entitlement": "OPENAI_ENTERPRISE_ACCESS"},
            ))
        assert result == 99

    def test_raw_payload_passed_to_candidate_upsert_is_plaintext_not_encrypted(self):
        """The hook must read the payload BEFORE _encrypt_sensitive_details
        runs — this is the whole reason it lives inside _ingest_system_event
        rather than a later sweep over already-encrypted stored rows."""
        with patch.object(mg.db, "is_available", return_value=True), \
             patch.object(mg.db, "get_conn") as mock_conn, \
             patch.object(mg.db, "upsert_ai_shadow_candidate") as mock_upsert, \
             patch.object(mg, "_encrypt_sensitive_details", side_effect=lambda p: {"encrypted": True}):
            cur = mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = (99,)
            mg._ingest_system_event(**self._base_kwargs(
                risk_flags=["shadow_ai_tool_detected"],
                raw_payload={"entitlement": "OPENAI_ENTERPRISE_ACCESS"},
            ))
        # If the hook had used the (now-encrypted) raw_payload, entitlement
        # would be missing entirely and no tool name could be derived.
        mock_upsert.assert_called_once_with("OpenAI", "OPENAI_ENTERPRISE_ACCESS", "jsmith@acme-corp.com")
