## [v0.9.6] — 2026-03-28

### Added
- **`POST /bind-apikey`** (localhost-only) — dashboard 生成新 API Key 后自动调用，将 `api_key` 写入 `~/.amber-hunter/config.json`，解决 api_key 不一致导致同步超时的问题

### Changed
- **dashboard sync timeout** — `AbortSignal.timeout` 从 30000ms 提升到 120000ms，支持 30+ 条未同步胶囊的批量同步（约 1.3s/条）
- **dashboard 401 自动重试** — `checkHunterStatus()`、`triggerSync()`、`loadSyncStatus()` 均加入 token 过期自动刷新 + 重试逻辑

---

## [v0.9.5] — 2026-03-28

### Changed
- **amber-proactive V4**：完全自包含脚本，LLM提取+写胶囊全部在脚本内完成，cron每15分钟直接触发，无需agent介入，无需heartbeat触发链路修复

### Fixed
- **heartbeat不触发问题**：Telegram消息不触发Mac app heartbeat，导致V3自动提取从未运行；V4彻底解决

# Changelog

## [v0.9.3] — 2026-03-27

### Fixed
- Import `CONFIG_PATH` from `core.keychain` in `amber_hunter.py` — `set_master_password_handler` was silently failing on the config.json fallback write due to `NameError` caught by bare `except Exception`
- Unify all version strings to `0.9.2 → 0.9.3`: `FastAPI(version=...)`, `/status` response, `/` root response, and `main()` startup print were still reporting `0.8.9` / `v0.8.4` while the file header said `v0.9.2`
- Fix `ensure_config_dir()` in `core/keychain.py` — was calling `Path(".amber-hunter").mkdir(...)` (relative to CWD) instead of `(HOME / ".amber-hunter").mkdir(parents=True, exist_ok=True)`, creating a spurious directory wherever the process was launched from
- Remove duplicate `_EMBED_MODEL = None` module-level declaration (line 146 was redundant after line 33)

## [v0.9.2] — 2026-03-26
### Fixed
- Add `sentence-transformers>=2.2.0` and `numpy>=1.24.0` to requirements.txt — semantic search now works out of the box after install
- Remove unused `mac-keychain` package from requirements.txt (macOS keychain uses the built-in `security` CLI)
- install.sh: show download size warning (~90MB) and surface pip errors instead of silently suppressing them

## [v0.9.1] — 2026-03-26
### Fixed
- Removed hardcoded personal Telegram session ID; session capture now finds any user's active Telegram session generically
- Cleaned personal name references from session logic comments


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
