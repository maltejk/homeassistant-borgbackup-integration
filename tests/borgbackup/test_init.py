#
# Tests for BorgBackup – __init__.py
#
# Unit tests for BorgAddonClient HTTP calls and entry setup/teardown.
#
# To run: pytest tests/borgbackup/test_init.py
#

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)

from custom_components.borgbackup import (
    AddonAuthError,
    AddonConnectionError,
    AddonError,
    AddonNotFoundError,
    AddonServerError,
    BorgAddonClient,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.borgbackup.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_PORT,
    DOMAIN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(data: dict, options: dict | None = None) -> MagicMock:
    entry = MagicMock()
    entry.data = data
    entry.options = options or {}
    entry.title = "BorgBackup (local-borgbackup)"
    entry.entry_id = "test_entry_id"
    entry.async_on_unload = MagicMock()
    entry.async_on_state_change = MagicMock(return_value=lambda: None)
    return entry


def _make_hass() -> MagicMock:
    hass = MagicMock()
    hass.data = {}
    hass.config.config_dir = "/tmp/ha_test"
    hass.config_entries.async_loaded_entries = MagicMock(return_value=[])
    return hass


def _make_mock_response(
    status: int, json_data=None, text_data: str = ""
) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text_data)
    if json_data is not None:
        resp.json = AsyncMock(return_value=json_data)
    return resp


# ---------------------------------------------------------------------------
# BorgAddonClient – construction and properties
# ---------------------------------------------------------------------------


class TestBorgAddonClientInit:
    """Tests for BorgAddonClient construction."""

    def test_stores_parameters(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        assert client._host == "local-borgbackup"
        assert client._port == 8774
        assert client._api_key == "test-api-key-abc123"

    def test_base_url(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        assert client._base_url == "http://local-borgbackup:8774"

    def test_headers_contain_bearer_token(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        assert client._headers()["Authorization"] == "Bearer test-api-key-abc123"


# ---------------------------------------------------------------------------
# BorgAddonClient – _raise_for_status
# ---------------------------------------------------------------------------


class TestRaiseForStatus:
    """Tests for the status-to-exception mapping helper."""

    def test_401_raises_auth_error(self, sample_config):
        client = BorgAddonClient(hass=_make_hass(), **sample_config)
        with pytest.raises(AddonAuthError):
            client._raise_for_status(401)

    def test_403_raises_auth_error(self, sample_config):
        client = BorgAddonClient(hass=_make_hass(), **sample_config)
        with pytest.raises(AddonAuthError):
            client._raise_for_status(403)

    def test_404_raises_not_found(self, sample_config):
        client = BorgAddonClient(hass=_make_hass(), **sample_config)
        with pytest.raises(AddonNotFoundError):
            client._raise_for_status(404)

    def test_502_raises_connection_error(self, sample_config):
        client = BorgAddonClient(hass=_make_hass(), **sample_config)
        with pytest.raises(AddonConnectionError):
            client._raise_for_status(502)

    def test_500_raises_server_error(self, sample_config):
        client = BorgAddonClient(hass=_make_hass(), **sample_config)
        with pytest.raises(AddonServerError):
            client._raise_for_status(500)

    def test_200_does_not_raise(self, sample_config):
        client = BorgAddonClient(hass=_make_hass(), **sample_config)
        client._raise_for_status(200)  # should not raise

    def test_201_does_not_raise(self, sample_config):
        client = BorgAddonClient(hass=_make_hass(), **sample_config)
        client._raise_for_status(201)  # should not raise


# ---------------------------------------------------------------------------
# BorgAddonClient – health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """Tests for BorgAddonClient.health_check."""

    async def test_success(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_resp = _make_mock_response(200)
        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_resp)
        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            await client.health_check()  # should not raise

    async def test_401_raises_auth_error(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_resp = _make_mock_response(401, text_data="Unauthorized")
        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_resp)
        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            with pytest.raises(AddonAuthError):
                await client.health_check()

    async def test_403_raises_auth_error(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_resp = _make_mock_response(403, text_data="Forbidden")
        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_resp)
        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            with pytest.raises(AddonAuthError):
                await client.health_check()

    async def test_aiohttp_error_raises_connection_error(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_session = MagicMock()
        mock_session.get = AsyncMock(side_effect=aiohttp.ClientError("no route"))
        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            with pytest.raises(AddonConnectionError):
                await client.health_check()

    async def test_500_raises_server_error(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_resp = _make_mock_response(500, text_data="Internal Server Error")
        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_resp)
        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            with pytest.raises(AddonServerError):
                await client.health_check()


# ---------------------------------------------------------------------------
# BorgAddonClient – list_backups
# ---------------------------------------------------------------------------


class TestListBackups:
    """Tests for BorgAddonClient.list_backups."""

    async def test_success_returns_list(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        backups = [{"backup_id": "b1", "name": "Backup 1"}]
        mock_resp = _make_mock_response(200, json_data=backups)
        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_resp)
        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            result = await client.list_backups()
        assert result == backups

    async def test_auth_error_raised_on_401(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_resp = _make_mock_response(401, text_data="Unauthorized")
        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_resp)
        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            with pytest.raises(AddonAuthError):
                await client.list_backups()

    async def test_network_error_raises_connection_error(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_session = MagicMock()
        mock_session.get = AsyncMock(side_effect=aiohttp.ClientError("timeout"))
        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            with pytest.raises(AddonConnectionError):
                await client.list_backups()


# ---------------------------------------------------------------------------
# BorgAddonClient – upload_backup
# ---------------------------------------------------------------------------


class TestUploadBackup:
    """Tests for BorgAddonClient.upload_backup."""

    async def test_success_posts_with_headers(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_resp = _make_mock_response(201)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=mock_resp)

        async def _stream():
            yield b"tar-data"

        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            await client.upload_backup(
                backup_id="bkp1",
                backup_meta='{"backup_id": "bkp1"}',
                open_stream=lambda: _stream(),
            )

        mock_session.post.assert_awaited_once()
        call_kwargs = mock_session.post.call_args
        assert "X-Backup-Id" in call_kwargs[1]["headers"]
        assert call_kwargs[1]["headers"]["X-Backup-Id"] == "bkp1"
        assert "X-Backup-Meta" in call_kwargs[1]["headers"]

    async def test_network_error_raises_connection_error(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(side_effect=aiohttp.ClientError("disconnected"))

        async def _stream():
            yield b"x"

        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            with pytest.raises(AddonConnectionError):
                await client.upload_backup(
                    backup_id="bkp1",
                    backup_meta="{}",
                    open_stream=lambda: _stream(),
                )

    async def test_timeout_error_raises_connection_error(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(side_effect=TimeoutError("stalled"))

        async def _stream():
            yield b"x"

        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            with pytest.raises(AddonConnectionError):
                await client.upload_backup(
                    backup_id="bkp1",
                    backup_meta="{}",
                    open_stream=lambda: _stream(),
                )

    async def test_uses_transfer_timeout_with_no_total_deadline(self, sample_config):
        """A large upload must not be killed by aiohttp's 300s default."""
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_resp = _make_mock_response(201)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=mock_resp)

        async def _stream():
            yield b"x"

        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            await client.upload_backup(
                backup_id="bkp1",
                backup_meta="{}",
                open_stream=lambda: _stream(),
            )

        timeout = mock_session.post.call_args[1]["timeout"]
        assert timeout.total is None


# ---------------------------------------------------------------------------
# BorgAddonClient – download_backup
# ---------------------------------------------------------------------------


class TestDownloadBackup:
    """Tests for BorgAddonClient.download_backup."""

    async def test_success_returns_async_iterator(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_resp = MagicMock()
        mock_resp.status = 200

        async def _chunks(n):
            for chunk in [b"chunk1", b"chunk2"]:
                yield chunk

        mock_resp.content = MagicMock()
        mock_resp.content.iter_chunked = MagicMock(side_effect=_chunks)
        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_resp)

        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            result = await client.download_backup("bkp1")
        assert result is not None

    async def test_404_raises_not_found(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_resp = _make_mock_response(404, text_data="Not Found")
        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_resp)
        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            with pytest.raises(AddonNotFoundError):
                await client.download_backup("missing")

    async def test_timeout_error_raises_connection_error(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_session = MagicMock()
        mock_session.get = AsyncMock(side_effect=TimeoutError("stalled"))
        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            with pytest.raises(AddonConnectionError):
                await client.download_backup("bkp1")

    async def test_uses_transfer_timeout_with_no_total_deadline(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.content = MagicMock()
        mock_resp.content.iter_chunked = MagicMock(side_effect=lambda n: iter(()))
        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_resp)

        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            await client.download_backup("bkp1")

        timeout = mock_session.get.call_args[1]["timeout"]
        assert timeout.total is None

    async def test_release_and_error_translation_on_stream_failure(
        self, sample_config
    ):
        """A dropped connection mid-download must release resp and raise
        AddonConnectionError instead of leaking a raw aiohttp exception."""
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_resp = MagicMock()
        mock_resp.status = 200

        async def _chunks(n):
            yield b"partial"
            raise aiohttp.ClientPayloadError("connection reset")

        mock_resp.content = MagicMock()
        mock_resp.content.iter_chunked = MagicMock(side_effect=_chunks)
        mock_resp.release = MagicMock()
        mock_session = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_resp)

        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            stream = await client.download_backup("bkp1")
            with pytest.raises(AddonConnectionError):
                async for _chunk in stream:
                    pass

        mock_resp.release.assert_called_once()


# ---------------------------------------------------------------------------
# BorgAddonClient – delete_backup
# ---------------------------------------------------------------------------


class TestDeleteBackup:
    """Tests for BorgAddonClient.delete_backup."""

    async def test_success_no_raise(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_resp = _make_mock_response(204)
        mock_session = MagicMock()
        mock_session.delete = AsyncMock(return_value=mock_resp)
        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            await client.delete_backup("bkp1")  # should not raise

    async def test_404_raises_not_found(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_resp = _make_mock_response(404, text_data="Not Found")
        mock_session = MagicMock()
        mock_session.delete = AsyncMock(return_value=mock_resp)
        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            with pytest.raises(AddonNotFoundError):
                await client.delete_backup("missing")

    async def test_network_error_raises_connection_error(self, sample_config):
        hass = _make_hass()
        client = BorgAddonClient(hass=hass, **sample_config)
        mock_session = MagicMock()
        mock_session.delete = AsyncMock(side_effect=aiohttp.ClientError("gone"))
        with patch(
            "custom_components.borgbackup.async_get_clientsession",
            return_value=mock_session,
        ):
            with pytest.raises(AddonConnectionError):
                await client.delete_backup("bkp1")


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    async def test_success(self, sample_config):
        hass = _make_hass()
        entry = _make_entry(sample_config)
        with patch(
            "custom_components.borgbackup.BorgAddonClient.health_check",
            new_callable=AsyncMock,
        ):
            result = await async_setup_entry(hass, entry)
        assert result is True
        assert entry.runtime_data is not None

    async def test_auth_error_raises_config_entry_auth_failed(self, sample_config):
        hass = _make_hass()
        entry = _make_entry(sample_config)
        with patch(
            "custom_components.borgbackup.BorgAddonClient.health_check",
            side_effect=AddonAuthError("bad key"),
        ):
            with pytest.raises(ConfigEntryAuthFailed):
                await async_setup_entry(hass, entry)

    async def test_connection_error_raises_config_entry_not_ready(self, sample_config):
        hass = _make_hass()
        entry = _make_entry(sample_config)
        with patch(
            "custom_components.borgbackup.BorgAddonClient.health_check",
            side_effect=AddonConnectionError("unreachable"),
        ):
            with pytest.raises(ConfigEntryNotReady):
                await async_setup_entry(hass, entry)

    async def test_server_error_raises_config_entry_error(self, sample_config):
        hass = _make_hass()
        entry = _make_entry(sample_config)
        with patch(
            "custom_components.borgbackup.BorgAddonClient.health_check",
            side_effect=AddonServerError("500 Internal Server Error"),
        ):
            with pytest.raises(ConfigEntryError):
                await async_setup_entry(hass, entry)


# ---------------------------------------------------------------------------
# async_unload_entry
# ---------------------------------------------------------------------------


class TestAsyncUnloadEntry:
    """Tests for async_unload_entry."""

    async def test_returns_true_without_teardown(self, sample_config):
        hass = _make_hass()
        entry = _make_entry(sample_config)
        entry.runtime_data = MagicMock()  # no teardown expected
        result = await async_unload_entry(hass, entry)
        assert result is True
        # Add-on is external; no teardown should have been called.
        entry.runtime_data.teardown = AsyncMock()
        entry.runtime_data.teardown.assert_not_called()

    # async_get_backup_agents / async_register_backup_agents_listener were
    # removed from this module (dead code — HA's backup platform loader only
    # ever called the copies in backup.py); their tests live in
    # tests/borgbackup/test_backup.py::TestAgentModuleHelpers.


