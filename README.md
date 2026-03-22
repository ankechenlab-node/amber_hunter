# 🌙 Amber-Hunter

> A local perception engine for [Huper琥珀](https://huper.org) — turns your AI collaboration sessions into frozen, searchable personal memories.

**Amber-Hunter** runs on your Mac, constantly watching your OpenClaw agent sessions. When you're ready to freeze "this moment", it captures the conversation context, recent file changes, and stores everything in an encrypted local capsule — ready to be searched and woken anytime.

---

## What It Does

- **Session Capture** — Reads OpenClaw session transcripts, extracts meaningful user/assistant exchanges
- **File Monitoring** — Tracks recently modified workspace files automatically
- **Instant Freeze** — `Cmd+Shift+A` to capture "what am I working on right now"
- **Local Encryption** — AES-256-GCM, master password never leaves your Mac
- **Cloud Sync (optional)** — Encrypts before uploading to your [huper.org](https://huper.org) account

---

## Installation

### Prerequisites

- macOS
- Python 3.10+
- [OpenClaw](https://github.com/openclaw/openclaw)

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/ankechenlab-node/amber_hunter.git
cd amber_hunter

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cat > ~/.amber-hunter/config.json << 'EOF'
{
  "api_key": "your-huper-org-api-key",
  "master_password": "your-local-encryption-password"
}
EOF

# 4. Start the service
python3 amber_hunter.py &

# 5. (Optional) Enable auto-start with LaunchAgent
cp com.huper.amber-hunter.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.huper.amber-hunter.plist
```

### Get Your API Key

1. Go to [huper.org/dashboard](https://huper.org/dashboard)
2. Login → API Key tab → Generate new key
3. Copy the key into `config.json`

---

## Usage

### Freeze via Browser

Open [huper.org](https://huper.org) and click "冻结当下" — amber-hunter pre-fills the modal with your current session context.

### Freeze via API

```bash
# Check status
curl http://localhost:18998/status

# Get session summary
curl http://localhost:18998/session/summary

# Get recent workspace files
curl http://localhost:18998/session/files

# Trigger freeze (returns pre-fill data)
curl -X POST http://localhost:18998/freeze

# List local capsules
curl http://localhost:18998/capsules

# Create a capsule
curl -X POST http://localhost:18998/capsules \
  -H "Content-Type: application/json" \
  -d '{"memo":"Architecture review","content":"Discussed the module structure...","tags":"design,review"}'
```

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET | Service status, session info |
| `/session/summary` | GET | OpenClaw conversation summary |
| `/session/files` | GET | Recently modified workspace files |
| `/freeze` | POST | Trigger freeze, returns pre-fill data |
| `/capsules` | GET | List local capsules |
| `/capsules` | POST | Create a capsule |

---

## Security

- **Master Password** stored in macOS Keychain, never transmitted
- **Capsules** encrypted with AES-256-GCM before any network transmission
- **API Key** only used for huper.org authentication, not for encryption
- **Local-first**: works fully offline without cloud sync

---

## License

MIT License — see [LICENSE](LICENSE)

---

*Built with 🔒 by [Anke Chen](https://github.com/ankechenlab-node) for the [Huper琥珀](https://huper.org) ecosystem.*
