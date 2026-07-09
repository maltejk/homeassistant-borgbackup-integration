# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
