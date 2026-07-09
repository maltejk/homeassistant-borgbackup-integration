#
# Tests for BorgBackup – config_flow.py
#
# Unit tests for the config flow: hassio discovery, manual user step, reauth,
# reconfigure, and error mapping in _try_connect.
#
# To run: pytest tests/borgbackup/test_config_flow.py
#

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.borgbackup import AddonAuthError, AddonConnectionError, AddonError
from custom_components.borgbackup.config_flow import BorgConfigFlow
from custom_components.borgbackup.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_PORT,
    DOMAIN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hass() -> MagicMock:
    hass = MagicMock()
    hass.data = {}
    hass.config.config_dir = "/tmp/ha_test"
    hass.config_entries = MagicMock()
    hass.config_entries.flow = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])
    return hass


def _make_flow(hass: MagicMock | None = None) -> BorgConfigFlow:
    """Create a BorgConfigFlow without going through HA's flow manager."""
    flow = BorgConfigFlow.__new__(BorgConfigFlow)
    flow.hass = hass or _make_hass()
    flow._hassio_discovery = {}
    flow._abort_if_unique_id_configured = MagicMock()
    flow._async_abort_entries_match = MagicMock()
    flow.async_create_entry = MagicMock(
        side_effect=lambda title, data: {"title": title, "data": data}
    )
    flow.async_show_form = MagicMock(
        side_effect=lambda **kw: {"type": "form", **kw}
    )
    flow.async_update_reload_and_abort = MagicMock(
        side_effect=lambda *a, **kw: {"type": "abort", "reason": "reconfigured"}
    )
    flow.add_suggested_values_to_schema = MagicMock(
        side_effect=lambda schema, values: schema
    )
    return flow


_GOOD_INPUT = {
    CONF_HOST: "local-borgbackup",
    CONF_PORT: 8774,
    CONF_API_KEY: "test-api-key-abc123",
}


# ---------------------------------------------------------------------------
# BorgConfigFlow – async_step_hassio / async_step_hassio_confirm
# ---------------------------------------------------------------------------


class TestHassioStep:
    """Tests for Supervisor auto-discovery flow."""

    async def test_hassio_step_proceeds_to_confirm(self):
        flow = _make_flow()
        discovery_info = MagicMock()
        discovery_info.config = {
            CONF_HOST: "local-borgbackup",
            CONF_PORT: 8774,
            CONF_API_KEY: "auto-key",
        }

        with patch.object(flow, "async_step_hassio_confirm", new=AsyncMock(return_value={"type": "form"})) as mock_confirm:
            await flow.async_step_hassio(discovery_info)
            mock_confirm.assert_awaited_once()

    async def test_hassio_confirm_shows_form_without_input(self):
        flow = _make_flow()
        flow._hassio_discovery = {
            CONF_HOST: "local-borgbackup",
            CONF_PORT: 8774,
            CONF_API_KEY: "auto-key",
        }

        result = await flow.async_step_hassio_confirm(user_input=None)
        assert result["type"] == "form"
        assert result["step_id"] == "hassio_confirm"

    async def test_hassio_confirm_success_creates_entry(self):
        flow = _make_flow()
        flow._hassio_discovery = {
            CONF_HOST: "local-borgbackup",
            CONF_PORT: 8774,
            CONF_API_KEY: "auto-key",
        }

        with patch(
            "custom_components.borgbackup.config_flow.BorgAddonClient.health_check",
            new_callable=AsyncMock,
        ):
            result = await flow.async_step_hassio_confirm(user_input={})

        assert "data" in result
        assert result["data"][CONF_HOST] == "local-borgbackup"
        assert result["data"][CONF_API_KEY] == "auto-key"

    async def test_hassio_confirm_duplicate_aborts(self):
        from homeassistant.data_entry_flow import AbortFlow

        flow = _make_flow()
        flow._hassio_discovery = {
            CONF_HOST: "local-borgbackup",
            CONF_PORT: 8774,
            CONF_API_KEY: "auto-key",
        }
        flow._async_abort_entries_match = MagicMock(
            side_effect=AbortFlow("already_configured")
        )
        with pytest.raises(AbortFlow):
            await flow.async_step_hassio_confirm(user_input={})
        flow._async_abort_entries_match.assert_called_once()

    async def test_hassio_confirm_auth_error_shows_form(self):
        flow = _make_flow()
        flow._hassio_discovery = {
            CONF_HOST: "local-borgbackup",
            CONF_PORT: 8774,
            CONF_API_KEY: "bad-key",
        }

        with patch(
            "custom_components.borgbackup.config_flow.BorgAddonClient.health_check",
            side_effect=AddonAuthError("bad key"),
        ):
            result = await flow.async_step_hassio_confirm(user_input={})

        assert result["type"] == "form"
        assert result["errors"]


# ---------------------------------------------------------------------------
# BorgConfigFlow – async_step_user
# ---------------------------------------------------------------------------


class TestUserStep:
    """Tests for the initial manual setup step."""

    async def test_shows_form_without_input(self):
        flow = _make_flow()
        result = await flow.async_step_user(user_input=None)
        assert result["type"] == "form"
        assert result["step_id"] == "user"

    async def test_success_creates_entry(self):
        flow = _make_flow()
        with patch(
            "custom_components.borgbackup.config_flow.BorgAddonClient.health_check",
            new_callable=AsyncMock,
        ):
            result = await flow.async_step_user(user_input=_GOOD_INPUT)

        assert "data" in result
        assert result["data"][CONF_HOST] == "local-borgbackup"
        assert result["data"][CONF_PORT] == 8774
        assert result["data"][CONF_API_KEY] == "test-api-key-abc123"

    async def test_entry_title_includes_host(self):
        flow = _make_flow()
        with patch(
            "custom_components.borgbackup.config_flow.BorgAddonClient.health_check",
            new_callable=AsyncMock,
        ):
            result = await flow.async_step_user(user_input=_GOOD_INPUT)

        assert "local-borgbackup" in result["title"]

    async def test_duplicate_host_port_aborts(self):
        from homeassistant.data_entry_flow import AbortFlow

        flow = _make_flow()
        flow._async_abort_entries_match = MagicMock(
            side_effect=AbortFlow("already_configured")
        )
        with patch(
            "custom_components.borgbackup.config_flow.BorgAddonClient.health_check",
            new_callable=AsyncMock,
        ) as mock_hc:
            with pytest.raises(AbortFlow):
                await flow.async_step_user(user_input=_GOOD_INPUT)
        # Guard must run before we bother contacting the add-on.
        flow._async_abort_entries_match.assert_called_once()
        mock_hc.assert_not_called()

    async def test_auth_error_shows_form_with_error(self):
        flow = _make_flow()
        with patch(
            "custom_components.borgbackup.config_flow.BorgAddonClient.health_check",
            side_effect=AddonAuthError("bad key"),
        ):
            result = await flow.async_step_user(user_input=_GOOD_INPUT)

        assert result["type"] == "form"
        assert CONF_API_KEY in result["errors"] or "base" in result["errors"]

    async def test_connection_error_shows_form_with_error(self):
        flow = _make_flow()
        with patch(
            "custom_components.borgbackup.config_flow.BorgAddonClient.health_check",
            side_effect=AddonConnectionError("no route"),
        ):
            result = await flow.async_step_user(user_input=_GOOD_INPUT)

        assert result["type"] == "form"
        assert result["errors"]

    async def test_generic_addon_error_shows_form_with_base_error(self):
        flow = _make_flow()
        with patch(
            "custom_components.borgbackup.config_flow.BorgAddonClient.health_check",
            side_effect=AddonError("boom"),
        ):
            result = await flow.async_step_user(user_input=_GOOD_INPUT)

        assert result["type"] == "form"
        assert "base" in result["errors"]


# ---------------------------------------------------------------------------
# BorgConfigFlow – async_step_reauth_confirm
# ---------------------------------------------------------------------------


class TestReauthStep:
    """Tests for the re-authentication flow (API key rotation)."""

    async def test_shows_form_without_input(self):
        flow = _make_flow()
        target = MagicMock()
        target.data = {CONF_HOST: "local-borgbackup", CONF_PORT: 8774, CONF_API_KEY: "old-key"}
        flow._get_reauth_entry = MagicMock(return_value=target)

        result = await flow.async_step_reauth_confirm(user_input=None)
        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"

    async def test_success_updates_api_key_and_aborts(self):
        flow = _make_flow()
        target = MagicMock()
        target.data = {CONF_HOST: "local-borgbackup", CONF_PORT: 8774, CONF_API_KEY: "old-key"}
        flow._get_reauth_entry = MagicMock(return_value=target)

        with patch(
            "custom_components.borgbackup.config_flow.BorgAddonClient.health_check",
            new_callable=AsyncMock,
        ):
            result = await flow.async_step_reauth_confirm(
                user_input={CONF_API_KEY: "new-key"}
            )

        assert result["type"] == "abort"
        flow.async_update_reload_and_abort.assert_called_once()
        call_kw = flow.async_update_reload_and_abort.call_args[1]
        assert call_kw["data_updates"][CONF_API_KEY] == "new-key"

    async def test_invalid_key_shows_error(self):
        flow = _make_flow()
        target = MagicMock()
        target.data = {CONF_HOST: "local-borgbackup", CONF_PORT: 8774, CONF_API_KEY: "old-key"}
        flow._get_reauth_entry = MagicMock(return_value=target)

        with patch(
            "custom_components.borgbackup.config_flow.BorgAddonClient.health_check",
            side_effect=AddonAuthError("bad key"),
        ):
            result = await flow.async_step_reauth_confirm(
                user_input={CONF_API_KEY: "wrong-key"}
            )

        assert result["type"] == "form"
        assert result["errors"]


# ---------------------------------------------------------------------------
# BorgConfigFlow – async_step_reconfigure
# ---------------------------------------------------------------------------


class TestReconfigureStep:
    """Tests for the reconfigure flow (host, port, API key)."""

    async def test_shows_form_without_input(self):
        flow = _make_flow()
        target = MagicMock()
        target.data = {CONF_HOST: "local-borgbackup", CONF_PORT: 8774, CONF_API_KEY: "test-key"}
        flow._get_reconfigure_entry = MagicMock(return_value=target)

        result = await flow.async_step_reconfigure(user_input=None)
        assert result["type"] == "form"
        assert result["step_id"] == "reconfigure"

    async def test_success_saves_new_host_port_and_key(self):
        flow = _make_flow()
        target = MagicMock()
        target.data = {CONF_HOST: "old-host", CONF_PORT: 8774, CONF_API_KEY: "old-key"}
        flow._get_reconfigure_entry = MagicMock(return_value=target)

        with patch(
            "custom_components.borgbackup.config_flow.BorgAddonClient.health_check",
            new_callable=AsyncMock,
        ):
            result = await flow.async_step_reconfigure(
                user_input={
                    CONF_HOST: "new-host",
                    CONF_PORT: 9000,
                    CONF_API_KEY: "new-key",
                }
            )

        assert result["type"] == "abort"
        call_kw = flow.async_update_reload_and_abort.call_args[1]
        assert call_kw["data_updates"][CONF_HOST] == "new-host"
        assert call_kw["data_updates"][CONF_PORT] == 9000
        assert call_kw["data_updates"][CONF_API_KEY] == "new-key"

    async def test_connection_error_shows_form(self):
        flow = _make_flow()
        target = MagicMock()
        target.data = {CONF_HOST: "local-borgbackup", CONF_PORT: 8774, CONF_API_KEY: "key"}
        flow._get_reconfigure_entry = MagicMock(return_value=target)

        with patch(
            "custom_components.borgbackup.config_flow.BorgAddonClient.health_check",
            side_effect=AddonConnectionError("unreachable"),
        ):
            result = await flow.async_step_reconfigure(
                user_input={CONF_HOST: "bad-host", CONF_PORT: 8774, CONF_API_KEY: "key"}
            )

        assert result["type"] == "form"
        assert result["errors"]
