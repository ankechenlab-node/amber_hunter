#!/usr/bin/env python3
"""
Amber-Hunter v0.8.4
Huper琥珀本地感知引擎

v0.8.4 修复：
- 加密：content 字段 AES-256-GCM 加密后存储，salt+nonce 持久化
- 认证：本地 API token 验证（防同一机器上其他进程滥用）
- CORS：仅允许 huper.org
- Keychain：master_password 读不到直接报错，不 fallback 到文件
- Session：正则加了 try/except 保护，失败不影响整体运行
"""

import os, sys, json, time, secrets
from pathlib import Path

# ── 核心模块 ────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from core.crypto import derive_key, encrypt_content, decrypt_content, generate_salt
from core.keychain import (
    get_master_password, set_master_password,
    get_api_token, get_huper_url,
    ensure_config_dir, KEYCHAIN_SVC,
)
from core.db import init_db, insert_capsule, get_capsule, list_capsules, mark_synced
from core.session import get_current_session_key, build_session_summary, get_recent_files
from core.models import CapsuleIn

# ── FastAPI ─────────────────────────────────────────────
import uvicorn
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

HOME = Path.home()
ensure_config_dir()

# ── FastAPI App ────────────────────────────────────────
app = FastAPI(title="Amber Hunter", version="0.8.4")

# CORS：仅允许 huper.org（生产）和 localhost（开发）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://huper.org", "http://localhost:18998", "http://127.0.0.1:18998"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ── 认证 ────────────────────────────────────────────────
def verify_token(authorization: str = Header(None)) -> bool:
    """验证本地 API token，防其他进程滥用"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:]
    stored = get_api_token()
    if not stored or token != stored:
        raise HTTPException(status_code=401, detail="Invalid API token")
    return True

def require_auth(authorization: str = Header(None)):
    """FastAPI 依赖：验证本地 token"""
    verify_token(authorization)
    return True

# ── Session 读取（无认证，供前端读取）──────────────────
@app.get("/session/summary")
def session_summary():
    """OpenClaw session 对话摘要（无需认证）"""
    session_key = get_current_session_key()
    if not session_key:
        return {"session_key": None, "summary": "未找到活跃 session", "messages": []}
    return build_session_summary(session_key)

@app.get("/session/files")
def session_files():
    """Workspace 最近变更文件（无需认证）"""
    files = get_recent_files(limit=10)
    return {"files": files, "workspace": str(HOME / ".openclaw" / "workspace")}

@app.post("/freeze")
def trigger_freeze(authorization: str = Header(None)):
    """触发 freeze：返回预填数据（需认证）"""
    verify_token(authorization)
    session_key = get_current_session_key()
    session_data = build_session_summary(session_key) if session_key else {}
    files = get_recent_files(limit=5)
    prefill = session_data.get("last_user_message", "") or ""
    if files:
        file_names = "; ".join([f"{f['path']}" for f in files])
        prefill = f"{prefill}\n\n相关文件：{file_names}" if prefill else file_names
    return {
        "session_key": session_key,
        "prefill": prefill[:500],
        "summary": session_data.get("summary", ""),
        "files": files[:5],
        "timestamp": time.time(),
    }

# ── 胶囊 CRUD（需认证）──────────────────────────────────
@app.get("/capsules")
def list_capsules_handler(authorization: str = Header(None)):
    """列出本地胶囊（需认证）"""
    verify_token(authorization)
    capsules = list_capsules(limit=50)
    # 不暴露加密字段
    return {
        "capsules": [
            {
                "id": c["id"],
                "memo": c["memo"],
                "tags": c["tags"],
                "session_id": c["session_id"],
                "window_title": c["window_title"],
                "created_at": c["created_at"],
                "synced": bool(c["synced"]),
                "has_encrypted_content": bool(c.get("salt")),
            }
            for c in capsules
        ]
    }

@app.post("/capsules")
def create_capsule(capsule: CapsuleIn, authorization: str = Header(None)):
    """
    创建本地胶囊（加密存储）。
    content 字段使用 AES-256-GCM 加密后存入 SQLite。
    salt 和 nonce 随记录保存。
    """
    verify_token(authorization)
    master_pw = get_master_password()
    if not master_pw:
        raise HTTPException(
            status_code=401,
            detail="未设置 master_password，请先在 huper.org/dashboard 配置"
        )

    capsule_id = secrets.token_hex(8)
    now = time.time()

    if capsule.content:
        # ── 加密 content ────────────────────────────────
        salt = generate_salt()
        key = derive_key(master_pw, salt)
        ciphertext, nonce = encrypt_content(capsule.content.encode("utf-8"), key)
        import hashlib
        content_hash = hashlib.sha256(ciphertext).hexdigest()
        import base64
        salt_b64   = base64.b64encode(salt).decode()
        nonce_b64  = base64.b64encode(nonce).decode()
        ct_b64     = base64.b64encode(ciphertext).decode()
    else:
        salt_b64 = nonce_b64 = ct_b64 = content_hash = None
        ct_b64 = capsule.content  # 空内容存空字符串

    insert_capsule(
        capsule_id=capsule_id,
        memo=capsule.memo,
        content=ct_b64,
        tags=capsule.tags,
        session_id=capsule.session_id,
        window_title=capsule.window_title,
        url=capsule.url,
        created_at=now,
        salt=salt_b64,
        nonce=nonce_b64,
        encrypted_len=len(ct_b64) if ct_b64 else 0,
        content_hash=content_hash,
    )

    return {"id": capsule_id, "created_at": now, "synced": False}

@app.get("/capsules/{capsule_id}")
def get_capsule_handler(capsule_id: str, authorization: str = Header(None)):
    """获取胶囊详情（含解密）"""
    verify_token(authorization)
    record = get_capsule(capsule_id)
    if not record:
        raise HTTPException(status_code=404, detail="胶囊不存在")

    master_pw = get_master_password()
    content = record["content"] or ""

    if record.get("salt") and record.get("nonce") and content:
        import base64
        try:
            salt = base64.b64decode(record["salt"])
            nonce = base64.b64decode(record["nonce"])
            ciphertext = base64.b64decode(content)
            key = derive_key(master_pw, salt)
            plaintext = decrypt_content(ciphertext, key, nonce)
            if plaintext:
                content = plaintext.decode("utf-8")
            else:
                content = "[解密失败：密钥错误]"
        except Exception as e:
            content = f"[解密失败：{e}]"

    return {
        "id": record["id"],
        "memo": record["memo"],
        "content": content,
        "tags": record["tags"],
        "session_id": record["session_id"],
        "window_title": record["window_title"],
        "url": record.get("url"),
        "created_at": record["created_at"],
        "synced": bool(record["synced"]),
    }

@app.delete("/capsules/{capsule_id}")
def delete_capsule(capsule_id: str, authorization: str = Header(None)):
    """删除胶囊（需认证）"""
    verify_token(authorization)
    from core.db import get_capsule
    if not get_capsule(capsule_id):
        raise HTTPException(status_code=404, detail="胶囊不存在")
    import sqlite3
    conn = sqlite3.connect(str(HOME / ".amber-hunter" / "hunter.db"))
    c = conn.cursor()
    c.execute("DELETE FROM capsules WHERE id=?", (capsule_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

# ── 服务状态（无需认证）────────────────────────────────
@app.get("/status")
def get_status():
    """服务状态"""
    session_key = get_current_session_key()
    master_pw = get_master_password()
    api_token = get_api_token()
    return {
        "running": True,
        "version": "0.8.4",
        "session_key": session_key,
        "has_master_password": bool(master_pw),
        "has_api_token": bool(api_token),
        "workspace": str(HOME / ".openclaw" / "workspace"),
        "huper_url": get_huper_url(),
    }

@app.get("/")
def root():
    return {"service": "amber-hunter", "version": "0.8.4", "docs": "/docs"}

# ── 启动 ────────────────────────────────────────────────
def main():
    init_db()
    print("🌙 Amber-Hunter v0.8.4 启动")
    print(f"   Session目录: {HOME / '.openclaw' / 'agents'}")
    print(f"   Workspace:   {HOME / '.openclaw' / 'workspace'}")
    print(f"   数据库:      {HOME / '.amber-hunter' / 'hunter.db'}")
    print(f"   API:        http://localhost:18998/")
    print(f"   CORS:       https://huper.org + localhost")
    print(f"   认证:       本地 API token")
    uvicorn.run(app, host="127.0.0.1", port=18998, log_level="warning")

if __name__ == "__main__":
    main()
