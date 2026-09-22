"""BorgBackup config-flow – setup, re-auth, and reconfigure wizards."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

try:
    from homeassistant.components.hassio import HassioServiceInfo
except ImportError:
    HassioServiceInfo = None  # type: ignore[assignment,misc]

from . import AddonAuthError, AddonConnectionError, AddonError, BorgAddonClient
from .const import (
    ADDON_SLUG,
    CONF_API_KEY,
    CONF_HOST,
    CONF_PORT,
    DEFAULT_PORT,
    DOMAIN,
)

# ---------------------------------------------------------------------------
# Form widgets
# ---------------------------------------------------------------------------

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

# ---------------------------------------------------------------------------
# Form schemas
# ---------------------------------------------------------------------------

SCHEMA_USER = vol.Schema(
    {
        vol.Required(CONF_HOST, default=f"local-{ADDON_SLUG}"): cv.string,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_API_KEY): _PASSWORD,
    }
)

SCHEMA_REAUTH = vol.Schema(
    {
        vol.Required(CONF_API_KEY): _PASSWORD,
    }
)

SCHEMA_RECONFIGURE = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_PORT): int,
        vol.Required(CONF_API_KEY): _PASSWORD,
    }
)


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------


class BorgConfigFlow(ConfigFlow, domain=DOMAIN):
    """Guide the user through initial setup, re-auth, and reconfiguration."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._hassio_discovery: dict[str, Any] = {}

    # -- Supervisor auto-discovery -------------------------------------------

    async def async_step_hassio(
        self, discovery_info: Any
    ) -> ConfigFlowResult:
        """Called by the Supervisor when the add-on publishes discovery info."""
        self._hassio_discovery = discovery_info.config
        host = self._hassio_discovery.get(CONF_HOST, f"local-{ADDON_SLUG}")
        port = int(self._hassio_discovery.get(CONF_PORT, DEFAULT_PORT))
        api_key = self._hassio_discovery.get(CONF_API_KEY)
        await self.async_set_unique_id(f"{host}:{port}")
        # A reinstalled add-on generates a new API key but keeps the same
        # host/port, so re-discovery would otherwise abort with
        # already_configured while the existing entry is left holding a key
        # that no longer works, forcing a manual reauth. Updating the entry
        # here keeps it working without user action.
        self._abort_if_unique_id_configured(
            updates={CONF_API_KEY: api_key} if api_key else None
        )
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm automatic setup from Supervisor discovery."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = self._hassio_discovery.get(CONF_HOST, f"local-{ADDON_SLUG}")
            port = int(self._hassio_discovery.get(CONF_PORT, DEFAULT_PORT))
            api_key = self._hassio_discovery[CONF_API_KEY]
            self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})
            errors = await self._try_connect(host, port, api_key)
            if not errors:
                return self.async_create_entry(
                    title=f"BorgBackup ({host})",
                    data={CONF_HOST: host, CONF_PORT: port, CONF_API_KEY: api_key},
                )
        return self.async_show_form(
            step_id="hassio_confirm",
            errors=errors,
            description_placeholders={
                CONF_HOST: self._hassio_discovery.get(CONF_HOST, f"local-{ADDON_SLUG}"),
                CONF_PORT: str(self._hassio_discovery.get(CONF_PORT, DEFAULT_PORT)),
            },
        )

    # -- initial setup -------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """First-time manual setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            api_key = user_input[CONF_API_KEY].strip()

            # Give a manually-created entry the same unique_id scheme Supervisor
            # discovery uses, so a later discovery message for this same add-on
            # updates this entry's key instead of colliding with it.
            await self.async_set_unique_id(f"{host}:{port}")
            self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})
            errors = await self._try_connect(host, port, api_key)
            if not errors:
                return self.async_create_entry(
                    title=f"BorgBackup ({host})",
                    data={CONF_HOST: host, CONF_PORT: port, CONF_API_KEY: api_key},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(SCHEMA_USER, user_input),
            errors=errors,
        )

    # -- re-authentication ---------------------------------------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Entry point for the re-auth flow."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user for a fresh API key."""
        errors: dict[str, str] = {}
        target = self._get_reauth_entry()

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            errors = await self._try_connect(
                target.data[CONF_HOST],
                target.data[CONF_PORT],
                api_key,
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    target,
                    data_updates={CONF_API_KEY: api_key},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                SCHEMA_REAUTH, {CONF_API_KEY: ""}
            ),
            description_placeholders={CONF_HOST: target.data[CONF_HOST]},
            errors=errors,
        )

    # -- reconfigure ---------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user change host, port, and API key."""
        errors: dict[str, str] = {}
        target = self._get_reconfigure_entry()

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            api_key = user_input[CONF_API_KEY].strip()

            errors = await self._try_connect(host, port, api_key)
            if not errors:
                return self.async_update_reload_and_abort(
                    target,
                    data_updates={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_API_KEY: api_key,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                SCHEMA_RECONFIGURE,
                user_input
                or {
                    CONF_HOST: target.data[CONF_HOST],
                    CONF_PORT: target.data[CONF_PORT],
                },
            ),
            errors=errors,
        )

    # -- shared helpers ------------------------------------------------------

    async def _try_connect(
        self, host: str, port: int, api_key: str
    ) -> dict[str, str]:
        """Attempt a health-check; return an error dict (empty on success)."""
        client = BorgAddonClient(self.hass, host, port, api_key)
        try:
            await client.health_check()
        except AddonAuthError:
            return {CONF_API_KEY: "invalid_api_key"}
        except AddonConnectionError:
            return {CONF_HOST: "cannot_connect"}
        except AddonError:
            return {"base": "addon_error"}
        return {}


