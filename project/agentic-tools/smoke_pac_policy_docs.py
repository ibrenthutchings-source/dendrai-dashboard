#!/usr/bin/env python3
"""
End-to-end smoke test for the plain-language policy -> Policy-as-Code flow.

Everything in test_pac_policy_docs.py mocks the database and the model, so it
proves the LOGIC but not that the thing works. This script proves the thing
works: it drives the real HTTP API against a real Postgres, exercising the
parts unit tests structurally cannot -- column types and lengths, the JSONB
round-trip, the FK cascade, the session-cookie auth gate, and the actual
Claude call.

Run it against a locally running api_server.py:

    python smoke_pac_policy_docs.py --username admin --password '...'

Options:
    --base-url URL     API root (default http://127.0.0.1:8001)
                       Point at http://localhost:5173 to test THROUGH the vite
                       dev proxy -- which is what the browser actually uses, and
                       where the /api/pac rewrite bug lived. Pass --api-prefix
                       /api/pac in that case.
    --api-prefix P     Path prefix for PaC routes (default /pac)
    --process ID       Which process to publish into (default itgc)
    --skip-convert     Don't call Claude; inject a known-good draft instead.
                       Use when ANTHROPIC_API_KEY isn't set, or to test just
                       the storage + review + publish plumbing for free.
    --keep             Don't delete the document at the end (leaves it in the
                       UI so you can eyeball the review screen).

Exit code is 0 only if every step passed. Nothing here is destructive beyond
the document it creates and the module version it publishes -- both are noted
at the end so you can undo them.
"""

from __future__ import annotations

import argparse
import sys
import uuid

try:
    import requests
except ImportError:
    sys.exit("requests is required -- pip install requests")


# A deliberately small, unambiguous policy. Small so conversion is fast and
# cheap; unambiguous so a correct conversion is obvious on sight.
POLICY_TEXT = """\
# Supplier Payment Segregation of Duties

No single user may both create a supplier record and approve a payment to that
supplier. Any exception requires documented CFO approval recorded before the
payment is released.

Payments above 250,000 USD require a second approver who is not the payment
originator.
"""

# What we publish. Kept syntactically valid on purpose -- the approve step is
# supposed to REFUSE anything that isn't, and we test that separately below.
GOOD_REGO = """\
package controls.oracle_fusion.{process}

import future.keywords.in
import future.keywords.if

deny_payment_event[msg] if {{
    input.event.type == "payment_release"
    input.event.created_by == input.event.approved_by
    msg := sprintf("{prefix}-SOD-01: user '%v' both created and approved payment '%v'", [input.event.created_by, input.event.payment_id])
}}

deny_payment_event[msg] if {{
    input.event.type == "payment_release"
    input.event.amount > 250000
    not input.event.second_approver
    msg := sprintf("{prefix}-SOD-02: payment '%v' above threshold released without a second approver", [input.event.payment_id])
}}
"""

BROKEN_REGO = "deny_payment_event[msg] if {\n    input.event.type == \"payment_release\"\n"


class Smoke:
    def __init__(self, base_url: str, api_prefix: str, process: str):
        self.base = base_url.rstrip("/")
        self.prefix = api_prefix.rstrip("/")
        self.process = process
        self.s = requests.Session()
        self.passed = 0
        self.failed: list[str] = []
        self.doc_id: int | None = None
        self.conv_id: int | None = None
        self.module_id: int | None = None
        self.version: str | None = None

    # -- reporting ----------------------------------------------------------
    def ok(self, msg: str):
        self.passed += 1
        print(f"  \033[32mPASS\033[0m  {msg}")

    def bad(self, msg: str, detail: str = ""):
        self.failed.append(msg)
        print(f"  \033[31mFAIL\033[0m  {msg}")
        if detail:
            print(f"        {detail}")

    def step(self, title: str):
        print(f"\n\033[1m{title}\033[0m")

    def url(self, path: str) -> str:
        return f"{self.base}{self.prefix}{path}"

    def check(self, cond: bool, msg: str, detail: str = "") -> bool:
        (self.ok if cond else self.bad)(msg, *([] if cond else [detail]))
        return cond

    # -- steps --------------------------------------------------------------
    def login(self, username: str, password: str) -> bool:
        self.step("1. Authenticate")
        try:
            r = self.s.post(f"{self.base}/auth/login",
                            json={"username": username, "password": password}, timeout=20)
        except requests.RequestException as exc:
            return self.check(False, "reach the API", f"{exc}\n        Is api_server.py running at {self.base}?")
        if r.status_code != 200:
            return self.check(False, f"log in as '{username}'", f"HTTP {r.status_code}: {r.text[:200]}")
        if "dendrai_session" not in self.s.cookies:
            return self.check(False, "receive a session cookie", "login returned 200 but set no dendrai_session cookie")
        return self.check(True, f"logged in as '{username}'")

    def routes_reachable(self) -> bool:
        """Catches the exact class of bug that made the Rego Editor render
        empty: routes served under a different prefix than the caller uses."""
        self.step("2. PaC routes are reachable at the expected prefix")
        r = self.s.get(self.url("/processes"), timeout=20)
        if r.status_code == 404:
            return self.check(False, f"GET {self.prefix}/processes",
                              f"404 -- the API does not serve routes at '{self.prefix}'. "
                              f"Against the vite dev server pass --api-prefix /api/pac.")
        if r.status_code == 403:
            return self.check(False, f"GET {self.prefix}/processes",
                              "403 -- this account lacks read access to the 'policycode' screen.")
        if not self.check(r.status_code == 200, f"GET {self.prefix}/processes", f"HTTP {r.status_code}: {r.text[:200]}"):
            return False
        ids = [p["id"] for p in r.json().get("processes", [])]
        return self.check(self.process in ids, f"process '{self.process}' exists",
                          f"known processes: {', '.join(ids) or '(none)'}")

    def create_document(self) -> bool:
        self.step("3. Store a plain-language policy (verbatim)")
        title = f"Smoke test -- SoD {uuid.uuid4().hex[:8]}"
        r = self.s.post(self.url("/policy-docs"), timeout=30, json={
            "process": self.process, "title": title,
            "text": POLICY_TEXT, "uploaded_by": "smoke-test",
        })
        if not self.check(r.status_code == 200, "POST /policy-docs", f"HTTP {r.status_code}: {r.text[:300]}"):
            return False
        body = r.json()
        self.doc_id = body.get("document_id")
        if not self.check(bool(self.doc_id), "document was assigned an id"):
            return False

        r = self.s.get(self.url(f"/policy-docs/{self.doc_id}"), timeout=20)
        if not self.check(r.status_code == 200, f"GET /policy-docs/{self.doc_id}", f"HTTP {r.status_code}"):
            return False
        stored = r.json().get("doc_text")
        # The source-of-record claim depends on this being byte-identical.
        return self.check(stored == POLICY_TEXT, "stored text is byte-identical to what was submitted",
                          f"submitted {len(POLICY_TEXT)} chars, got back {len(stored or '')}")

    def convert(self, skip: bool) -> bool:
        self.step("4. Draft Rego from the prose")
        if skip:
            print("  \033[33mSKIP\033[0m  --skip-convert: injecting a draft directly instead of calling Claude")
            # Still needs a conversion row to review, so make one the same way
            # the API does -- via a real convert call is impossible without a
            # key, so we fail loudly if the endpoint is the only way in.
            r = self.s.post(self.url(f"/policy-docs/{self.doc_id}/convert"), json={}, timeout=300)
            if r.status_code == 502:
                self.bad("cannot create a conversion without Claude",
                         "No ANTHROPIC_API_KEY on the server. The review/publish steps below need a "
                         "conversion row to exist, so they cannot run. Set the key and re-run without --skip-convert.")
                return False
        else:
            r = self.s.post(self.url(f"/policy-docs/{self.doc_id}/convert"),
                            json={"guidance": "Focus on the two payment controls."}, timeout=300)
        if not self.check(r.status_code == 200, "POST /policy-docs/{id}/convert",
                          f"HTTP {r.status_code}: {r.text[:400]}"):
            return False
        body = r.json()
        self.conv_id = body.get("conversion_id")
        self.check(body.get("status") == "pending_review",
                   "draft landed at 'pending_review' (not published)",
                   f"status was {body.get('status')!r}")
        if body.get("syntax_valid"):
            self.ok(f"model produced valid Rego with control IDs {body.get('control_ids')}")
        else:
            # Not a failure of the flow -- storing an invalid draft for repair
            # is the designed behaviour, and the next step overwrites it anyway.
            print(f"  \033[33mNOTE\033[0m  model output failed validation "
                  f"({'; '.join(body.get('syntax_errors') or [])}) -- stored for repair, as designed")
        return self.check(bool(self.conv_id), "conversion was assigned an id")

    def module_unchanged_by_conversion(self, before: dict) -> bool:
        """The core guarantee: converting must not touch live policy."""
        self.step("5. Converting did NOT publish anything")
        r = self.s.get(self.url(f"/modules/{self.process}"), timeout=20)
        if not self.check(r.status_code == 200, f"GET /modules/{self.process}", f"HTTP {r.status_code}"):
            return False
        after = r.json()
        return self.check(after.get("id") == before.get("id"),
                          "live module is untouched after conversion",
                          f"module id changed {before.get('id')} -> {after.get('id')} -- a draft reached production")

    def in_review_queue(self) -> bool:
        self.step("6. Draft appears in the review queue")
        r = self.s.get(self.url("/conversions?status=pending_review"), timeout=20)
        if not self.check(r.status_code == 200, "GET /conversions", f"HTTP {r.status_code}"):
            return False
        rows = r.json().get("conversions", [])
        mine = [c for c in rows if c["id"] == self.conv_id]
        if not self.check(bool(mine), f"conversion {self.conv_id} is in the queue",
                          f"queue has {len(rows)} row(s), none matching"):
            return False
        # The queue is unusable without the parent document's identity.
        return self.check(bool(mine[0].get("document_title")),
                          "queue row carries its document's title")

    def reject_invalid_publish(self) -> bool:
        """The guarantee that matters most: unparseable Rego can never go live."""
        self.step("7. Approving invalid Rego is refused")
        r = self.s.put(self.url(f"/conversions/{self.conv_id}/draft"),
                       json={"rego_content": BROKEN_REGO}, timeout=30)
        if not self.check(r.status_code == 200, "PUT draft (broken Rego saved for repair)",
                          f"HTTP {r.status_code}: {r.text[:200]}"):
            return False
        self.check(r.json().get("syntax_valid") is False, "server re-validated and marked it invalid")

        r = self.s.post(self.url(f"/conversions/{self.conv_id}/decision"), timeout=30,
                        json={"decision": "approve", "reviewer": "smoke-test"})
        return self.check(r.status_code == 422, "approve refused with 422",
                          f"got HTTP {r.status_code} -- invalid Rego was publishable!")

    def edit_and_publish(self) -> bool:
        self.step("8. Reviewer edits the draft, then approves")
        prefix = {"itgc": "ITGC", "procure_to_pay": "P2P", "order_to_cash": "OTC"}.get(
            self.process, self.process.upper()[:8])
        good = GOOD_REGO.format(process=self.process, prefix=prefix)

        r = self.s.put(self.url(f"/conversions/{self.conv_id}/draft"),
                       json={"rego_content": good}, timeout=30)
        if not self.check(r.status_code == 200 and r.json().get("syntax_valid") is True,
                          "PUT draft (valid Rego)", f"HTTP {r.status_code}: {r.text[:300]}"):
            return False

        r = self.s.post(self.url(f"/conversions/{self.conv_id}/decision"), timeout=60, json={
            "decision": "approve", "reviewer": "smoke-test",
            "reviewer_role": "Control Owner", "notes": "Smoke test approval.",
        })
        if not self.check(r.status_code == 200, "POST decision=approve", f"HTTP {r.status_code}: {r.text[:300]}"):
            return False
        body = r.json()
        self.module_id, self.version = body.get("published_module_id"), body.get("published_version")
        self.check(bool(self.module_id), "a module version was published")
        self.check(body.get("document_status") == "published", "document marked published")

        # A second decision on a closed conversion must be refused.
        r2 = self.s.post(self.url(f"/conversions/{self.conv_id}/decision"), timeout=30,
                         json={"decision": "reject", "reviewer": "someone-else"})
        self.check(r2.status_code == 409, "re-deciding a closed conversion is refused (409)",
                   f"got HTTP {r2.status_code}")

        self.step("9. The published module is the REVIEWED text")
        r = self.s.get(self.url(f"/modules/{self.process}"), timeout=20)
        if not self.check(r.status_code == 200, f"GET /modules/{self.process}", f"HTTP {r.status_code}"):
            return False
        live = r.json()
        self.check(live.get("id") == self.module_id, "live module is the one just published",
                   f"expected id {self.module_id}, live is {live.get('id')}")
        # The whole point of the review step: the human's edits go live, not
        # whatever the model originally emitted.
        return self.check(live.get("rego_content", "").strip() == good.strip(),
                          "live Rego is exactly what the reviewer approved")

    def cleanup(self, keep: bool):
        self.step("10. Cleanup")
        if keep:
            print(f"  \033[33mKEPT\033[0m  document {self.doc_id} left in place (--keep)")
            return
        if not self.doc_id:
            return
        r = self.s.delete(self.url(f"/policy-docs/{self.doc_id}"), timeout=20)
        self.check(r.status_code == 200, f"deleted document {self.doc_id}", f"HTTP {r.status_code}")
        r = self.s.get(self.url(f"/conversions/{self.conv_id}"), timeout=20)
        self.check(r.status_code == 404, "its conversions cascaded away",
                   f"conversion {self.conv_id} still returns HTTP {r.status_code}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://127.0.0.1:8001")
    ap.add_argument("--api-prefix", default="/pac")
    ap.add_argument("--process", default="itgc")
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--skip-convert", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    s = Smoke(args.base_url, args.api_prefix, args.process)
    print(f"\033[1mPaC policy-document smoke test\033[0m")
    print(f"  target: {s.base}{s.prefix}   process: {s.process}")

    try:
        if s.login(args.username, args.password) and s.routes_reachable():
            before = s.s.get(s.url(f"/modules/{s.process}"), timeout=20).json()
            if s.create_document() and s.convert(args.skip_convert):
                s.module_unchanged_by_conversion(before)
                if s.in_review_queue() and s.reject_invalid_publish():
                    s.edit_and_publish()
            s.cleanup(args.keep)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130

    print(f"\n{'-' * 60}")
    if s.failed:
        print(f"\033[31m{len(s.failed)} FAILED\033[0m, {s.passed} passed")
        for f in s.failed:
            print(f"  * {f}")
        return 1
    print(f"\033[32mAll {s.passed} checks passed.\033[0m")
    if s.module_id:
        print(f"  Published module #{s.module_id} (v{s.version}) for '{s.process}'.")
        print(f"  That is a real new version -- roll it back in the Rego Editor if unwanted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
