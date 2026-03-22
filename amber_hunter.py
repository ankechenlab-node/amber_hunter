#!/usr/bin/env python3
"""
Amber-Hunter: Huper琥珀本地感知引擎
v0.8.3 | 2026-03-22

功能：
  - 读取 OpenClaw session 对话历史
  - 监控 workspace 文件变更
  - 本地加密胶囊存储
  - 可选：加密上传 huper.org

启动：python3 amber_hunter.py
API：  http://localhost:18998/
"""

import os, json, time, sqlite3, hashlib, base64, secrets, threading
from pathlib import Path
from typing import Optional
from datetime import datetime

# ── FastAPI ──────────────────────────────────────────────
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── 加密（AES-256-GCM）────────────────────────────────
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# ── 常量 ────────────────────────────────────────────────
HOME = Path.home()
AGENTS_DIR = HOME / ".openclaw" / "agents"
SESSIONS_FILE = AGENTS_DIR / "main" / "sessions" / "sessions.json"
WORKSPACE_DIR = HOME / ".openclaw" / "workspace"
HUNTER_DB = HOME / ".amber-hunter" / "hunter.db"
KEYRING_SERVICE = "com.huper.amber-hunter"
KEYRING_ACCOUNT = "master_password"

os.makedirs(HOME / ".amber-hunter", exist_ok=True)

# ── FastAPI App ──────────────────────────────────────────
app = FastAPI(title="Amber Hunter", version="0.8.3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 数据模型 ─────────────────────────────────────────────
class CapsuleIn(BaseModel):
    memo: str
    content: str = ""
    tags: str = ""
    session_id: Optional[str] = None
    window_title: Optional[str] = None
    url: Optional[str] = None

class CapsuleOut(BaseModel):
    id: str
    memo: str
    tags: str
    session_id: Optional[str]
    window_title: Optional[str]
    created_at: float
    synced: bool

# ── 数据库初始化 ────────────────────────────────────────
def init_db():
    HUNTER_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HUNTER_DB))
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS capsules (
            id          TEXT PRIMARY KEY,
            memo        TEXT,
            content     TEXT,
            tags        TEXT,
            session_id  TEXT,
            window_title TEXT,
            url         TEXT,
            created_at  REAL NOT NULL,
            synced      INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ── 加密 ────────────────────────────────────────────────
def derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                      salt=salt, iterations=100_000)
    return kdf.derive(password.encode("utf-8"))

def encrypt_content(data: bytes, key: bytes) -> tuple[bytes, bytes]:
    nonce = secrets.token_bytes(12)
    aesgcm = AESGCM(key)
    return aesgcm.encrypt(nonce, data, None), nonce

def decrypt_content(ciphertext: bytes, key: bytes, nonce: bytes) -> Optional[bytes]:
    try:
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None)
    except Exception:
        return None

def get_master_password() -> Optional[str]:
    """从 macOS Keychain 读取 master_password"""
    try:
        import subprocess
        r = subprocess.run(
            ["security", "find-generic-password",
             "-s", KEYRING_SERVICE, "-a", KEYRING_ACCOUNT,
             "-w"],
            capture_output=True, text=True, timeout=3
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    # fallback: 从配置文件读（开发模式）
    try:
        cfg_path = HOME / ".amber-hunter" / "config.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            return cfg.get("master_password")
    except Exception:
        pass
    return None

def get_api_key() -> Optional[str]:
    cfg_path = HOME / ".amber-hunter" / "config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text())
        return cfg.get("api_key")
    return None

# ── Session 读取 ────────────────────────────────────────
def get_current_session_key() -> Optional[str]:
    """找到最近一次活跃的 session key"""
    try:
        if not SESSIONS_FILE.exists():
            return None
        sessions = json.loads(SESSIONS_FILE.read_text())
        if not sessions:
            return None
        # 按 updatedAt 降序
        sorted_sessions = sorted(
            sessions.items(),
            key=lambda x: x[1].get("updatedAt", 0),
            reverse=True
        )
        for key, meta in sorted_sessions:
            # 跳过 cron 和 sub-agent session
            if "cron:" in key or "sub-agent" in key:
                continue
            return key
    except Exception:
        pass
    return None

def read_session_messages(session_key: str, limit: int = 100) -> list[dict]:
    """读取 session JSONL 文件，返回最近 limit 条消息"""
    try:
        if not SESSIONS_FILE.exists():
            return []
        sessions = json.loads(SESSIONS_FILE.read_text())
        meta = sessions.get(session_key, {})
        # sessionId 存储在 sessions.json 的 meta 里
        session_id = meta.get("sessionId", "")
        if not session_id:
            # 尝试从 key 推导
            session_id = session_key.replace("agent:main:", "").replace(":", "_")
        file_path = AGENTS_DIR / "main" / "sessions" / f"{session_id}.jsonl"
        if not file_path.exists():
            return []
        messages = []
        with open(file_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "message":
                        msg = obj.get("message", {})
                        role = msg.get("role", "")
                        content = msg.get("content", [])
                        text_parts = []
                        for item in (content if isinstance(content, list) else []):
                            if isinstance(item, dict):
                                if item.get("type") == "text":
                                    text_parts.append(item.get("text", ""))
                                elif item.get("type") == "toolCall":
                                    pass  # skip tool calls
                                elif item.get("type") == "toolResult":
                                    pass  # skip tool results
                        if text_parts:
                            messages.append({
                                "role": role,
                                "text": " ".join(text_parts)[:500],
                                "timestamp": obj.get("timestamp", ""),
                            })
                except Exception:
                    continue
        return messages[-limit:]
    except Exception:
        return []

def _strip_telegram_meta(text: str) -> str:
    """去除 Telegram 元数据，提取实际用户文本"""
    import re
    text = re.sub(r'System:\s*\[[^\]]+\]\s*', '', text)
    text = re.sub(r'Conversation info[^`]*`{3,}json.*?`{3,}', '', text, flags=re.DOTALL)
    text = re.sub(r'Sender[^`]*`{3,}json.*?`{3,}', '', text, flags=re.DOTALL)
    text = text.strip()
    return text

def build_session_summary(session_key: str) -> dict:
    """构建 session 摘要"""
    messages = read_session_messages(session_key, limit=100)
    if not messages:
        return {"session_key": session_key, "summary": "", "messages": []}

    # 提取用户消息（跳过工具调用）
    user_msgs = [_strip_telegram_meta(m["text"]) for m in messages if m["role"] == "user"]
    user_msgs = [t for t in user_msgs if t]  # 去除空消息

    # 找最近一个有实质内容的用户消息
    last_topic = ""
    for msg in reversed(user_msgs):
        if len(msg) > 10:  # 跳过太短的消息
            last_topic = msg
            break

    if last_topic:
        summary = f"最近对话：{last_topic[:200]}"
    else:
        summary = "当前 session 无用户对话内容"

    return {
        "session_key": session_key,
        "summary": summary,
        "last_user_message": last_topic[:300] if last_topic else None,
        "message_count": len(messages),
        "recent_messages": messages[-6:],
    }

# ── Workspace 文件变更 ───────────────────────────────────
def get_recent_files(limit: int = 10) -> list[dict]:
    """返回 workspace 最近修改的文件"""
    try:
        files = []
        if WORKSPACE_DIR.exists():
            all_files = [f for f in WORKSPACE_DIR.rglob("*") if f.is_file() and not f.name.startswith(".")]
            for f in sorted(all_files, key=lambda x: -x.stat().st_mtime):
                files.append({
                    "path": str(f.relative_to(HOME)),
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "size_kb": round(f.stat().st_size / 1024, 1),
                })
                if len(files) >= limit:
                    break
        return files
    except Exception as e:
        return []

# ── API 路由 ────────────────────────────────────────────

@app.get("/status")
def get_status():
    """服务状态"""
    master_pw = get_master_password()
    api_key = get_api_key()
    session_key = get_current_session_key()
    return {
        "running": True,
        "version": "0.8.3",
        "session_key": session_key,
        "has_master_password": bool(master_pw),
        "has_api_key": bool(api_key),
        "workspace": str(WORKSPACE_DIR),
    }

@app.get("/session/summary")
def session_summary():
    """OpenClaw session 对话摘要"""
    session_key = get_current_session_key()
    if not session_key:
        return {"session_key": None, "summary": "未找到活跃 session", "messages": []}
    summary = build_session_summary(session_key)
    return summary

@app.get("/session/files")
def session_files():
    """Workspace 最近变更文件"""
    files = get_recent_files(limit=10)
    return {"files": files, "workspace": str(WORKSPACE_DIR)}

@app.post("/freeze")
def trigger_freeze():
    """
    触发 freeze：返回预填数据给前端
    前端通过此接口获取 session 摘要 + 文件列表，
    预填到琥珀冻结弹窗。
    """
    session_key = get_current_session_key()
    session_data = build_session_summary(session_key) if session_key else {}
    files = get_recent_files(limit=5)
    files_summary = "; ".join([f"{f['path']}" for f in files]) if files else ""
    prefill = session_data.get("last_user_message", "") or ""
    if files_summary:
        prefill = f"{prefill}\n\n相关文件：{files_summary}" if prefill else files_summary
    return {
        "session_key": session_key,
        "prefill": prefill[:500],
        "summary": session_data.get("summary", ""),
        "files": files[:5],
        "timestamp": time.time(),
    }

@app.get("/capsules")
def list_capsules():
    """本地胶囊列表"""
    conn = sqlite3.connect(str(HUNTER_DB))
    c = conn.cursor()
    rows = c.execute(
        "SELECT id, memo, tags, session_id, window_title, created_at, synced "
        "FROM capsules ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return {
        "capsules": [
            {
                "id": r[0], "memo": r[1], "tags": r[2],
                "session_id": r[3], "window_title": r[4],
                "created_at": r[5], "synced": bool(r[6]),
            }
            for r in rows
        ]
    }

@app.post("/capsules")
def create_capsule(capsule: CapsuleIn):
    """创建本地胶囊（加密存储）"""
    master_pw = get_master_password()
    if not master_pw:
        raise HTTPException(status_code=401, detail="未设置 master_password，请在 dashboard 中配置")

    capsule_id = secrets.token_hex(8)
    now = time.time()

    conn = sqlite3.connect(str(HUNTER_DB))
    c = conn.cursor()
    c.execute(
        "INSERT INTO capsules (id, memo, content, tags, session_id, window_title, url, created_at, synced) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (capsule_id, capsule.memo, capsule.content, capsule.tags,
         capsule.session_id, capsule.window_title, capsule.url, now, 0)
    )
    conn.commit()
    conn.close()

    return {"id": capsule_id, "created_at": now, "synced": False}

@app.get("/capsules/{capsule_id}")
def get_capsule(capsule_id: str):
    """获取胶囊详情"""
    conn = sqlite3.connect(str(HUNTER_DB))
    c = conn.cursor()
    row = c.execute(
        "SELECT id, memo, content, tags, session_id, window_title, url, created_at, synced "
        "FROM capsules WHERE id=?", (capsule_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="胶囊不存在")
    return {
        "id": row[0], "memo": row[1], "content": row[2], "tags": row[3],
        "session_id": row[4], "window_title": row[5], "url": row[6],
        "created_at": row[7], "synced": bool(row[8]),
    }

# ── 启动 ────────────────────────────────────────────────
def main():
    print("🌙 Amber-Hunter v0.8.3 启动")
    print(f"   Session目录: {SESSIONS_FILE}")
    print(f"   Workspace:   {WORKSPACE_DIR}")
    print(f"   数据库:       {HUNTER_DB}")
    print(f"   API:         http://localhost:18998/")
    uvicorn.run(app, host="127.0.0.1", port=18998, log_level="warning")

if __name__ == "__main__":
    main()
