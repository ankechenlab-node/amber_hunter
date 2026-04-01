## [v1.2.5] — 2026-04-01

### Fixed
- **proactive-check.js API key 路径错误** — 之前从 `cfg.providers['minimax-cn'].apiKey` 读取，实际 OpenClaw 配置结构为 `cfg.models.providers['minimax-cn'].apiKey`；导致所有用户的 amber-proactive 自动捕获认证失败；从不动脚本改为正确路径
- **LaunchAgent plist 路径错误（Anke 本地问题）** — plist 指向 `~/.openclaw/workspace/skills/amber-proactive/`（不存在），实际为 `~/.openclaw/skills/amber-hunter/proactive/`；导致 amber-proactive 进程从未真正启动

### Changed
- **proactive-check.js MiniMax 模型** — 从 `MiniMax-M2.1-flash` 升级为 `MiniMax-M2.7-highspeed`（与 OpenClaw 主模型一致，提取质量更高）

---

## [v1.2.4] — 2026-04-01

### Fixed
- **`get_unsynced_capsules()` 字段缺失** — `core/db.py` SELECT 语句未包含 `source_type` 和 `category`，导致同步 payload 这两个字段始终为空；现已补全
- **`httpx.Client` 在同步循环内重复创建** — 之前每上传一条胶囊就新建一个 TCP 连接；提取 `_do_sync_capsules()` helper 后改为单 Client 复用，N 条胶囊只需 1 个连接

### Changed
- **同步 payload 补全** — `_do_sync_capsules()` 新增 `source_type` 和 `category` 字段，确保云端存储与本地完全一致
- **消除代码重复** — `_background_sync()` 和 `sync_to_cloud()` 之前各有约 80 行相同同步逻辑；统一提取到 `_do_sync_capsules()`，两处均调用该 helper
- **`GET /capsules`** — 新增 `category`、`source_type` 字段；新增可选 `limit` 参数（1–300，默认 50）
- **`GET /memories`** — 新增 `category`、`source_type` 字段
- **文件头版本号** — `v1.2.2` → `v1.2.4`（之前文件头未随 v1.2.3 更新）

---

## [v1.2.3] — 2026-04-01

### Fixed
- **`/recall` 语义搜索 bug** — 语义向量化之前只在关键词候选（top-50）上做，导致关键词命中为 0 时语义搜索无结果；现在对全量胶囊（最多 300 条）同时做关键词 + 语义，彻底修复漏召回

### Changed
- **`/recall` hybrid 模式** — 从「关键词优先，语义补充」改为真正的加权混合：`0.4×keyword_norm + 0.6×semantic`；两路均对全量胶囊评分后合并排序
- **`/recall` 返回字段** — 新增 `category`、`source_type`；`injected_prompt` 格式包含 category 标签；limit 候选池从 200 条扩展到 300 条
- **`/status` 增强** — 新增字段：`capsule_count`（本地胶囊总数）、`queue_pending`（待审队列数）、`last_sync`（最近同步时间戳）、`semantic_model_loaded`（模型是否已预热）
- **FastAPI version** — 对齐到 `1.2.3`

---

## [v1.2.2] — 2026-04-01

### Fixed
- **Version strings** — `/status` and root `/` endpoints were returning `"1.1.9"` instead of the correct version; now aligned to `"1.2.2"`

---

## [v1.2.1] — 2026-03-31

### Added
- **`core/llm.py`** — LLM provider abstraction layer; MiniMax / OpenAI / Local (Ollama) unified interface; `get_llm()` factory, `complete()` text, `complete_json()` JSON; auto-detects API key from OpenClaw config or env
- **`POST /rerank`** — LLM-powered re-ranking of memory candidates; accepts `{query, memories[]}`, returns memories with `relevance_score` updated by LLM judgment
- **`GET /recall?rerank=true`** — optional LLM reranking after keyword/vector recall; non-blocking via `asyncio.to_thread`
- **`GET /classify` LLM fallback** — keyword matching primary; LLM classification triggers when keyword results < 2 tags; retry loop handles MiniMax extended thinking (200→400 tokens)

### Fixed
- **Proactive session selection** — was selecting by mtime (cron session always newest → always skipped real sessions); now selects by message count (most messages = real active session)
- **`.deleted.` file filtering** — proactive-check now skips session files containing `.deleted.` in filename
- **Duplicate session enqueue** — was re-enqueuing same session on every run; now deduplicates by `session_id` (regardless of message count growth)
- **Cron job path** — was pointing to non-existent `~/.openclaw/workspace/skills/amber-proactive/`; corrected to `~/.openclaw/skills/amber-hunter/proactive/proactive-check.js`

---

## [v1.1.9] — 2026-03-31

### Added
- **Universal life taxonomy** — 8 fixed categories (thought/learning/decision/reflection/people/life/creative/dev) replacing developer-only tags; 11 new life tags in TAG_META; bilingual Chinese+English auto-detection keywords
- **`_infer_category()`** — keyword-based category auto-detection in both `amber_hunter.py` and `app.py`; bilingual coverage for everyday life phrases
- **`POST /ingest`** (localhost) — AI-initiated memory write endpoint; `confidence≥0.95` + `review_required=false` → direct capsule; else → `memory_queue`; returns `{queued, capsule_id/queue_id, category, source_type}`
- **`POST /api/ingest`** (cloud) — same semantics for external AI clients (ChatGPT, Claude.ai) authenticating via user JWT
- **`memory_queue` table** (hunter.db) — stores AI-proposed memories pending user review; fields: id, memo, context, category, tags, source, confidence, created_at, status
- **Queue management endpoints** — `GET /queue`, `POST /queue/{id}/approve`, `POST /queue/{id}/reject`, `POST /queue/{id}/edit`
- **`source_type` + `category` DB fields** — added to both `capsules` (cloud) and local hunter.db tables; values: manual/freeze/ai_chat/ai_pending/ingest
- **Dashboard review queue card** — "待确认记忆" card in dashboard.html with badge, approve/reject/edit/approve-all UI; loaded at init
- **SKILL.md multi-client guide** — complete rewrite covering 8 categories, judgment rules, /ingest + /queue docs, openclaw/Claude Code/Cowork/ChatGPT usage patterns, platform matrix

### Changed
- Version bumped: `amber_hunter.py`, `/status`, root endpoint all → `1.1.9`
- `_CATEGORY_KEYWORDS` expanded: `thought` (想法/有个念头), `people` (聊了/聊天/和朋友), `life` (心情/情绪/低落/焦虑) — life phrases now correctly auto-categorized

### Fixed
- `api_ingest()` function signature: was incorrectly accepting `user_id` param; now uses `g.user_id` consistent with all other `@require_auth` routes
- `broadcast_event()` call in `/api/ingest`: was referencing non-existent `_push_sse()`

---

## [v1.0.0] — 2026-03-30

### Added
- **VPS / headless Linux 支持** — `core/keychain.py` 新增 `_linux_is_headless()` 检测；无 DISPLAY/WAYLAND_DISPLAY/secret-tool 环境自动降级到 `config.json` 存储凭据，VPS 部署无需 GNOME Keyring
- **平台感知 `/status`** — 返回 `platform`（macos/linux/windows）和 `headless`（bool）字段，方便客户端检测运行环境
- **公开 `is_headless()` API** — `core/keychain.py` 导出 `is_headless()`，可供上层模块判断环境

### Changed
- **正式版本** — amber-hunter 进入 v1.0 里程碑，支持 macOS / Windows / Linux 桌面 / Linux headless(VPS) 全平台

---

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
