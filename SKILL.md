# Amber-Hunter Skill
> Universal AI memory backend for Huper琥珀
> Version: 1.1.9 | 2026-03-31

---

> amber-hunter runs on the user's local machine (Mac / Linux / Windows). Local AI clients communicate via `localhost:18998`. External AI clients (ChatGPT, Claude.ai) use the cloud API at `huper.org/api`.

---

## What It Does

Amber-Hunter is the **capture and recall layer** of Huper琥珀 — a personal memory protocol that works across any AI client and any platform.

- **Free & open** — works immediately after install, no account needed
- **Universal capture** — works for developers AND everyday life memories
- **AI-initiated writes** — any AI can push memories via `/ingest`; user reviews and approves
- **Active recall** — `/recall?q=<query>` retrieves relevant past memories before responding
- **E2E encrypted** — AES-256-GCM, master_password stored in OS keychain, never uploaded
- **Cross-platform** — macOS / Windows / Linux (desktop + headless server)
- **Cloud sync** — optional, encrypted upload to huper.org for cross-device access

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
| User explicitly asks to save | "记住这个" / "save this" | 1.0 | false |
| Clear decision made | "决定用 SQLite" / "we're going with plan B" | 0.9 | true |
| Preference expressed | "我更喜欢..." / "I prefer TypeScript" | 0.85 | true |
| Personal fact revealed | name, location, job, relationship | 0.8 | true |
| End-of-conversation summary | AI extracts 1-2 key takeaways | 0.7 | true |
| AI judges it might be useful | general insight or observation | 0.6 | true |

**Default behavior**: when in doubt, set `review_required: true`. The user reviews in the dashboard and accepts/rejects. Accepted/rejected history improves future judgment.

**Never capture**: conversation scaffolding ("can you help me"), ephemeral context ("right now I need"), common knowledge, task details that won't recur.

---

## API Endpoints (v1.1.9)

### Core

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/status` | GET | none | Service health |
| `/memories` | GET | none (localhost) | Local memory snapshot |
| `/token` | GET | localhost only | Get local API key |
| `/recall` | GET | Bearer / ?token= | Retrieve relevant memories (`?q=<query>&limit=3`) |
| `/freeze` | GET/POST | Bearer / ?token= | Capture current dev session context |
| `/capsules` | GET | Bearer | List local capsules |
| `/capsules` | POST | Bearer | Create capsule manually |
| `/sync` | GET | Bearer / ?token= | Sync to huper.org cloud |
| `/config` | GET/POST | Bearer / ?token= | Read/set config (auto_sync etc.) |

### New in v1.1.9 — AI Memory Writes

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/ingest` | POST | Bearer / ?token= | AI pushes a memory → queue or direct capsule |
| `/queue` | GET | Bearer / ?token= | List pending memories awaiting user review |
| `/queue/{id}/approve` | POST | Bearer / ?token= | Accept → writes to capsules |
| `/queue/{id}/reject` | POST | Bearer / ?token= | Dismiss → status=rejected |
| `/queue/{id}/edit` | POST | Bearer / ?token= | Edit then accept → writes modified to capsules |

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
  "tags": "decided",
  "source": "claude_cowork",
  "confidence": 0.9,
  "review_required": true
}
```

**Response**:
```json
// Goes to review queue:
{"queued": true, "queue_id": "abc123", "message": "Added to review queue"}

// Written directly (confidence≥0.95 and review_required=false):
{"queued": false, "capsule_id": "xyz456", "message": "Saved directly"}
```

---

## Usage Patterns by Client

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

### Claude in Cowork

Claude in Cowork uses Desktop Commander to call localhost:

```python
# Push a memory
mcp__Desktop_Commander__start_process(
  command='curl -X POST "http://localhost:18998/ingest?token=TOKEN" \
    -H "Content-Type: application/json" \
    -d \'{"memo":"...","category":"decision","confidence":0.9,"review_required":true,"source":"claude_cowork"}\''
)
```

### ChatGPT (via GPT Action / cloud API)

Users configure their huper.org API key in the GPT:

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

GPT Action OpenAPI spec: see `tasks/gpt-action-schema.yaml` in the amber-site repo.

---

## Platform Support

| Platform | Auto-start | Keychain | /ingest | /api/ingest |
|----------|-----------|---------|---------|------------|
| **macOS** | LaunchAgent (launchctl) | macOS Keychain | ✅ | ✅ |
| **Windows** | Task Scheduler | Windows Credential Manager (pywin32) | ✅ | ✅ |
| **Linux desktop** | systemd user service | GNOME Keyring (secret-tool) | ✅ | ✅ |
| **Linux headless (VPS)** | systemd | config.json fallback (明文) | N/A | ✅ |

### Installation

```bash
# macOS / Linux
bash ~/.openclaw/skills/amber-hunter/install.sh

# Verify
curl http://localhost:18998/status
curl http://localhost:18998/memories
```

### Auto-start commands

| Platform | Command |
|----------|---------|
| macOS | `launchctl load ~/Library/LaunchAgents/com.huper.amber-hunter.plist` |
| Linux | `systemctl --user start amber-hunter` |
| Windows | Configured automatically by install.sh via schtasks |

---

## Config & Storage

- `~/.amber-hunter/config.json` — API key, Huper URL, other settings
- `~/.amber-hunter/hunter.db` — local SQLite (capsules + memory_queue)
- `~/.amber-hunter/amber-hunter.log` — service log
- **OS keychain** — stores `master_password`, never written to disk in production

---

## Troubleshooting

```bash
# Service not running
curl http://localhost:18998/status
tail -f ~/.amber-hunter/amber-hunter.log

# Linux: secret-tool not found
sudo apt install libsecret-tools        # Ubuntu/Debian
sudo dnf install libsecret             # Fedora
sudo pacman -S libsecret               # Arch

# Windows: pywin32 not installed (Credential Manager fallback to config.json)
pip install pywin32

# Check pending memories
curl "http://localhost:18998/queue?token=$(curl -s localhost:18998/token | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"api_key\"])')"
```

---

## Version History

- **v1.1.9** (2026-03-31): Universal memory taxonomy (8 life categories + tags); `/ingest` endpoint for AI-initiated writes; `memory_queue` table + approve/reject/edit flow; `source_type` + `category` DB fields; dashboard review queue card; ChatGPT GPT Action schema; SKILL.md multi-client guide; `_background_sync()` + 30min periodic scheduler; Private Network Access CORS headers.
- **v0.9.6** (2026-03-28): `/bind-apikey` localhost endpoint; dashboard retry-on-401 token refresh; sync timeout 120s.
- **v0.9.5** (2026-03-28): amber-proactive V4 — self-contained cron, LLM extraction, 15min interval.
- **v0.9.2** (2026-03-26): Fix semantic search — sentence-transformers + numpy; remove unused mac-keychain.
- **v0.9.1** (2026-03-26): Remove hardcoded personal Telegram session ID; generic session capture.
- **v0.8.4** (2026-03-22): Cross-platform support (macOS/Linux/Windows), E2E encryption, /memories no-auth, Claude Cowork session.

---

*Built with 🔒 by [Anke Chen](https://github.com/ankechenlab-node) for the [Huper琥珀](https://huper.org) ecosystem.*
