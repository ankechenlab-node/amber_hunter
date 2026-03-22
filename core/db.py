"""
core/db.py — SQLite 数据库操作
"""

import sqlite3, json
from pathlib import Path
from datetime import datetime
from typing import Optional

HOME = Path.home()
DB_PATH = HOME / ".amber-hunter" / "hunter.db"


def init_db():
    """初始化数据库（含加密字段迁移）"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS capsules (
            id              TEXT PRIMARY KEY,
            memo            TEXT,
            content         TEXT,
            tags            TEXT,
            session_id      TEXT,
            window_title    TEXT,
            url             TEXT,
            created_at      REAL NOT NULL,
            synced         INTEGER DEFAULT 0
        )
    """)

    # v0.8.4+: 加密字段
    try:
        c.execute("ALTER TABLE capsules ADD COLUMN salt TEXT")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE capsules ADD COLUMN nonce TEXT")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE capsules ADD COLUMN encrypted_len INTEGER")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE capsules ADD COLUMN content_hash TEXT")
    except Exception:
        pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    conn.close()


def insert_capsule(
    capsule_id: str,
    memo: str,
    content: str,
    tags: str,
    session_id: str | None,
    window_title: str | None,
    url: str | None,
    created_at: float,
    salt: str | None = None,
    nonce: str | None = None,
    encrypted_len: int | None = None,
    content_hash: str | None = None,
) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO capsules
              (id,memo,content,tags,session_id,window_title,url,created_at,salt,nonce,encrypted_len,content_hash,synced)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (capsule_id, memo, content, tags, session_id, window_title,
              url, created_at, salt, nonce, encrypted_len, content_hash, 0))
        conn.commit()
        return True
    finally:
        conn.close()


def get_capsule(capsule_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    try:
        row = c.execute(
            "SELECT id,memo,content,tags,session_id,window_title,url,created_at,salt,nonce,encrypted_len,content_hash,synced "
            "FROM capsules WHERE id=?", (capsule_id,)
        ).fetchone()
        if not row:
            return None
        keys = ["id","memo","content","tags","session_id","window_title","url",
                "created_at","salt","nonce","encrypted_len","content_hash","synced"]
        return dict(zip(keys, row))
    finally:
        conn.close()


def list_capsules(limit: int = 50) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    rows = c.execute(
        "SELECT id,memo,tags,session_id,window_title,created_at,salt,nonce,synced "
        "FROM capsules ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    keys = ["id","memo","tags","session_id","window_title","created_at","salt","nonce","synced"]
    return [dict(zip(keys, r)) for r in rows]


def mark_synced(capsule_id: str):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("UPDATE capsules SET synced=1 WHERE id=?", (capsule_id,))
    conn.commit()
    conn.close()


def get_unsynced_capsules() -> list[dict]:
    """返回所有未同步的胶囊（含加密 content，用于云端上传）"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    rows = c.execute(
        "SELECT id,memo,content,tags,session_id,window_title,url,created_at,salt,nonce,encrypted_len,content_hash,synced "
        "FROM capsules WHERE synced=0"
    ).fetchall()
    conn.close()
    keys = ["id","memo","content","tags","session_id","window_title","url","created_at","salt","nonce","encrypted_len","content_hash","synced"]
    return [dict(zip(keys, r)) for r in rows]


def get_config(key: str) -> str | None:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    row = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else None


def set_config(key: str, value: str):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO config (key,value) VALUES (?,?)", (key, value))
    conn.commit()
    conn.close()
