# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.0] - 2026-09-22

### Fixed

- **Large uploads/downloads no longer die at 300 seconds** — `upload_backup`/
  `download_backup` now use a `ClientTimeout` with no overall deadline
  (bounded instead by a 30s connect timeout and a 3600s read timeout), since
  the add-on only responds once the transfer has actually finished.
- `TimeoutError` is now caught in every `BorgAddonClient` method and mapped
  to `AddonConnectionError`; previously it wasn't an `aiohttp.ClientError`
  and ran as an unhandled exception straight through `_wrap_addon_errors`.
- `download_backup`'s response is now released (`resp.release()`) once the
  stream is exhausted or fails, instead of leaking the connection; an error
  raised mid-stream is now translated to `BackupAgentError` for the caller
  instead of leaking a raw `aiohttp` exception.
- **Rediscovering the add-on after it's reinstalled now updates the stored
  API key** instead of leaving the entry with the old, now-invalid one and
  forcing a manual reauth. A manually-configured entry now also gets the
  same `host:port` unique ID discovery uses, so the two stay in sync.

### Removed

- Dead duplicate `async_get_backup_agents`/`async_register_backup_agents_listener`
  in `__init__.py` — HA's backup platform loader only ever called the copies
  in `backup.py`.

### Changed

- CI now also runs the test suite on Python 3.13 (previously 3.14 only);
  `hassfest`/`hacs/action` are pinned to a commit SHA instead of floating
  branches.

## [2.0.1] - 2026-07-09

### Fixed

- **Duplicate config entries** — both the manual setup and the Supervisor
  discovery confirmation now call `_async_abort_entries_match` on `host`+`port`
  before contacting the add-on, so the same add-on can no longer be added twice.
- **Manifest** — declare `hassio` in `after_dependencies` (the config flow imports
  `homeassistant.components.hassio` for Supervisor discovery), fixing the hassfest
  `[DEPENDENCIES]` validation error.
- **Discovery unique ID** — the Supervisor discovery step now calls
  `async_set_unique_id` (host+port) and aborts if already configured, clearing the
  hassfest `[CONFIG_FLOW]` warning and preventing repeated discovery prompts.

## [2.0.0] - 2026-04-06

### 🎉 Initial release as standalone repository

BorgBackup has been extracted from the CloudStash repository into its own standalone
project. The integration has been fully rewritten to use the new add-on architecture.

#### Architecture change

- **Removed**: `BorgGateway` (direct subprocess calls to `borg`)
- **Removed**: `borgbackup` pip dependency (no longer required on the HA host)
- **Removed**: SSH key filesystem management from the integration
- **Removed**: repository path / passphrase / pruning config from the integration
- **Added**: `BorgAddonClient` — async `aiohttp` HTTP client to the BorgBackup add-on
- **Added**: Supervisor auto-discovery step (`async_step_hassio`)
- **Added**: Config stores only `host`, `port`, `api_key`

#### Error handling

New `AddonError` hierarchy with four subclasses:
- `AddonAuthError` (401/403) → `ConfigEntryAuthFailed` / `BackupAgentError("invalid_api_key")`
- `AddonConnectionError` (502 / network) → `ConfigEntryNotReady` / `BackupAgentError("cannot_connect")`
- `AddonNotFoundError` (404) → `BackupNotFound`
- `AddonServerError` (5xx) → `ConfigEntryError` / `BackupAgentError("addon_error")`

#### Quality

- 74 unit tests — all green
- GitHub Actions CI: pytest + hassfest + HACS validation

#### Migration from CloudStash repo

If you were using the BorgBackup integration from the CloudStash repository, you need to:

1. Install and configure the [BorgBackup Add-on](https://github.com/your-org/borgbackup-addon)
2. Remove the old `borgbackup` integration from HA (Settings → Devices & Services)
3. Install this integration via HACS or manually
4. Re-add the integration using **Add Integration → BorgBackup** and enter the add-on API key
