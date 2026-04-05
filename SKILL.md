# Amber-Hunter Skill
> Gives any AI client long-term memory — captures, encrypts, and recalls personal context across sessions
> Version: 1.2.37 | 2026-04-05

---

> **Tags**: ai-memory | second-brain | local-encrypted | proactive-recall | cross-platform | context-management | RAG | long-term-memory | AI-personal-assistant | privacy-first

---

amber-hunter runs on the user's local machine (Mac / Linux / Windows). Local AI clients communicate via `localhost:18998`. External AI clients (ChatGPT, Claude.ai) use the cloud API at `huper.org/api`.

---

## What It Does

Amber-Hunter is the **capture and recall layer** of Huper琥珀 — a personal memory protocol that works across any AI client and any platform.

- **AI long-term memory** — gives ChatGPT, Claude, and any AI client persistent context across conversations
- **Proactive capture** — AI-initiated writes via `/ingest`; user reviews and approves before memories are stored
- **Instant recall** — `/recall?q=<query>` retrieves relevant past memories before responding (hybrid semantic + keyword search)
- **Second brain** — builds a personal knowledge base that survives context windows and session boundaries
- **E2E encrypted** — AES-256-GCM, master_password in OS keychain, never uploaded in plaintext
- **Cross-platform** — macOS / Windows / Linux (desktop + headless server)
- **Cloud sync** — optional encrypted upload to huper.org for cross-device access
- **RAG-ready** — `/recall` endpoint returns structured context for Retrieval Augmented Generation pipelines

---

## Memory Category System (v1.1.9+)

琥珀 uses a two-level taxonomy: **category** (8 fixed domains) + **tags** (specific labels).

### The 8 Categories

| category | emoji | Label | Covers |
|----------|-------|-------|--------|
| `thought` | 💭 | 想法 | Fleeting ideas, insights, eureka moments |
| `learning` | 📖 | 学习 | Reading notes, courses, new knowledge |
| `decision` | 🎯 | 决策 | Choices made, directions set |
| `reflection` | 🌱 | 成长 | Reflections, reviews, emotional records |
| `people` | 🤝 | 关系 | Conversations with others, notes about people |
| `life` | 🏃 | 生活 | Health, food, daily observations |
| `creative` | 🎨 | 创意 | Design ideas, things to build |
| `dev` | 💻 | 开发 | All developer-specific content (code, errors, APIs, etc.) |

### Auto-detection Keywords

The system auto-tags based on content keywords. AI clients should also suggest `category` when calling `/ingest`:

```
thought    → "想到", "突然想", "realize", "just thought"
learning   → "读了", "看了", "reading", "book says"
decision   → "决定", "选择了", "decided", "going with"
reflection → "反思", "复盘", "reflecting", "looking back"
people     → "和...聊", "talked to", "met with"
life       → "运动", "睡眠", "sleep", "exercise"
creative   → creative/design keywords
dev        → python/js/git/docker/api/sql/error keywords (all existing dev rules)
```

---

## Multi-Client Integration Guide

### Which endpoint to use

| AI Client | Network | Endpoint | Auth |
|-----------|---------|----------|------|
| **openclaw** | localhost | `POST /ingest` | Bearer token |
| **Claude Code** | localhost | `POST /ingest` | Bearer token |
| **Claude in Cowork** | localhost (Desktop Commander) | `POST /ingest` | Bearer token |
| **ChatGPT** | internet (cloud) | `POST https://huper.org/api/ingest` | User JWT / API key |
| **Claude.ai** | internet (cloud) | `POST https://huper.org/api/ingest` | User JWT / API key |

### Get the local API token

```bash
curl http://localhost:18998/token
# → {"api_key": "ahk_xxxx..."}
```

---

## What's Worth Capturing — Judgment Rules

Use these rules when deciding whether to call `/ingest` during a conversation:

| Signal | Example | confidence | review_required |
|--------|---------|-----------|-----------------|
| save_request | "记住这个" / "save this" / "提醒我" | 1.0 | false |
| decision | "决定用 SQLite" / "we're going with plan B" / "用 FastAPI" | 0.9 | true |
| preference | "我更喜欢..." / "I prefer TypeScript" | 0.85 | true |
| personal_fact | 我的名字是... / 我住在... / 我在...工作 | 0.8 | true |
| summary | "总结一下..." / "key takeaways" / "tl;dr" | 0.7 | true |
| insight | "没想到..." / "discovered that" / "game changer" | 0.6 | true |

**Proactive Hook** (v1.2.13): `handler.js/ts` auto-detects these 6 signals from `agent:response` events and calls `/ingest` with `review_required: true`. All captured signals appear in the review queue before becoming permanent memories.

**Default behavior**: when in doubt, set `review_required: true`. The user reviews in the dashboard and accepts/rejects. Accepted/rejected history improves future judgment.

**Never capture**: conversation scaffolding ("can you help me"), ephemeral context ("right now I need"), common knowledge, task details that won't recur.

---

## API Endpoints (v1.2.9)

### Core

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/status` | GET | none | Service health + capsule_count + queue_pending + last_sync + semantic_model_loaded |
| `/` | GET | none | Root info + version |
| `/token` | GET | localhost only | Get local API key |
| `/memories` | GET | localhost only | Local memory snapshot (no auth required) |

### Memory Retrieval

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/recall` | GET | Bearer / ?token= | Retrieve relevant memories (`?q=<query>&limit=3&rerank=true&category_path=<path>&use_insights=true`); hybrid mode: `0.4×keyword + 0.6×semantic`; `category_path` 支持前缀匹配（如 `knowledge` 匹配 `knowledge/python`）；`use_insights=true` 时若存在对应路径的 insight 缓存则优先返回压缩摘要（v1.2.17）；返回 category/source_type |
| `/rerank` | POST | Bearer / ?token= | LLM re-rank candidates; body: `{query, memories[]}` → `{memories: [...]}` |
| `/recall/{id}/hit` | PATCH | Bearer / ?token= | Increment capsule access count (updates hotness) |
| `/classify` | GET | none | Topic classify; `?text=<text>` → `{"topics": "tag1,tag2"}`; keyword primary, LLM fallback |

### Memory Writes

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/capsules` | GET | Bearer | List local capsules (`?limit=1-300`); returns category/source_type |
| `/capsules` | POST | Bearer | Create capsule manually |
| `/capsules/{id}` | GET | Bearer | Get capsule by ID |
| `/capsules/{id}` | DELETE | Bearer | Delete capsule |
| `/ingest` | POST | Bearer / ?token= | AI pushes memory → direct capsule if confidence≥0.95+review_required=false, else → queue |
| `/extract` | POST | Bearer / ?token= | Structured LLM extraction; body: `{text, source}` → extracted memories |

### Queue Management

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/queue` | GET | Bearer / ?token= | List pending memories awaiting review |
| `/queue/{id}/approve` | POST | Bearer / ?token= | Accept → writes to capsules |
| `/queue/{id}/reject` | POST | Bearer / ?token= | Dismiss → status=rejected |
| `/queue/{id}/edit` | POST | Bearer / ?token= | Edit then accept → writes modified to capsules |
| `/-review` | GET | Bearer / ?token= | Terminal-friendly queue list (v1.2.9) |
| `/-review/{qid}` | POST | Bearer / ?token= | approve/reject queue item from CLI (v1.2.9) |

### Session Context (proactive capture)

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/freeze` | GET/POST | Bearer / ?token= | Capture current dev session context |
| `/session/summary` | GET | Bearer | Get current session summary |
| `/session/files` | GET | Bearer | Get open files in current session |
| `/session/preload` | GET | Bearer | Get preloaded memories for current scene (v1.2.19) |

### DID Identity (v1.2.20 — multi-device)

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/did/setup` | POST | Bearer | Generate mnemonic + derive device key → save locally (mnemonic shown once) |
| `/did/status` | GET | Bearer | Check if local DID identity is configured |
| `/did/register-device` | POST | Bearer | Register device public key to cloud (cloud account must have DID set up) |

### Sync & Config

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/sync` | GET | Bearer / ?token= | Sync to huper.org cloud |
| `/config` | GET/POST | Bearer / ?token= | Read/set config (auto_sync etc.) |
| `/config/llm` | GET/PUT | Bearer / ?token= | Read/set LLM provider (minimax/openai/claude/local) |

### Localhost-only (security restricted)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/master-password` | POST | Set master_password (stored in OS keychain) |
| `/bind-apikey` | POST | Update huper.org API key in config |

---

## `/ingest` Request Format

```json
POST http://localhost:18998/ingest?token={api_key}
Content-Type: application/json

{
  "memo": "Anke prefers SQLite over Postgres for simpler deployment",
  "context": "During database selection discussion for amber project",
  "category": "decision",
  "tags": "decided,database",
  "source": "claude_cowork",
  "confidence": 0.9,
  "review_required": true,
  "agent_tag": "openclaw"    // v2.0.0: optional, adds #agent:openclaw tag for color-coding in UI
}
```

**Response**:
```json
// Goes to review queue:
{"queued": true, "queue_id": "abc123", "category": "decision", "source_type": "ingest"}

// Written directly (confidence≥0.95 and review_required=false):
{"queued": false, "capsule_id": "xyz456", "category": "decision", "source_type": "ingest"}

// First ingest (capsule_count==0, v2.0.0):
{"queued": false, "capsule_id": "xyz456", "welcome": true, "message": "这是你的第一条记忆！...", "sample_count": 3}
```

---

## LLM Provider Configuration (v1.2.1+)

amber-hunter supports multiple LLM providers:

| Provider | Config key | Notes |
|---------|-----------|-------|
| **MiniMax** | `minimax` | Default; auto-detects API key from OpenClaw config |
| **OpenAI** | `openai` | GPT-4o mini etc. |
| **Claude** | `claude` | Claude 3.5 Haiku etc. |
| **Local** | `local` | Ollama / LM Studio |

```bash
# Set provider
curl -X PUT http://localhost:18998/config/llm?token={api_key} \
  -H "Content-Type: application/json" \
  -d '{"provider": "openai"}'

# Get current provider
curl http://localhost:18998/config/llm?token={api_key}
```

---

## Usage Patterns

### openclaw / Claude Code

```bash
# 1. At conversation start — retrieve relevant context
TOKEN=$(curl -s http://localhost:18998/token | python3 -c "import sys,json; print(json.load(sys.stdin)['api_key'])")
curl "http://localhost:18998/recall?token=$TOKEN&q=YOUR_QUERY&limit=3"

# 2. During conversation — push a memory when something worth keeping surfaces
curl -X POST "http://localhost:18998/ingest?token=$TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "memo": "User decided to use SQLite for simpler ops",
    "category": "decision",
    "tags": "decided",
    "source": "claude_code",
    "confidence": 0.9,
    "review_required": true
  }'

# 3. End of conversation — auto-extract 1-2 key takeaways (confidence=0.7)
curl -X POST "http://localhost:18998/ingest?token=$TOKEN" \
  -d '{"memo":"Summary: ...", "source":"claude_code", "confidence":0.7, "review_required":true}'
```

### ChatGPT (via GPT Action / cloud API)

```bash
curl -X POST https://huper.org/api/ingest \
  -H "Authorization: Bearer USER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "memo": "User mentioned they prefer async Python patterns",
    "category": "dev",
    "tags": "python",
    "source": "chatgpt",
    "confidence": 0.8,
    "review_required": true
  }'
```

---

## Platform Support

| Feature | macOS | Linux | Windows |
|---------|-------|-------|---------|
| amber-hunter service | ✅ LaunchAgent | ✅ systemd | ✅ Planned |
| Keychain storage | ✅ security CLI | ✅ secret-tool / config.json | ✅ cmdkey |
| Semantic search | ✅ | ✅ | ✅ |
| Proactive capture | ✅ | ✅ | ❌ |
| `/freeze` session | ✅ | ✅ | ❌ |

---

## Troubleshooting

```bash
# Service not running
curl http://localhost:18998/status
tail -f ~/.amber-hunter/amber-hunter.log

# Linux: secret-tool not found
sudo apt install libsecret-tools        # Ubuntu/Debian
sudo dnf install libsecret             # Fedora

# Check pending memories
curl "http://localhost:18998/queue?token=$(curl -s localhost:18998/token | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"api_key\"])')"
```

---

## FAQ & Known Issues

### Q: amber-hunter runs on a VPS, not my local Mac. How do I configure the API key?

When amber-hunter is on a **different machine** than your browser, the dashboard's "Generate API Key" button can't auto-bind (it POSTs to `127.0.0.1:18998` which is your local machine, not the VPS).

**Manual setup on the VPS:**

```bash
# Option 1: environment variable (recommended for VPS)
echo 'export AMBER_TOKEN="your_api_key_here"' >> ~/.bashrc
source ~/.bashrc

# Option 2: write directly to config.json
mkdir -p ~/.amber-hunter
cat >> ~/.amber-hunter/config.json << 'EOF'
{"api_token": "your_api_key_here"}
EOF

# Then restart amber-hunter
launchctl unload ~/Library/LaunchAgents/com.huper.amber-hunter.plist
launchctl load ~/Library/LaunchAgents/com.huper.amber-hunter.plist
```

**How to get the API key:**
1. Go to huper.org → Dashboard → Account → API Key
2. Click "Generate API Key" and copy the key immediately (it's only shown once)

### Q: Dashboard shows "尚未生成API Key" even after I generated one

This is a UI bug in older versions. Update to the latest version, or refresh the dashboard page. The key is stored correctly in the database — the display just wasn't updating after generation.

### Q: Sync shows "network unreachable" or "Token 无效" errors

**If amber-hunter is on a VPS:** The `api_token` in config.json may be empty or wrong. Verify:

```bash
cat ~/.amber-hunter/config.json | grep api_token
```

If empty, the VPS can't reach huper.org cloud sync. Manually set the `api_token` as shown in the FAQ above.

**If amber-hunter is on your local Mac:** Make sure `bind-apikey` completed successfully (it runs automatically after generating a key). Check:

```bash
curl -s http://localhost:18998/config | python3 -c "import sys,json; d=json.load(sys.stdin); print('api_token:', d.get('api_key','(not set)')[:10]+'...')"
```

### Q: I generated a new API Key but amber-hunter on VPS stopped syncing

Each key can only be used by **one** amber-hunter instance at a time. If you generate a new key from the dashboard, the old key (still configured on VPS) becomes invalid. Either:
- Copy the new key to the VPS config and restart
- Or keep using the old key (don't click "Generate New Key" unless you mean to rotate it)

### Q: "尚未生成API Key" never goes away on first use

This means you haven't generated an API key yet. Click the orange "生成 API Key" button on the Dashboard → Account → API Key page. The key is shown once — copy it immediately and save it somewhere before leaving the page.

---

## Version History

- **v1.2.37** (2026-04-05): Auto-Train Trigger — `main()` 新增后台训练触发线程：每新增 N 个胶囊触发增量训练（默认阈值 100）；每 6 小时周期性增量训练；cold-start（50+ 胶囊且无模型时等待 10 秒后触发首次训练）；`_spawn_train_if_enabled()` daemon 线程执行 `fine_tune(iterations=100, use_gpt2_pretrain=True, incremental=True)`；训练完成后重置 AmberTrainer 单例并热重载。
- **v1.2.36** (2026-04-06): Rerank Cold-Start — amber_hunter.py 新增 `_rerank_memories_model` / `_rerank_memories` dispatcher；recall 和 `/rerank` 支持 `rerank_engine=auto|model|llm|none`；cold-start 用户自动降级：模型不可用 → LLM → 原顺序；`rerank` bool 参数保留兼容（映射为 `rerank_engine=llm`）；`_rerank_source` 标注每个结果的来源。
- **v1.2.35** (2026-04-05): AmberGPT Trainer 增强 — WAL sessions 文本扩充训练数据；TagHead 多标签分类头（N_EMBED→N_EMBED/2→tag_vocab_size）；GPT-2 预训练权重初始化（`load_pretrained_gpt2()`）；增量训练 checkpoint resume（`ckpt_tag_vocab_size` metadata）；tag vocab 去噪（过滤日期/hash/系统前缀；合并中文↔英文同义词）；`_is_noise_tag()` / `_canonical_tag()` 过滤器；`predict_tags()` 返回 top-k 标签及置信度；tag vocab hot-reload。
- **v1.2.34** (2026-04-05): Local GPT Fine-tune — `core/trainer.py` 实现 AmberGPT（N_HEAD=1, BLOCK_SIZE=96, N_EMBED=256, N_LAYER=6）在你记忆数据上微调；`POST /admin/train` 后台启动训练；`GET /admin/train/status` 检查状态；`GET /admin/train/score` 对 query-memory 对评分；模型保存至 `~/.amber-hunter/models/amber-gpt.pt`；Push Notifications — `POST /notify` 端点调用 huper.org 推送浏览器通知；修复 P2-2 WAL dead code（移除无效 wal_signals 预加载）；queue edit 预生成 cap_id 关联校正记录；`/-review` 支持 `?format=text|json`。
- **v1.2.33** (2026-04-05): Incremental Sync + MCP Server — `get_unsynced_capsules(since=last_sync_at)` 增量同步；`POST /mcp` MCP 协议处理器暴露 7 个工具（recall_memories/create_memory/list_memories/get_memory/update_memory/delete_memory/get_stats）；`GET /stats` 返回胶囊统计/热力分布/WAL 状态/向量统计；`GET /admin/export` 备份导出；`POST /sync/resolve/{id}` 冲突解决。
- **v1.2.32** (2026-04-05): `POST /admin/reindex-vectors` 重建 LanceDB 向量索引；修复 `list_tables()` Pydantic 对象访问（`.tables` 属性）；`local_files_only=True` 避免 HuggingFace 下载超时。
- **v1.2.31** (2026-04-04): Multi-Embedding Provider — `core/embedding.py` 支持 MiniLM / Voyage / OpenAI / Ollama；`core/vector.py` 使用统一 `get_embed()` 接口；recall cooldown 逻辑（30分钟内召回过的胶囊压制分数至 0）。
- **v1.2.30** (2026-04-04): `_auto_tag_local()` 规则化自动标签（18个分类）；`POST /admin/reindex-vectors` 端点；修复 WAL `processed_count` 统计。
- **v1.2.29** (2026-04-04): G1 Self-Correction Loop — `correction_log` SQLite 表记录每次校正事件；`_normalize_tag` 应用用户校正规则（5分钟缓存）；`record_tag_correction` / `record_category_correction` 在 queue edit 时调用；`GET /corrections/stats` 分析校正模式；`POST /corrections/apply` 采纳替换规则。
- **v1.2.28** (2026-04-04): P2-1 Mem0 Auto-extraction — `core/extractor.py` 从对话自动抽取 facts/preferences/decisions；`POST /extract/auto` 高置信直接入库/中置信进队列；`GET /extract/status` 查看抽取统计；结合 WAL 信号 + 偏好提取 + LLM 结构化抽取三重机制。
- **v1.2.27** (2026-04-04): P1-1 Structured User Profile — `user_profile` SQLite 表；`core/profile.py` LLM extraction；`GET /profile` 返回四段画像；`PUT /profile/{section}` 手动更新；`POST /profile/build` 从 session 构建；recall 响应注入 `profile` 字段。
- **v1.2.26** (2026-04-04): P0-2续 WAL GC — `wal_gc(age_hours=24)` 删除已处理条目；`get_wal_stats()` 新增 `processed_count`；懒 GC（>50 条已处理时自动清理）；`POST /wal/gc` 端点支持手动 GC。
- **v1.2.25** (2026-04-04): P0-3 可解释召回 — `_kw_score` 返回 `(score, matched_terms)`；`breakdown` 新增 `matched_terms` + `wal_signal`；`reason` 改为详细自然语言说明（含具体匹配词、语义相似度%、WAL信号类型）。
- **v1.2.24** (2026-04-04): P0-2 WAL 热存储 — `core/wal.py` 新增 Session State WAL 模块；`recall_memories` 返回前检测偏好/决定/修正信号并写入 `~/.amber-hunter/session_wal.jsonl`；新增 `/wal/status` + `/wal/entries` 端点。
- **v1.2.23** (2026-04-04): P0-1 LanceDB 向量搜索 — `core/vector.py` 新增 LanceDB 封装；胶囊入库时同步写向量；recall 优先 LanceDB top_k 检索（0.50权重），on-the-fly 回退；升级 torch 2.8.0。
- **v1.2.22** (2026-04-04): Fix line 1570 bug (`row[2]`→`stored_challenge`) in `/api/did/auth/verify`; add `POST /did/auth/challenge` + `POST /did/auth/sign-challenge` in amber_hunter; fix `did_register_device` to use `get_api_token()`; fix `HOME` reference before definition.
- **v1.2.21** (2026-04-04): D2 DID Challenge-Response Auth + capsule key derivation — `derive_capsule_key` wired into `create_capsule`/`get_capsule` (DID device key → AES-256-GCM, PBKDF2 fallback); `POST /api/did/auth/challenge` + `POST /api/did/auth/verify` endpoints; `require_auth` supports DID token; `device_priv` saved to `did.json`.
- **v1.2.8** (2026-04-01): Fix proactive-check.js — filter log lines from session transcript; memo truncation 60→80 chars.
- **v1.2.4** (2026-04-01): Fix `source_type`/`category` missing in sync payload; `httpx.Client` reuse for sync; `/capsules` limit param; `/memories` new fields.
- **v1.2.3** (2026-04-01): Fix `/recall` semantic search on full corpus; hybrid mode `0.4×keyword + 0.6×semantic`; `/status` enhanced with capsule_count/queue_pending/last_sync.
- **v1.2.1** (2026-03-31): LLM abstraction layer (`core/llm.py`); `/rerank` endpoint; `/classify` LLM fallback; proactive session selection by message count.
- **v1.1.9** (2026-03-31): Universal memory taxonomy (8 life categories); `/ingest` + queue management; `source_type` + `category` fields; ChatGPT GPT Action.
- **v0.9.5** (2026-03-28): amber-proactive V4 — self-contained cron, LLM extraction.
- **v0.8.4** (2026-03-22): E2E encryption, cross-platform keychain, `/memories` no-auth.

---

*Built with 🔒 by [Anke Chen](https://github.com/ankechenlab-node) for the [Huper琥珀](https://huper.org) ecosystem.*
