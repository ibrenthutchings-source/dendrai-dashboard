"""
The Linguist — evaluates sentiment drift and narrative-transactional divergence.

Analytical lens: when what people say doesn't match what they do, that gap
is itself a risk signal.

The Linguist looks for:
  - Commit messages that contradict the actual code change type (e.g., "minor fix"
    on a push that modified 500 files)
  - SAP journal entry descriptions that are suspiciously vague or overly generic
  - SailPoint access justifications that don't match the requested role's purpose
  - Keyword-based urgency / obfuscation scoring
  - Anomalous narrative patterns (boilerplate text, identical justifications)
"""

from __future__ import annotations

import re
import time
from typing import Any

from ..models.risk_intelligence import AgentEvaluation, AgentVerdict
from ..models.uro import EventType, SourceSystem, URO
from .base import BaseAgent


# ── Keyword lexicons ──────────────────────────────────────────────────────────

# Words that appear in legitimate change narratives but are high-risk in context
_OBFUSCATION_PATTERNS = re.compile(
    r"\b(?:misc(?:ellaneous)?|various|other|general|routine|regular|per request"
    r"|as discussed|see email|n/?a|tbd|todo|test|temp(?:orary)?|quick.?fix"
    r"|urgent|emergency|bypass|exception|override|manual|special)\b",
    re.IGNORECASE,
)

# Generic boilerplate that indicates copy-paste or automation without human review
_BOILERPLATE_PATTERNS = re.compile(
    r"\b(?:auto.?generated|system.?generated|automated|script|bot|pipeline)\b",
    re.IGNORECASE,
)

_URGENCY_ESCALATORS = re.compile(
    r"\b(?:immediately|asap|critical|must.?have|no.?time|deadline|ceo|cfo|board|audit)\b",
    re.IGNORECASE,
)

# GitHub commit message signals
_COMMIT_SUPPRESSION = re.compile(
    r"\b(?:skip.?ci|no.?verify|force|wip|fixup|squash|revert|hotfix|emergency)\b",
    re.IGNORECASE,
)


class TheLinguist(BaseAgent):
    name = "The Linguist"

    async def evaluate(self, uro: URO) -> AgentEvaluation:
        start = time.monotonic()
        evidence: dict[str, Any] = {}
        signals: list[str] = []

        cp = uro.conformed_payload
        indicators = cp.risk_indicators if cp else {}

        # ── Route to source-specific analysis ────────────────────────────────
        if uro.source_system == SourceSystem.SAP:
            signals, evidence = self._analyse_sap(uro, indicators, signals, evidence)
        elif uro.source_system == SourceSystem.GITHUB:
            signals, evidence = self._analyse_github(uro, indicators, signals, evidence)
        elif uro.source_system == SourceSystem.SAILPOINT:
            signals, evidence = self._analyse_sailpoint(uro, indicators, signals, evidence)
        else:
            signals, evidence = self._analyse_generic(uro, indicators, signals, evidence)

        verdict, confidence, risk_delta = self._derive_verdict(signals)

        elapsed_ms = int((time.monotonic() - start) * 1000)

        return AgentEvaluation(
            agent_name=self.name,
            verdict=verdict,
            confidence=confidence,
            risk_delta=risk_delta,
            reasoning=self._build_reasoning(signals, uro),
            evidence=evidence,
            evaluation_ms=elapsed_ms,
        )

    # ── Source-specific analysers ─────────────────────────────────────────────

    def _analyse_sap(self, uro: URO, indicators: dict, signals: list, evidence: dict):
        raw = uro.raw_payload.content
        narrative = str(
            raw.get("BKTXT") or raw.get("SGTXT") or raw.get("narrative") or ""
        ).strip()
        evidence["narrative"] = narrative

        if not narrative:
            signals.append("SAP journal entry has no posting text (BKTXT/SGTXT) — unexplained transaction")
            evidence["missing_narrative"] = True
        else:
            self._score_narrative(narrative, signals, evidence)

        # Narrative vs. amount divergence: tiny description on a large amount
        amount = indicators.get("amount") or 0
        if amount and float(amount) > 100_000 and len(narrative) < 10:
            signals.append(
                f"Narrative length ({len(narrative)} chars) is suspiciously brief "
                f"for a ${float(amount):,.0f} transaction"
            )

        return signals, evidence

    def _analyse_github(self, uro: URO, indicators: dict, signals: list, evidence: dict):
        raw = uro.raw_payload.content
        commits = raw.get("commits", [])
        messages = [c.get("message", "") for c in commits if c.get("message")]
        files_changed = sum(
            len(c.get("added", [])) + len(c.get("modified", [])) + len(c.get("removed", []))
            for c in commits
        )
        evidence["commit_count"] = len(commits)
        evidence["files_changed"] = files_changed

        for msg in messages[:5]:
            self._score_narrative(msg, signals, evidence, context="commit message")
            # Tiny message on large change
            if len(msg) < 15 and files_changed > 20:
                signals.append(
                    f"Commit message '{msg}' is too brief ({len(msg)} chars) for a "
                    f"{files_changed}-file change — potential rubber-stamp bypass"
                )
            # Suppression keywords
            if _COMMIT_SUPPRESSION.search(msg):
                match = _COMMIT_SUPPRESSION.search(msg)
                signals.append(
                    f"Commit message contains CI/review suppression keyword: "
                    f"'{match.group()}' in '{msg[:60]}'"
                )

        return signals, evidence

    def _analyse_sailpoint(self, uro: URO, indicators: dict, signals: list, evidence: dict):
        raw = uro.raw_payload.content
        justification = str(
            raw.get("justification") or raw.get("requestJustification") or ""
        ).strip()
        evidence["justification"] = justification

        if not justification:
            signals.append("Access request has no justification text — mandatory field blank")
            evidence["missing_justification"] = True
        else:
            self._score_narrative(justification, signals, evidence, context="access justification")
            # Very short justification for a privilege escalation
            if uro.event_type == EventType.PRIVILEGE_ESCALATION and len(justification) < 20:
                signals.append(
                    f"Privilege escalation justification too brief ({len(justification)} chars): "
                    f"'{justification}'"
                )

        return signals, evidence

    def _analyse_generic(self, uro: URO, indicators: dict, signals: list, evidence: dict):
        narrative = str(indicators.get("narrative") or indicators.get("description") or "")
        if narrative:
            self._score_narrative(narrative, signals, evidence)
        return signals, evidence

    # ── Core narrative scorer ─────────────────────────────────────────────────

    def _score_narrative(
        self, text: str, signals: list, evidence: dict, context: str = "narrative"
    ) -> None:
        obfuscation_hits = _OBFUSCATION_PATTERNS.findall(text)
        boilerplate_hits = _BOILERPLATE_PATTERNS.findall(text)
        urgency_hits     = _URGENCY_ESCALATORS.findall(text)

        if obfuscation_hits:
            words = list({w.lower() for w in obfuscation_hits})[:3]
            signals.append(
                f"{context.capitalize()} contains obfuscation/vague language: {words} — "
                f"'{text[:80]}'"
            )
            evidence["obfuscation_hits"] = len(obfuscation_hits)

        if boilerplate_hits:
            signals.append(
                f"{context.capitalize()} appears auto-generated/boilerplate: {boilerplate_hits[:2]}"
            )
            evidence["boilerplate_hits"] = len(boilerplate_hits)

        if urgency_hits:
            signals.append(
                f"{context.capitalize()} contains urgency-escalating language: "
                f"{list({w.lower() for w in urgency_hits})[:3]} — often precedes bypass attempts"
            )
            evidence["urgency_hits"] = len(urgency_hits)

    # ── Verdict derivation ────────────────────────────────────────────────────

    def _derive_verdict(
        self, signals: list[str]
    ) -> tuple[AgentVerdict, float, float]:
        if not signals:
            return AgentVerdict.CLEAR, 0.60, -0.03

        severity_count = len(signals)
        # "bypass", "suppression", "missing_narrative", "privilege escalation justification" → ESCALATE
        escalation_keywords = ("bypass", "suppression", "blank", "missing", "Privilege escalation")
        if any(any(kw.lower() in s.lower() for kw in escalation_keywords) for s in signals):
            confidence = min(0.90, 0.58 + severity_count * 0.07)
            return AgentVerdict.ESCALATE, confidence, +0.12

        confidence = min(0.80, 0.45 + severity_count * 0.08)
        return AgentVerdict.MONITOR, confidence, +0.04

    @staticmethod
    def _build_reasoning(signals: list[str], uro: URO) -> str:
        if not signals:
            return (
                f"Narrative analysis of '{uro.event_type.value}' from actor '{uro.actor_id}' "
                "found no linguistic drift, obfuscation patterns, or narrative-transactional "
                "divergence. Narrative content is consistent with expected patterns."
            )
        bullets = "\n  • ".join(signals)
        return (
            f"Linguistic analysis of '{uro.event_type.value}' (source: {uro.source_system.value}) "
            f"from actor '{uro.actor_id}' identified {len(signals)} narrative signal(s):\n  • {bullets}"
        )
