"""BorgBackup integration constants."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from homeassistant.util.hass_dict import HassKey

DOMAIN: Final = "borgbackup"

# --- Add-on connection ---
CONF_HOST = "host"
CONF_PORT = "port"
CONF_API_KEY = "api_key"

# --- Add-on defaults ---
ADDON_SLUG: Final = "borgbackup"
DEFAULT_PORT: Final = 8774

# --- Borg archive naming ---
# All HA-managed archives are prefixed with this string so they can be
# distinguished from other archives the user may have in the same repo.
ARCHIVE_PREFIX: Final = "ha-"

# --- Listing cache ---
LISTING_CACHE_SECONDS: Final = 300

# --- Upload/download timeouts ---
# aiohttp's default ClientTimeout(total=300) kills a large backup transfer
# mid-stream: the add-on doesn't respond until the whole tar is written to
# borg (upload) or extracted (download), which for multi-GB backups or an
# SSH-remote repo easily exceeds 5 minutes. `total=None` removes the overall
# deadline for these two calls specifically; sock_connect/sock_read still
# bound how long a genuinely stuck connection can hang.
TRANSFER_SOCK_CONNECT_TIMEOUT: Final = 30
TRANSFER_SOCK_READ_TIMEOUT: Final = 3600

# --- Internal HA keys ---
AGENT_LISTENER_KEY: HassKey[list[Callable[[], None]]] = HassKey(
    f"{DOMAIN}.agent_listeners"
)
