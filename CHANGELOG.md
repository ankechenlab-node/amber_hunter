# Changelog

All notable changes to amber-hunter are documented here.

## [v0.9.0] — 2026-03-26
### Compatibility
- Compatible with **huper v1.0.0** (DID identity layer: BIP-39 mnemonic + Ed25519 keys)


### Added
- **Active Recall `/recall`** — Search relevant amber memories before responding
  - `GET /recall?q=<query>&limit=3`
  - Returns `injected_prompt` for each memory, ready to inject into AI context
  - Supports `keyword` and `semantic` (sentence-transformers) search
  - Response includes `semantic_available` so AI knows vector search status
- **Proactive Memory Capture** — Automatically detects significant moments from OpenClaw session history
  - Signals: `correction`, `error_fix`, `decision`, `preference`, `discovery`
  - Runs every 10 minutes via LaunchAgent (macOS) / systemd (Linux)
  - Completely silent — zero user interruption
- **Auto-Sync Toggle** — `GET/POST /config` for auto_sync preference
  - When enabled, every freeze automatically syncs to huper.org cloud
- **Cross-Platform Keychain**
  - macOS: Keychain via `security` command
  - Linux: GNOME Keyring via `secret-tool`
  - Windows: Credential Manager via `cmdkey`
- **Cross-Platform Auto-Start**
  - macOS: LaunchAgent
  - Linux: systemd user service
  - Windows: Task Scheduler

### Fixed
- CORS preflight 405: switched to StarletteCORSMiddleware + explicit OPTIONS
- Mixed content: Authorization header blocked by browser from HTTPS→HTTP; switched to query param `?token=`
- SSE 500: `threading.Queue` → `queue.Queue` (Python 3.10 compatibility)

### API Endpoints
- `/recall` — Active memory retrieval (new)
- `/sync` — Cloud sync (GET, query param auth)
- `/config` — Auto-sync config (GET/POST)
- `/master-password` — Set master password (localhost only)
- `/token` — Get local API key (localhost only)

---

## [v0.8.4] — 2026-03-22

### Added
- **Encryption** — AES-256-GCM encryption for all capsule content
  - `salt` and `nonce` persisted in SQLite
  - `derive_key` uses PBKDF2-HMAC-SHA256
- **Local API Authentication** — Bearer token validation on all `/capsules` endpoints
- **macOS Keychain** — master_password stored in Keychain, never written to disk
- **CORS Configuration** — Restricted to `https://huper.org` + `localhost`

### Fixed
- Session regex stability: all regex wrapped in try/except
- CORS preflight handling

### Security
- master_password must come from Keychain (no plaintext fallback)
- API key required for all capsule operations

---

*Released versions are tagged in git. Full history: `git log --oneline`.*
