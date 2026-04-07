"""BorgBackup backup-agent implementation."""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from time import monotonic
from typing import Any

from homeassistant.components.backup import (
    AgentBackup,
    BackupAgent,
    BackupAgentError,
    BackupNotFound,
)
from homeassistant.core import HomeAssistant, callback

from . import (
    AddonAuthError,
    AddonConnectionError,
    AddonError,
    AddonNotFoundError,
    BorgAddonClient,
    BorgEntry,
)
from .const import (
    AGENT_LISTENER_KEY,
    ARCHIVE_PREFIX,
    DOMAIN,
    LISTING_CACHE_SECONDS,
)

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error-handling decorator
# ---------------------------------------------------------------------------


def _wrap_addon_errors[T](
    fn: Callable[..., Coroutine[Any, Any, T]],
) -> Callable[..., Coroutine[Any, Any, T]]:
    """Translate AddonError subclasses into BackupAgentError / BackupNotFound."""

    @functools.wraps(fn)
    async def _inner(*a: Any, **kw: Any) -> T:
        try:
            return await fn(*a, **kw)
        except AddonNotFoundError as exc:
            raise BackupNotFound(str(exc)) from exc
        except AddonAuthError as exc:
            raise BackupAgentError(
                translation_domain=DOMAIN,
                translation_key="invalid_api_key",
            ) from exc
        except AddonConnectionError as exc:
            raise BackupAgentError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
            ) from exc
        except AddonError as exc:
            raise BackupAgentError(
                translation_domain=DOMAIN,
                translation_key="addon_error",
            ) from exc

    return _inner  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Module-level helpers (registered in backup.py, called from __init__.py)
# ---------------------------------------------------------------------------


async def async_get_backup_agents(hass: HomeAssistant) -> list[BackupAgent]:
    """Return one agent per loaded config entry."""
    entries: list[BorgEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    return [BorgBackupAgent(hass, e) for e in entries]


@callback
def async_register_backup_agents_listener(
    hass: HomeAssistant,
    *,
    listener: Callable[[], None],
    **kwargs: Any,
) -> Callable[[], None]:
    """Subscribe to agent add/remove notifications; returns an unsubscribe handle."""
    listeners = hass.data.setdefault(AGENT_LISTENER_KEY, [])
    listeners.append(listener)

    @callback
    def _unsubscribe() -> None:
        listeners.remove(listener)
        if not listeners:
            del hass.data[AGENT_LISTENER_KEY]

    return _unsubscribe


# ---------------------------------------------------------------------------
# Backup agent
# ---------------------------------------------------------------------------


class BorgBackupAgent(BackupAgent):
    """Stores Home Assistant backups via the BorgBackup add-on."""

    domain = DOMAIN

    def __init__(self, hass: HomeAssistant, entry: BorgEntry) -> None:
        super().__init__()
        self._client: BorgAddonClient = entry.runtime_data
        self._entry = entry
        self.name = entry.title
        self.unique_id = entry.entry_id
        self._listing: dict[str, AgentBackup] = {}
        self._listing_valid_until: float = 0.0

    # -- public BackupAgent interface ----------------------------------------

    @_wrap_addon_errors
    async def async_download_backup(
        self,
        backup_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[bytes]:
        """Stream a backup archive from the Borg repository via the add-on."""
        await self._resolve(backup_id)  # raises BackupNotFound if missing
        return await self._client.download_backup(backup_id)

    @_wrap_addon_errors
    async def async_upload_backup(
        self,
        *,
        open_stream: Callable[[], Coroutine[Any, Any, AsyncIterator[bytes]]],
        backup: AgentBackup,
        **kwargs: Any,
    ) -> None:
        """Upload a backup to the Borg repository via the add-on."""
        await self._client.upload_backup(
            backup_id=backup.backup_id,
            backup_meta=json.dumps(backup.as_dict()),
            open_stream=open_stream,
        )
        self._drop_cache()

    @_wrap_addon_errors
    async def async_delete_backup(self, backup_id: str, **kwargs: Any) -> None:
        """Delete a backup from the Borg repository via the add-on."""
        await self._resolve(backup_id)  # raises BackupNotFound if missing
        await self._client.delete_backup(backup_id)
        self._drop_cache()

    @_wrap_addon_errors
    async def async_list_backups(self, **kwargs: Any) -> list[AgentBackup]:
        """Return every backup known to the Borg repository."""
        return list((await self._fetch_listing()).values())

    @_wrap_addon_errors
    async def async_get_backup(self, backup_id: str, **kwargs: Any) -> AgentBackup:
        """Return metadata for a single backup."""
        return await self._resolve(backup_id)

    # -- internal helpers ----------------------------------------------------

    async def _resolve(self, backup_id: str) -> AgentBackup:
        """Look up *backup_id* in the cache or raise ``BackupNotFound``."""
        listing = await self._fetch_listing()
        if (entry := listing.get(backup_id)) is not None:
            return entry
        raise BackupNotFound(f"Backup {backup_id} not found")

    def _drop_cache(self) -> None:
        """Force the next listing call to re-fetch from the add-on."""
        self._listing_valid_until = 0.0
        self._listing = {}

    async def _fetch_listing(self) -> dict[str, AgentBackup]:
        """Return all backups, re-fetching from the add-on when cache is stale."""
        if monotonic() <= self._listing_valid_until:
            return self._listing

        raw_backups = await self._client.list_backups()
        result: dict[str, AgentBackup] = {}

        for raw in raw_backups:
            try:
                parsed = AgentBackup.from_dict(raw)
            except (KeyError, TypeError) as exc:
                _LOG.warning("Skipping backup with invalid metadata: %s", exc)
                continue
            result[parsed.backup_id] = parsed

        self._listing = result
        self._listing_valid_until = monotonic() + LISTING_CACHE_SECONDS
        return result
