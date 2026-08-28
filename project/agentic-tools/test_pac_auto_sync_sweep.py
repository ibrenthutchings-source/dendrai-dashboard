"""
Tests for pac_auto_sync_sweep.py's sweep_once() orchestration.

No pytest-asyncio in this repo's test deps, so async calls are driven with
asyncio.run() directly, matching test_multi_tenant_sweeps.py's convention.
mcp_governance/pac_endpoints calls mocked as MagicMock/AsyncMock instances
passed into a single patch.object() per target — this suite's standard
pattern, adopted after a nested-patch bug elsewhere silently shadowed an
outer mock.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import mcp_governance
import pac_auto_sync_sweep as sweep
import pac_endpoints


def _repo(id_=1, owner="acme", repo_name="policies", branch="main", token="tok", last_synced_sha=None):
    return {"id": id_, "owner": owner, "repo_name": repo_name, "branch": branch,
            "token": token, "last_synced_sha": last_synced_sha}


class TestSweepOnce:
    def test_syncs_a_repo_whose_head_moved(self):
        repos = [_repo(last_synced_sha="old-sha")]
        with patch.object(mcp_governance, "_fetch_auto_sync_candidates", MagicMock(return_value=repos)), \
             patch.object(pac_endpoints, "get_branch_head_sha", AsyncMock(return_value="new-sha")), \
             patch.object(mcp_governance, "sync_pac_repo", AsyncMock(return_value={"synced": True})) as sync_mock, \
             patch.object(mcp_governance, "_update_last_synced_sha", MagicMock()) as update_mock:
            result = asyncio.run(sweep.sweep_once())
        assert result == 1
        sync_mock.assert_called_once_with(1)
        update_mock.assert_called_once_with(1, "new-sha")

    def test_skips_a_repo_whose_head_is_unchanged(self):
        repos = [_repo(last_synced_sha="same-sha")]
        with patch.object(mcp_governance, "_fetch_auto_sync_candidates", MagicMock(return_value=repos)), \
             patch.object(pac_endpoints, "get_branch_head_sha", AsyncMock(return_value="same-sha")), \
             patch.object(mcp_governance, "sync_pac_repo", AsyncMock()) as sync_mock, \
             patch.object(mcp_governance, "_update_last_synced_sha", MagicMock()) as update_mock:
            result = asyncio.run(sweep.sweep_once())
        assert result == 0
        sync_mock.assert_not_called()
        update_mock.assert_not_called()

    def test_never_synced_repo_with_no_stored_sha_syncs_once(self):
        """last_synced_sha is None (never synced) — any real head_sha counts
        as a change, same honesty-over-silence reasoning as the rest of this
        session's work: a repo that's never been synced should sync, not be
        silently skipped because None != None never evaluates true here."""
        repos = [_repo(last_synced_sha=None)]
        with patch.object(mcp_governance, "_fetch_auto_sync_candidates", MagicMock(return_value=repos)), \
             patch.object(pac_endpoints, "get_branch_head_sha", AsyncMock(return_value="first-sha")), \
             patch.object(mcp_governance, "sync_pac_repo", AsyncMock(return_value={"synced": True})) as sync_mock, \
             patch.object(mcp_governance, "_update_last_synced_sha", MagicMock()):
            result = asyncio.run(sweep.sweep_once())
        assert result == 1
        sync_mock.assert_called_once_with(1)

    def test_no_head_sha_available_skips_without_erroring(self):
        """get_branch_head_sha returns None on any GitHub/network failure —
        must skip silently, never crash the sweep over one bad repo."""
        repos = [_repo(last_synced_sha="old-sha")]
        with patch.object(mcp_governance, "_fetch_auto_sync_candidates", MagicMock(return_value=repos)), \
             patch.object(pac_endpoints, "get_branch_head_sha", AsyncMock(return_value=None)), \
             patch.object(mcp_governance, "sync_pac_repo", AsyncMock()) as sync_mock:
            result = asyncio.run(sweep.sweep_once())
        assert result == 0
        sync_mock.assert_not_called()

    def test_one_repo_failing_does_not_stop_the_rest_of_the_sweep(self):
        repos = [_repo(id_=1, last_synced_sha="old"), _repo(id_=2, last_synced_sha="old")]
        head_shas = AsyncMock(side_effect=[Exception("GitHub down"), "new-sha"])
        with patch.object(mcp_governance, "_fetch_auto_sync_candidates", MagicMock(return_value=repos)), \
             patch.object(pac_endpoints, "get_branch_head_sha", head_shas), \
             patch.object(mcp_governance, "sync_pac_repo", AsyncMock(return_value={"synced": True})) as sync_mock, \
             patch.object(mcp_governance, "_update_last_synced_sha", MagicMock()):
            result = asyncio.run(sweep.sweep_once())
        assert result == 1
        sync_mock.assert_called_once_with(2)

    def test_sync_failure_does_not_update_last_synced_sha(self):
        repos = [_repo(last_synced_sha="old-sha")]
        with patch.object(mcp_governance, "_fetch_auto_sync_candidates", MagicMock(return_value=repos)), \
             patch.object(pac_endpoints, "get_branch_head_sha", AsyncMock(return_value="new-sha")), \
             patch.object(mcp_governance, "sync_pac_repo", AsyncMock(side_effect=Exception("sync failed"))), \
             patch.object(mcp_governance, "_update_last_synced_sha", MagicMock()) as update_mock:
            result = asyncio.run(sweep.sweep_once())
        assert result == 0
        update_mock.assert_not_called()

    def test_no_candidates_is_a_clean_no_op(self):
        with patch.object(mcp_governance, "_fetch_auto_sync_candidates", MagicMock(return_value=[])), \
             patch.object(pac_endpoints, "get_branch_head_sha", AsyncMock()) as head_mock:
            result = asyncio.run(sweep.sweep_once())
        assert result == 0
        head_mock.assert_not_called()
