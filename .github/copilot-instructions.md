# HomeassistantBorgBackup — Copilot Instructions

HomeassistantBorgBackup is a Home Assistant custom integration that registers a `BackupAgent` backed by the BorgBackup add-on. It communicates exclusively via HTTP with the companion add-on — there is no direct subprocess or borg CLI usage inside the integration.

## Architecture

```
custom_components/borgbackup/
  __init__.py      # BorgAddonClient (aiohttp), AddonError hierarchy, HA entry setup
  backup.py        # BorgBackupAgent — BackupAgent implementation, listing cache
  config_flow.py   # Setup / reauth / reconfigure flows + Supervisor auto-discovery
  const.py         # Domain, config keys, defaults (single source of truth)
  manifest.json    # HA metadata, depends on "backup" component
  strings.json     # UI strings + error keys
  translations/    # en.json (and future locales)
```

**HTTP client model:** All borg operations are delegated to the add-on over HTTP. `BorgAddonClient` (in `__init__.py`) wraps `aiohttp` and runs entirely on the HA event loop. Never use blocking I/O or subprocess calls inside the integration.

**Add-on API base URL:** `http://<host>:<port>` (default port 8774). Authentication via `Authorization: Bearer <api_key>` header.

**Archive naming:** All HA-managed archives are prefixed with `ha-` (`ARCHIVE_PREFIX`). This distinguishes them from user-created borgmatic archives in the same repository.

**Add-on HTTP API endpoints:**

| Method | Path | Key headers | Success |
|--------|------|-------------|--------|
| `GET` | `/api/health` | `Authorization` | 200 |
| `GET` | `/api/backups` | `Authorization` | 200 + JSON list |
| `POST` | `/api/backups` | `Authorization`, `X-Backup-Id`, `X-Backup-Meta`, `Content-Type: application/octet-stream` | 2xx |
| `GET` | `/api/backups/{id}` | `Authorization` | 200 + stream |
| `DELETE` | `/api/backups/{id}` | `Authorization` | 204 |

## Build & Test

```bash
pip install -r requirements_test.txt
pytest tests/ -v
```

Tests are under `tests/borgbackup/`. `asyncio_mode = "auto"` (pytest-asyncio). No Makefile.

CI runs pytest (Python 3.12 and 3.13 matrix), hassfest (HA manifest validation), and HACS validation.

## Key Conventions

- **Python 3.12+ features used throughout:** `from __future__ import annotations`, PEP 613 type aliases (`type BorgEntry = ...`), new-style generics (`def fn[T](...)`), union syntax (`str | None`).
- **Private helpers** are prefixed with `_`. Public interface methods use the HA `async_*` naming convention.
- Section dividers `# ---...---` mark logical groupings within modules.
- 88-char line limit.

**Error handling pattern:**
- `BorgAddonClient._raise_for_status()` converts HTTP status codes to `AddonError` subclasses.
- `@_wrap_addon_errors` decorator (in `backup.py`) converts `AddonError` subclasses → `BackupAgentError` / `BackupNotFound`. All public `BorgBackupAgent` methods use it. Add it to any new public method.

**Error hierarchy:**
- `AddonError` (base)
  - `AddonAuthError` — HTTP 401/403; becomes `BackupAgentError(translation_key="invalid_api_key")`
  - `AddonConnectionError` — network error or HTTP 502; becomes `BackupAgentError(translation_key="cannot_connect")`
  - `AddonNotFoundError` — HTTP 404; becomes `BackupNotFound`
  - `AddonServerError` — HTTP 5xx; becomes `BackupAgentError(translation_key="addon_error")`

**Config keys** live only in `const.py`. Always reference constants, never string literals.

## Config Flow

- `async_step_hassio`: triggered automatically by Supervisor when the add-on is detected. Presents a confirmation screen and pre-fills host/port.
- `async_step_user`: manual setup; full `{host, port, api_key}` form.
- `async_step_reauth` / `async_step_reconfigure`: update credentials or connection details on an existing entry.
- All steps call `_try_connect()` which maps errors to UI error keys. Error keys must exist in `strings.json` under `config.error`.

## Testing Patterns

- Tests are class-based (`class TestXxx`).
- Fixtures in `tests/borgbackup/conftest.py`: `mock_addon_client` (MagicMock with AsyncMock methods), `sample_config`.
- Helper factories used in tests (defined inline per test file, not in conftest): `_make_agent()`, `_make_backup()`, `_make_entry()`, `_make_hass()` — look for these when writing new test cases.
- Use `patch()` + `AsyncMock` for injecting dependencies; never rely on real network calls.
- `conftest.py` includes shims for `lru.LRU` (C-extension) and `BackupNotFound` / `suggested_filename` for older HA releases — check there when adding fixtures for new HA releases.
- New behaviour → new test. Aim to keep the 74-test suite green (`pytest tests/ -v`).

## HA-Specific Pitfalls

- `manifest.json` must list `"backup"` in `dependencies` — without it HA won't guarantee load order.
- `ConfigEntry[BorgAddonClient]` is the canonical entry type; use the `BorgEntry` alias.
- `async_setup_entry` stores the client in `entry.runtime_data` (not `hass.data`).
- Listing backups is cached for 300 s (`LISTING_CACHE_SECONDS`). Mutations must call `_drop_cache()` immediately.
- `strings.json` steps require both `data` (field labels) and `data_description` (help text per field) — add both when introducing new fields.
- The add-on is **required**; there is no subprocess fallback. If the add-on is unreachable, raise `ConfigEntryNotReady` (handled in `async_setup_entry`).
- If the add-on becomes unreachable mid-session the listing cache remains valid for up to 300 s (`LISTING_CACHE_SECONDS`). Subsequent `async_list_backups()` calls will return stale data and will not re-attempt until the cache expires or `_drop_cache()` is called.
- `async_get_backup_agents` and `async_register_backup_agents_listener` are **intentionally duplicated** in both `__init__.py` and `backup.py`. HA calls both modules; keep them in sync when modifying the listener pattern.
- Agent availability changes are propagated via `entry.async_on_state_change(_propagate_state_change)`, which fires all callbacks registered in `hass.data[AGENT_LISTENER_KEY]`. This is how HA's backup UI learns a new agent is ready.

## Reference Docs

- [README.md](../README.md) — user-facing setup, architecture diagram, troubleshooting
- [CHANGELOG.md](../CHANGELOG.md) — version history
