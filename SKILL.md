# Amber-Hunter Skill
> Local memory engine for Huper琥珀
> Version: 0.9.0 | 2026-03-26

---

> amber-hunter runs on the user's local machine (Mac / Linux / Windows). Agents communicate with it via `localhost:18998`.

---

## What It Does

Amber-Hunter is the **capture layer** of Huper琥珀 — free, open-source, and zero-barrier. It lets users experience "freeze this moment" AI collaboration memory with no account required.

- **Free & open** — works immediately after install, no account needed
- **Core value**: freeze the current moment — make AI collaboration memory retrievable
- **Optional upgrade**: register at huper.org to unlock cross-device cloud sync

---

## Core Features

- **Session capture** — reads OpenClaw / Claude live conversation history as freeze content
- **File monitoring** — tracks recently modified files in the workspace
- **Local encrypted storage** — AES-256-GCM encryption, master_password stored in OS keychain
- **Active Recall** — `/recall?q=<query>` retrieves relevant past memories before responding; supports keyword + semantic (sentence-transformers) search
- **Proactive Memory Capture** — background agent auto-detects significant moments (corrections, decisions, preferences, discoveries) every 10 min; completely silent
- **Topic Classification** — `/classify` endpoint categorizes capsules into 16 topics with vector fine-tuning; used by amber-proactive
- **Memory layer** — paragraph-level summarization (20-message window) + preference extraction (24 signal keywords) per freeze
- **Cloud sync** — encrypted upload to huper.org (optional, requires account)

---

## API Endpoints (v0.9.0)

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/status` | GET | none | Service status |
| `/memories` | GET | **none** | Local memory snapshot (localhost only) |
| `/token` | GET | localhost | Get local API key |
| `/session/summary` | GET | none | OpenClaw/Claude session summary |
| `/session/files` | GET | none | Recent workspace files |
| `/freeze` | GET/POST | Bearer or ?token= | Trigger freeze |
| `/capsules` | GET | Bearer | List local capsules |
| `/capsules` | POST | Bearer | Create capsule |
| `/capsules/{id}` | GET | Bearer | Read capsule |
| `/capsules/{id}` | DELETE | Bearer | Delete capsule |
| `/recall` | GET | Bearer or ?token= | Retrieve relevant memories (`?q=<query>&limit=3`) |
| `/classify` | GET/POST | Bearer or ?token= | Topic classification (16 topics, vector-tuned) |
| `/sync` | GET | Bearer or ?token= | Cloud sync (requires account) |
| `/config` | GET/POST | Bearer or ?token= | Read/set config (incl. auto_sync) |
| `/master-password` | POST | localhost | Set master_password |

---

## Authentication

### Bearer Header (server-to-server)
```
Authorization: Bearer <api_key>
```

### Query Parameter (browser cross-origin compatible)
```
GET /freeze?token=<api_key>
GET /sync?token=<api_key>
GET /recall?token=<api_key>&q=your+query
```

> Note: Browsers block `Authorization` headers on HTTPS→HTTP localhost requests, so the frontend uses query parameters instead.

---

## Installation

### Platform Support

| Platform | Auto-start | Keychain |
|----------|-----------|---------| 
| **macOS** | LaunchAgent (launchctl) | macOS Keychain |
| **Linux** | systemd user service | GNOME Keyring (secret-tool) |
| **Windows** | Task Scheduler (schtasks) | Windows Credential Manager |

### Prerequisites
- macOS / Linux / Windows (user's local machine)
- Python 3.10+

### Quick Install (run on the user's local machine)
```bash
bash ~/.openclaw/skills/amber-hunter/install.sh
```

### Manual Install
```bash
# 1. Install dependencies
pip install -r ~/.openclaw/skills/amber-hunter/requirements.txt

# 2. Start service (no account needed)
python3 ~/.openclaw/skills/amber-hunter/amber_hunter.py &

# 3. Verify
curl http://localhost:18998/status
curl http://localhost:18998/memories
```

---

## Auto-start

`install.sh` configures this automatically.

| Platform | Command |
|----------|---------| 
| macOS | `launchctl load ~/Library/LaunchAgents/com.huper.amber-hunter.plist` |
| Linux | `systemctl --user start amber-hunter` |
| Windows | Task Scheduler entry created automatically at login |

---

## Config & Storage

- `~/.amber-hunter/config.json` — API key and Huper URL
- `~/.amber-hunter/hunter.db` — local capsule SQLite database
- `~/.amber-hunter/amber-hunter.log` — service log
- **OS keychain** (macOS Keychain / Linux GNOME Keyring / Windows Credential Manager) — stores `master_password`, never written to disk

---

## Usage

### No account (immediate use)
```bash
# View local memories
curl http://localhost:18998/memories

# Active recall — retrieve relevant past memories
curl "http://localhost:18998/recall?token=<api_key>&q=what+did+we+decide+about+auth"

# Get API token for OpenClaw/Claude integration
curl http://localhost:18998/token
```

### Optional: register huper.org for cloud sync
1. Go to https://huper.org and create an account
2. Get your API key from the dashboard, add it to `~/.amber-hunter/config.json`
3. Set a master_password (local encryption key — never uploaded)
4. Enable cloud sync to access memories across devices

---

## Troubleshooting

### amber-hunter not connecting
```bash
curl http://localhost:18998/status
python3 ~/.openclaw/skills/amber-hunter/amber_hunter.py &
tail -f ~/.amber-hunter/amber-hunter.log
```

### Linux: secret-tool not installed
```bash
# Ubuntu/Debian
sudo apt install libsecret-tools
# Fedora
sudo dnf install libsecret
# Arch
sudo pacman -S libsecret
```

---

## Version History

- **v0.8.9** (2026-03-26): Session capture targets Anke Telegram session; system message filtering; OpenClaw queue/exec cleanup; `get_current_session_key()` Telegram priority over Claude Cowork
- **v0.8.9** (2026-03-23): Topic classification system (16 topics + vector fine-tuning + sensitive keyword override), `/classify` endpoint for amber-proactive
- **v0.8.8** (2026-03-23): Memory layer refactor — paragraph-level summarization (20-message window), preference extraction (24 signal keywords), `preferences` field in freeze
- **v0.8.7** (2026-03-22): Removed VPS warning, English-only SKILL.md, localhost-only security annotations
- **v0.8.4** (2026-03-22): Cross-platform support (macOS/Linux/Windows), E2E encryption, /memories no-auth local access, Claude Cowork session support

---

*Built with 🔒 by [Anke Chen](https://github.com/ankechenlab-node) for the [Huper琥珀](https://huper.org) ecosystem.*
