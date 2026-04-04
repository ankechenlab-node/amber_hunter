# Amber-Hunter Skill
> Gives any AI client long-term memory — captures, encrypts, and recalls personal context across sessions
> Version: 1.2.26 | 2026-04-04

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

## Version History

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
