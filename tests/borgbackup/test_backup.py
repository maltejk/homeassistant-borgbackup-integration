#
# Tests for BorgBackup – backup.py
#
# Unit tests for BorgBackupAgent: listing, downloading, uploading, deleting
# backups, cache behaviour, and the error-handling decorator.
#
# To run: pytest tests/borgbackup/test_backup.py
#

from __future__ import annotations

import json
from time import monotonic
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.backup import BackupNotFound

from custom_components.borgbackup.backup import (
    LISTING_CACHE_SECONDS,
    BorgBackupAgent,
    _wrap_addon_errors,
    async_get_backup_agents,
    async_register_backup_agents_listener,
)
from custom_components.borgbackup import (
    AddonAuthError,
    AddonConnectionError,
    AddonError,
    AddonNotFoundError,
)
from custom_components.borgbackup.const import (
    AGENT_LISTENER_KEY,
    DOMAIN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(
    mock_addon_client,
    options: dict | None = None,
) -> BorgBackupAgent:
    """Build a BorgBackupAgent with a mocked config entry."""
    entry = MagicMock()
    entry.runtime_data = mock_addon_client
    entry.data = {"host": "local-borgbackup", "port": 8774, "api_key": "test-key"}
    entry.options = options or {}
    entry.title = "Test BorgBackup"
    entry.entry_id = "entry_abc"
    agent = BorgBackupAgent.__new__(BorgBackupAgent)
    agent._client = mock_addon_client
    agent._entry = entry
    agent.name = entry.title
    agent.unique_id = entry.entry_id
    agent._listing = {}
    agent._listing_valid_until = 0.0
    return agent


def _make_backup(backup_id: str = "bkp1") -> MagicMock:
    backup = MagicMock()
    backup.backup_id = backup_id
    backup.as_dict.return_value = {"backup_id": backup_id, "name": "Test"}
    return backup

# ---------------------------------------------------------------------------
# _wrap_addon_errors decorator
# ---------------------------------------------------------------------------


class TestWrapAddonErrors:
    """Tests for AddonError → BackupAgentError translation."""

    async def test_success_passes_through(self):
        @_wrap_addon_errors
        async def fn():
            return 42

        assert await fn() == 42

    async def test_not_found_raises_backup_not_found(self):
        @_wrap_addon_errors
        async def fn():
            raise AddonNotFoundError("missing")

        with pytest.raises(BackupNotFound):
            await fn()

    async def test_auth_error_translated(self):
        from homeassistant.components.backup import BackupAgentError

        @_wrap_addon_errors
        async def fn():
            raise AddonAuthError("bad key")

        with pytest.raises(BackupAgentError):
            await fn()

    async def test_connection_error_translated(self):
        from homeassistant.components.backup import BackupAgentError

        @_wrap_addon_errors
        async def fn():
            raise AddonConnectionError("unreachable")

        with pytest.raises(BackupAgentError):
            await fn()

    async def test_generic_addon_error_translated(self):
        from homeassistant.components.backup import BackupAgentError

        @_wrap_addon_errors
        async def fn():
            raise AddonError("boom")

        with pytest.raises(BackupAgentError):
            await fn()

    async def test_non_addon_error_propagates(self):
        @_wrap_addon_errors
        async def fn():
            raise ValueError("unrelated")

        with pytest.raises(ValueError):
            await fn()


# ---------------------------------------------------------------------------
# BorgBackupAgent – listing cache
# ---------------------------------------------------------------------------


class TestListingCache:
    """Tests for the 300-second cache and _drop_cache."""

    async def test_list_backups_returns_parsed_backups(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        mock_addon_client.list_backups.return_value = [
            {"backup_id": "b1", "name": "Backup 1"}
        ]

        with patch(
            "custom_components.borgbackup.backup.AgentBackup.from_dict",
            return_value=MagicMock(backup_id="b1"),
        ):
            backups = await agent.async_list_backups()

        assert len(backups) == 1

    async def test_cache_is_used_on_second_call(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        mock_addon_client.list_backups.return_value = []

        await agent.async_list_backups()
        await agent.async_list_backups()

        assert mock_addon_client.list_backups.call_count == 1

    async def test_drop_cache_forces_refetch(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        mock_addon_client.list_backups.return_value = []

        await agent.async_list_backups()
        agent._drop_cache()
        await agent.async_list_backups()

        assert mock_addon_client.list_backups.call_count == 2

    async def test_invalid_metadata_is_skipped(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        # AgentBackup.from_dict will raise KeyError for missing required fields
        mock_addon_client.list_backups.return_value = [{"invalid": "data"}]

        with patch(
            "custom_components.borgbackup.backup.AgentBackup.from_dict",
            side_effect=KeyError("backup_id"),
        ):
            backups = await agent.async_list_backups()

        assert backups == []

    async def test_cache_expires_after_listing_cache_seconds(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        mock_addon_client.list_backups.return_value = []

        await agent.async_list_backups()
        # Simulate expiry by setting the timestamp into the past.
        agent._listing_valid_until = monotonic() - 1

        await agent.async_list_backups()
        assert mock_addon_client.list_backups.call_count == 2


# ---------------------------------------------------------------------------
# BorgBackupAgent – get backup
# ---------------------------------------------------------------------------


class TestGetBackup:
    """Tests for async_get_backup."""

    async def test_found(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        mock_backup = MagicMock(backup_id="b1")
        agent._listing = {"b1": mock_backup}
        agent._listing_valid_until = monotonic() + 999

        result = await agent.async_get_backup("b1")
        assert result is mock_backup

    async def test_not_found_raises(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        agent._listing = {}
        agent._listing_valid_until = monotonic() + 999
        mock_addon_client.list_backups.return_value = []

        with pytest.raises(BackupNotFound):
            await agent.async_get_backup("missing")


# ---------------------------------------------------------------------------
# BorgBackupAgent – upload
# ---------------------------------------------------------------------------


class TestUploadBackup:
    """Tests for async_upload_backup."""

    async def test_calls_client_upload_with_correct_id(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        backup = _make_backup("bkp42")

        async def _stream():
            yield b"data"

        await agent.async_upload_backup(open_stream=_stream, backup=backup)

        mock_addon_client.upload_backup.assert_awaited_once()
        call_kwargs = mock_addon_client.upload_backup.call_args[1]
        assert call_kwargs["backup_id"] == "bkp42"

    async def test_meta_json_contains_backup_dict(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        backup = _make_backup("bkp99")
        backup.as_dict.return_value = {"backup_id": "bkp99", "name": "My Backup"}

        async def _stream():
            yield b"data"

        await agent.async_upload_backup(open_stream=_stream, backup=backup)

        meta_json = mock_addon_client.upload_backup.call_args[1]["backup_meta"]
        parsed = json.loads(meta_json)
        assert parsed["backup_id"] == "bkp99"

    async def test_cache_is_dropped_after_upload(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        backup = _make_backup("bkp1")
        agent._listing = {"old": MagicMock()}
        agent._listing_valid_until = monotonic() + 999

        async def _stream():
            yield b"x"

        await agent.async_upload_backup(open_stream=_stream, backup=backup)
        assert agent._listing == {}
        assert agent._listing_valid_until == 0.0


# ---------------------------------------------------------------------------
# BorgBackupAgent – download
# ---------------------------------------------------------------------------


class TestDownloadBackup:
    """Tests for async_download_backup."""

    async def test_calls_client_download(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        agent._listing = {"b1": MagicMock(backup_id="b1")}
        agent._listing_valid_until = monotonic() + 999

        async def _iter():
            yield b"bytes"

        mock_addon_client.download_backup.return_value = _iter()

        result = await agent.async_download_backup("b1")
        mock_addon_client.download_backup.assert_awaited_once_with("b1")
        assert result is not None

    async def test_not_found_raises(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        agent._listing = {}
        agent._listing_valid_until = monotonic() + 999
        mock_addon_client.list_backups.return_value = []

        with pytest.raises(BackupNotFound):
            await agent.async_download_backup("missing")

    async def test_error_during_stream_is_translated(self, mock_addon_client):
        """AddonError raised while iterating the stream (not while obtaining
        it) must still become a BackupAgentError — _wrap_addon_errors alone
        doesn't cover this because it only guards the initial call."""
        from homeassistant.components.backup import BackupAgentError

        agent = _make_agent(mock_addon_client)
        agent._listing = {"b1": MagicMock(backup_id="b1")}
        agent._listing_valid_until = monotonic() + 999

        async def _iter():
            yield b"partial"
            raise AddonConnectionError("dropped")

        mock_addon_client.download_backup.return_value = _iter()

        stream = await agent.async_download_backup("b1")
        with pytest.raises(BackupAgentError):
            async for _chunk in stream:
                pass


# ---------------------------------------------------------------------------
# BorgBackupAgent – delete
# ---------------------------------------------------------------------------


class TestDeleteBackup:
    """Tests for async_delete_backup."""

    async def test_calls_client_delete(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        agent._listing = {"b1": MagicMock(backup_id="b1")}
        agent._listing_valid_until = monotonic() + 999

        await agent.async_delete_backup("b1")
        mock_addon_client.delete_backup.assert_awaited_once_with("b1")

    async def test_cache_dropped_after_delete(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        agent._listing = {"b1": MagicMock(backup_id="b1")}
        agent._listing_valid_until = monotonic() + 999

        await agent.async_delete_backup("b1")
        assert agent._listing == {}
        assert agent._listing_valid_until == 0.0

    async def test_not_found_raises(self, mock_addon_client):
        agent = _make_agent(mock_addon_client)
        agent._listing = {}
        agent._listing_valid_until = monotonic() + 999
        mock_addon_client.list_backups.return_value = []

        with pytest.raises(BackupNotFound):
            await agent.async_delete_backup("missing")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestAgentModuleHelpers:
    """Tests for async_get_backup_agents and async_register_backup_agents_listener."""

    async def test_get_agents_returns_one_per_entry(self):
        hass = MagicMock()
        hass.data = {}
        entry = MagicMock()
        entry.title = "BorgBackup"
        entry.entry_id = "e1"
        entry.runtime_data = MagicMock()
        entry.options = {}
        hass.config_entries.async_loaded_entries.return_value = [entry]

        agents = await async_get_backup_agents(hass)
        assert len(agents) == 1

    def test_listener_subscribe_and_unsubscribe(self):
        hass = MagicMock()
        hass.data = {}
        cb = MagicMock()

        unsub = async_register_backup_agents_listener(hass, listener=cb)
        assert cb in hass.data[AGENT_LISTENER_KEY]
        unsub()
        assert AGENT_LISTENER_KEY not in hass.data
