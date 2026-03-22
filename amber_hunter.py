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
from starlette.middleware.cors import CORSMiddleware as StarletteCORSMiddleware
from starlette.responses import Response
from pydantic import BaseModel

HOME = Path.home()
ensure_config_dir()

# ── FastAPI App ────────────────────────────────────────
app = FastAPI(title="Amber Hunter", version="0.8.4")

# CORS：仅允许 huper.org（生产）和 localhost（开发）
# 使用 Starlette CORS middleware（更稳定）
app.add_middleware(
    StarletteCORSMiddleware,
    allow_origins=["https://huper.org", "http://localhost:18998", "http://127.0.0.1:18998"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ── 认证 ────────────────────────────────────────────────
def verify_token(authorization: str = Header(None)) -> bool:
    """验证本地 API token，防同一机器上其他进程滥用"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:]
    stored = get_api_token()
    if not stored or token != stored:
        raise HTTPException(status_code=401, detail="Invalid API token")
    return True

# ── 通用 CORS 响应头 ──────────────────────────────────
ALLOWED_ORIGINS = [
    "https://huper.org",
    "http://localhost:18998",
    "http://127.0.0.1:18998",
]

def add_cors_headers(request: Request):
    """手动给 Response 添加 CORS 头"""
    origin = request.headers.get("origin", "")
    if origin in ALLOWED_ORIGINS:
        return {"access-control-allow-origin": origin, "access-control-allow-credentials": "true"}
    return {}

# ── Session 读取（无认证，供前端读取）──────────────────
@app.get("/session/summary")
def session_summary(request: Request):
    headers = add_cors_headers(request)
    session_key = get_current_session_key()
    if not session_key:
        return JSONResponse({"session_key": None, "summary": "未找到活跃 session", "messages": []}, headers=headers)
    return JSONResponse(build_session_summary(session_key), headers=headers)

@app.get("/session/files")
def session_files(request: Request):
    headers = add_cors_headers(request)
    files = get_recent_files(limit=10)
    return JSONResponse({
        "files": files,
        "workspace": str(HOME / ".openclaw" / "workspace")
    }, headers=headers)

@app.api_route("/freeze", methods=["GET", "POST", "OPTIONS"])
def trigger_freeze(request: Request, authorization: str = Header(None)):
    """触发 freeze：返回预填数据（需认证）"""
    # 处理 CORS preflight
    if request.method == "OPTIONS":
        h = add_cors_headers(request)
        h["access-control-allow-methods"] = "GET, POST, OPTIONS"
        h["access-control-allow-headers"] = "Authorization, Content-Type"
        return JSONResponse({}, headers=h)

    verify_token(authorization)
    session_key = get_current_session_key()
    session_data = build_session_summary(session_key) if session_key else {}
    files = get_recent_files(limit=5)
    prefill = session_data.get("last_user_message", "") or ""
    if files:
        file_names = "; ".join([f"{f['path']}" for f in files])
        prefill = f"{prefill}\n\n相关文件：{file_names}" if prefill else file_names

    h = add_cors_headers(request)
    return JSONResponse({
        "session_key": session_key,
        "prefill": prefill[:500],
        "summary": session_data.get("summary", ""),
        "files": files[:5],
        "timestamp": time.time(),
    }, headers=h)

# ── 胶囊 CRUD（需认证）──────────────────────────────────
@app.get("/capsules")
def list_capsules_handler(authorization: str = Header(None), request: Request = None):
    verify_token(authorization)
    capsules = list_capsules(limit=50)
    h = add_cors_headers(request) if request else {}
    return JSONResponse({
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
    }, headers=h)

@app.post("/capsules")
def create_capsule(capsule: CapsuleIn, authorization: str = Header(None), request: Request = None):
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
        # ── 加密 content ──────────────────────────────
        salt = generate_salt()
        key = derive_key(master_pw, salt)
        ciphertext, nonce = encrypt_content(capsule.content.encode("utf-8"), key)
        import hashlib, base64
        content_hash = hashlib.sha256(ciphertext).hexdigest()
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

    h = add_cors_headers(request) if request else {}
    return JSONResponse({"id": capsule_id, "created_at": now, "synced": False}, headers=h)

@app.get("/capsules/{capsule_id}")
def get_capsule_handler(capsule_id: str, authorization: str = Header(None), request: Request = None):
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
            content = plaintext.decode("utf-8") if plaintext else "[解密失败：密钥错误]"
        except Exception as e:
            content = f"[解密失败：{e}]"

    h = add_cors_headers(request) if request else {}
    return JSONResponse({
        "id": record["id"],
        "memo": record["memo"],
        "content": content,
        "tags": record["tags"],
        "session_id": record["session_id"],
        "window_title": record["window_title"],
        "url": record.get("url"),
        "created_at": record["created_at"],
        "synced": bool(record["synced"]),
    }, headers=h)

@app.delete("/capsules/{capsule_id}")
def delete_capsule(capsule_id: str, authorization: str = Header(None), request: Request = None):
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
    h = add_cors_headers(request) if request else {}
    return JSONResponse({"status": "ok"}, headers=h)

# ── 服务状态（无需认证）────────────────────────────────
@app.get("/status")
def get_status(request: Request):
    session_key = get_current_session_key()
    master_pw = get_master_password()
    api_token = get_api_token()
    h = add_cors_headers(request)
    return JSONResponse({
        "running": True,
        "version": "0.8.4",
        "session_key": session_key,
        "has_master_password": bool(master_pw),
        "has_api_token": bool(api_token),
        "workspace": str(HOME / ".openclaw" / "workspace"),
        "huper_url": get_huper_url(),
    }, headers=h)

@app.get("/")
def root(request: Request):
    h = add_cors_headers(request)
    return JSONResponse({"service": "amber-hunter", "version": "0.8.4", "docs": "/docs"}, headers=h)

# ── 启动 ───────────────────────────────────────────────
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
