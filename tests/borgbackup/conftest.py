"""Shared fixtures for BorgBackup tests."""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub out C-extension modules that may not have wheels for the
# current Python version.  Injected BEFORE any Home Assistant import.
# ---------------------------------------------------------------------------

if "lru" not in sys.modules:
    _lru = types.ModuleType("lru")

    class _LRU(dict):
        """Minimal stand-in for lru.LRU used by HA template helpers."""

        def __init__(self, maxsize: int = 128, *a, **kw):
            super().__init__(*a, **kw)
            self._maxsize = maxsize

        def get_size(self):
            return self._maxsize

    _lru.LRU = _LRU  # type: ignore[attr-defined]
    sys.modules["lru"] = _lru

# ---------------------------------------------------------------------------
# Compatibility shims: BackupNotFound / suggested_filename may be absent
# in older HA releases.
# ---------------------------------------------------------------------------

try:
    from homeassistant.components.backup import BackupNotFound  # noqa: F401
except ImportError:
    import homeassistant.components.backup as _backup_mod

    class _BackupNotFound(Exception):
        """Placeholder for older HA versions."""

    _backup_mod.BackupNotFound = _BackupNotFound  # type: ignore[attr-defined]

try:
    from homeassistant.components.backup import suggested_filename  # noqa: F401
except ImportError:
    import homeassistant.components.backup as _backup_mod2

    def _suggested_filename(backup):  # type: ignore[override]
        return f"{backup.backup_id}.tar"

    _backup_mod2.suggested_filename = _suggested_filename  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_addon_client():
    """Return a fully-mocked BorgAddonClient."""
    client = MagicMock()
    client.health_check = AsyncMock()
    client.list_backups = AsyncMock(return_value=[])
    client.upload_backup = AsyncMock()
    client.download_backup = AsyncMock()
    client.delete_backup = AsyncMock()
    return client


@pytest.fixture
def sample_config():
    """Return a sample configuration dict for a BorgBackup config entry."""
    return {
        "host": "local-borgbackup",
        "port": 8774,
        "api_key": "test-api-key-abc123",
    }

