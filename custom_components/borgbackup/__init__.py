"""BorgBackup – HTTP client gateway to the BorgBackup add-on."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    AGENT_LISTENER_KEY,
    CONF_API_KEY,
    CONF_HOST,
    CONF_PORT,
    DOMAIN,
    TRANSFER_SOCK_CONNECT_TIMEOUT,
    TRANSFER_SOCK_READ_TIMEOUT,
)

_TRANSFER_TIMEOUT = aiohttp.ClientTimeout(
    total=None,
    sock_connect=TRANSFER_SOCK_CONNECT_TIMEOUT,
    sock_read=TRANSFER_SOCK_READ_TIMEOUT,
)

_LOG = logging.getLogger(__name__)

_CHUNK = 65536

# ---------------------------------------------------------------------------
# Config-entry type alias
# ---------------------------------------------------------------------------

type BorgEntry = ConfigEntry[BorgAddonClient]


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class AddonError(Exception):
    """Base class for all BorgBackup add-on errors."""


class AddonAuthError(AddonError):
    """API key rejected (HTTP 401 or 403)."""


class AddonConnectionError(AddonError):
    """Cannot reach the add-on (network error or HTTP 502)."""


class AddonNotFoundError(AddonError):
    """Requested backup not found (HTTP 404)."""


class AddonServerError(AddonError):
    """Add-on returned an unexpected server error (HTTP 5xx)."""


# ---------------------------------------------------------------------------
# BorgAddonClient
# ---------------------------------------------------------------------------


class BorgAddonClient:
    """Thin async HTTP client for the BorgBackup add-on API."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        api_key: str,
    ) -> None:
        self._hass = hass
        self._host = host
        self._port = port
        self._api_key = api_key

    # -- helpers -------------------------------------------------------------

    @property
    def _base_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _raise_for_status(self, status: int, body: str = "") -> None:
        """Raise the appropriate AddonError for non-2xx HTTP status codes."""
        if status in (401, 403):
            raise AddonAuthError(f"HTTP {status}: {body[:200]}")
        if status == 404:
            raise AddonNotFoundError(f"HTTP 404: {body[:200]}")
        if status == 502:
            raise AddonConnectionError(f"HTTP 502: {body[:200]}")
        if status >= 500:
            raise AddonServerError(f"HTTP {status}: {body[:200]}")

    # -- public API ----------------------------------------------------------

    async def health_check(self) -> None:
        """Verify that the add-on is reachable and the API key is valid.

        Raises:
            AddonConnectionError: if the add-on cannot be reached.
            AddonAuthError: if the API key is invalid.
            AddonServerError: if the add-on returns an unexpected error.
        """
        session = async_get_clientsession(self._hass)
        try:
            resp = await session.get(
                f"{self._base_url}/api/health", headers=self._headers()
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise AddonConnectionError(str(exc)) from exc
        body = await resp.text()
        self._raise_for_status(resp.status, body)

    async def list_backups(self) -> list[dict[str, Any]]:
        """Return all HA-managed backups from the add-on as metadata dicts."""
        session = async_get_clientsession(self._hass)
        try:
            resp = await session.get(
                f"{self._base_url}/api/backups", headers=self._headers()
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise AddonConnectionError(str(exc)) from exc
        body = await resp.text()
        self._raise_for_status(resp.status, body)
        return await resp.json()

    async def upload_backup(
        self,
        backup_id: str,
        backup_meta: str,
        open_stream: Callable[[], Coroutine[Any, Any, AsyncIterator[bytes]]],
    ) -> None:
        """Stream a backup tar to the add-on via chunked POST.

        Args:
            backup_id: HA backup ID used as the archive name suffix.
            backup_meta: JSON-serialised AgentBackup.as_dict() metadata.
            open_stream: Async callable returning an AsyncIterator[bytes].
        """
        session = async_get_clientsession(self._hass)

        async def _body() -> AsyncIterator[bytes]:
            async for chunk in await open_stream():
                yield chunk

        headers = {
            **self._headers(),
            "X-Backup-Id": backup_id,
            "X-Backup-Meta": backup_meta,
            "Content-Type": "application/octet-stream",
        }
        try:
            resp = await session.post(
                f"{self._base_url}/api/backups",
                headers=headers,
                data=_body(),
                timeout=_TRANSFER_TIMEOUT,
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise AddonConnectionError(str(exc)) from exc
        body = await resp.text()
        self._raise_for_status(resp.status, body)

    async def download_backup(self, backup_id: str) -> AsyncIterator[bytes]:
        """Stream a backup tar from the add-on."""
        session = async_get_clientsession(self._hass)
        try:
            resp = await session.get(
                f"{self._base_url}/api/backups/{backup_id}",
                headers=self._headers(),
                timeout=_TRANSFER_TIMEOUT,
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise AddonConnectionError(str(exc)) from exc
        if resp.status != 200:
            body = await resp.text()
            self._raise_for_status(resp.status, body)

        async def _iter() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.content.iter_chunked(_CHUNK):
                    yield chunk
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise AddonConnectionError(str(exc)) from exc
            finally:
                resp.release()

        return _iter()

    async def delete_backup(self, backup_id: str) -> None:
        """Delete a backup from the add-on."""
        session = async_get_clientsession(self._hass)
        try:
            resp = await session.delete(
                f"{self._base_url}/api/backups/{backup_id}",
                headers=self._headers(),
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise AddonConnectionError(str(exc)) from exc
        if resp.status != 204:
            body = await resp.text()
            self._raise_for_status(resp.status, body)


# ---------------------------------------------------------------------------
# Entry setup / teardown
# ---------------------------------------------------------------------------


async def async_setup_entry(hass: HomeAssistant, entry: BorgEntry) -> bool:
    """Initialise a BorgBackup config entry."""
    client = BorgAddonClient(
        hass=hass,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        api_key=entry.data[CONF_API_KEY],
    )

    try:
        await client.health_check()
    except AddonAuthError as exc:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="invalid_api_key",
        ) from exc
    except AddonConnectionError as exc:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        ) from exc
    except AddonError as exc:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="addon_error",
        ) from exc

    entry.runtime_data = client

    def _propagate_state_change() -> None:
        for cb in hass.data.get(AGENT_LISTENER_KEY, []):
            cb()

    entry.async_on_unload(entry.async_on_state_change(_propagate_state_change))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BorgEntry) -> bool:
    """Unload a BorgBackup config entry."""
    # No local resources to clean up — the add-on runs independently.
    return True
