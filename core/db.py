"""
core/db.py — SQLite 数据库操作
v1.1.9: 新增 source_type/category 列 + memory_queue 表
"""

import sqlite3, json, secrets, time
from pathlib import Path
from typing import Optional

HOME = Path.home()
DB_PATH = HOME / ".amber-hunter" / "hunter.db"


def init_db():
    """初始化数据库（含加密字段 + v1.1.9 新字段迁移）"""
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
            synced          INTEGER DEFAULT 0
        )
    """)

    # v0.8.4+: 加密字段
    for col in ["salt TEXT", "nonce TEXT", "encrypted_len INTEGER", "content_hash TEXT"]:
        try:
            c.execute(f"ALTER TABLE capsules ADD COLUMN {col}")
        except Exception:
            pass

    # v1.1.9: 来源与分类字段
    for col in ["source_type TEXT DEFAULT 'manual'", "category TEXT DEFAULT ''"]:
        try:
            c.execute(f"ALTER TABLE capsules ADD COLUMN {col}")
        except Exception:
            pass

    # v1.1.9: AI 提议记忆审核队列
    c.execute("""
        CREATE TABLE IF NOT EXISTS memory_queue (
            id          TEXT PRIMARY KEY,
            memo        TEXT NOT NULL,
            context     TEXT,
            category    TEXT DEFAULT '',
            tags        TEXT DEFAULT '',
            source      TEXT DEFAULT '',
            confidence  REAL DEFAULT 0.5,
            created_at  REAL NOT NULL,
            status      TEXT DEFAULT 'pending'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # v1.2.8+: hit tracking columns on capsules
    for col in [
        "last_accessed REAL DEFAULT 0",
        "hotness_score REAL DEFAULT 0.0",
        "hit_count INTEGER DEFAULT 0",
    ]:
        try:
            c.execute(f"ALTER TABLE capsules ADD COLUMN {col}")
        except Exception:
            pass

    # v1.2.8+: memory_hits — record each recall usage
    c.execute("""
        CREATE TABLE IF NOT EXISTS memory_hits (
            id               TEXT PRIMARY KEY,
            capsule_id       TEXT NOT NULL,
            session_id       TEXT,
            hit_at           REAL DEFAULT (strftime('%s', 'now')),
            search_query     TEXT,
            relevance_score  REAL
        )
    """)

    # v1.2.10+: 常用查询索引
    for index_sql in [
        "CREATE INDEX IF NOT EXISTS idx_capsules_synced ON capsules(synced)",
        "CREATE INDEX IF NOT EXISTS idx_capsules_created_at ON capsules(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_capsules_session_id ON capsules(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_capsules_category ON capsules(category)",
        "CREATE INDEX IF NOT EXISTS idx_memory_queue_status ON memory_queue(status)",
        "CREATE INDEX IF NOT EXISTS idx_memory_queue_created_at ON memory_queue(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_memory_hits_capsule_id ON memory_hits(capsule_id)",
    ]:
        try:
            c.execute(index_sql)
        except Exception:
            pass

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
    source_type: str = "manual",
    category: str = "",
) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO capsules
              (id,memo,content,tags,session_id,window_title,url,created_at,
               salt,nonce,encrypted_len,content_hash,synced,source_type,category)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (capsule_id, memo, content, tags, session_id, window_title,
              url, created_at, salt, nonce, encrypted_len, content_hash,
              0, source_type, category))
        conn.commit()
        return True
    finally:
        conn.close()


def get_capsule(capsule_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    try:
        row = c.execute(
            "SELECT id,memo,content,tags,session_id,window_title,url,created_at,"
            "salt,nonce,encrypted_len,content_hash,synced,source_type,category "
            "FROM capsules WHERE id=?", (capsule_id,)
        ).fetchone()
        if not row:
            return None
        keys = ["id","memo","content","tags","session_id","window_title","url",
                "created_at","salt","nonce","encrypted_len","content_hash","synced",
                "source_type","category"]
        return dict(zip(keys, row))
    finally:
        conn.close()


def list_capsules(limit: int = 50) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    rows = c.execute(
        "SELECT id,memo,content,tags,session_id,window_title,created_at,"
        "salt,nonce,synced,source_type,category "
        "FROM capsules ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    keys = ["id","memo","content","tags","session_id","window_title","created_at",
            "salt","nonce","synced","source_type","category"]
    return [dict(zip(keys, r)) for r in rows]


def mark_synced(capsule_id: str):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("UPDATE capsules SET synced=1 WHERE id=?", (capsule_id,))
    conn.commit()
    conn.close()


def get_unsynced_capsules() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    rows = c.execute(
        "SELECT id,memo,content,tags,session_id,window_title,url,created_at,"
        "salt,nonce,encrypted_len,content_hash,synced,source_type,category "
        "FROM capsules WHERE synced=0"
    ).fetchall()
    conn.close()
    keys = ["id","memo","content","tags","session_id","window_title","url",
            "created_at","salt","nonce","encrypted_len","content_hash","synced",
            "source_type","category"]
    return [dict(zip(keys, r)) for r in rows]


# ── memory_queue CRUD ─────────────────────────────────────

def queue_insert(memo: str, context: str, category: str, tags: str,
                 source: str, confidence: float) -> str:
    """插入待审核记忆，返回新 id"""
    qid = secrets.token_hex(8)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("""
        INSERT INTO memory_queue (id,memo,context,category,tags,source,confidence,created_at,status)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (qid, memo, context, category, tags, source, confidence, time.time(), "pending"))
    conn.commit()
    conn.close()
    return qid


def queue_list_pending() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    rows = c.execute(
        "SELECT id,memo,context,category,tags,source,confidence,created_at,status "
        "FROM memory_queue WHERE status='pending' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    keys = ["id","memo","context","category","tags","source","confidence","created_at","status"]
    return [dict(zip(keys, r)) for r in rows]


def queue_get(qid: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    row = c.execute(
        "SELECT id,memo,context,category,tags,source,confidence,created_at,status "
        "FROM memory_queue WHERE id=?", (qid,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    keys = ["id","memo","context","category","tags","source","confidence","created_at","status"]
    return dict(zip(keys, row))


def queue_set_status(qid: str, status: str):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("UPDATE memory_queue SET status=? WHERE id=?", (status, qid))
    conn.commit()
    conn.close()


def queue_update(qid: str, memo: str, category: str, tags: str):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("UPDATE memory_queue SET memo=?,category=?,tags=?,status='edited' WHERE id=?",
              (memo, category, tags, qid))
    conn.commit()
    conn.close()


# ── config ────────────────────────────────────────────────

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


def save_tag_feedback(original_tag: str, corrected_tag: str) -> None:
    """记录用户修正过的标签对，用于引导后续标签生成"""
    import json as _json
    key = f"tag_feedback:{original_tag.lower()}"
    existing = get_config(key)
    corrections = []
    if existing:
        try:
            corrections = _json.loads(existing)
        except Exception:
            corrections = []
    if corrected_tag.lower() not in corrections:
        corrections.append(corrected_tag.lower())
    set_config(key, _json.dumps(corrections))


def get_tag_feedback(original_tag: str) -> list:
    """获取某标签的用户修正历史"""
    import json as _json
    key = f"tag_feedback:{original_tag.lower()}"
    val = get_config(key)
    if not val:
        return []
    try:
        return _json.loads(val)
    except Exception:
        return []


# ── memory_hits — hit tracking v1.2.8 ──────────────────────────────

def insert_memory_hit(
    hit_id: str,
    capsule_id: str,
    session_id: str | None,
    search_query: str | None,
    relevance_score: float | None,
) -> bool:
    """Insert a record of a recall hit."""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    try:
        c.execute("""
            INSERT OR IGNORE INTO memory_hits
              (id, capsule_id, session_id, hit_at, search_query, relevance_score)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (hit_id, capsule_id, session_id or None, time.time(),
              search_query or None, relevance_score or None))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def update_capsule_hit(capsule_id: str, relevance_score: float) -> None:
    """
    Update hotness_score and last_accessed for a capsule after a recall hit.
    Formula: hotness += relevance_score * 0.1  (diminishing bonus per hit)
    """
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    try:
        now = time.time()
        c.execute("""
            UPDATE capsules
            SET last_accessed = ?,
                hit_count = hit_count + 1,
                hotness_score = MIN(hotness_score + ?, 10.0)
            WHERE id = ?
        """, (now, relevance_score * 0.1, capsule_id))
        conn.commit()
    finally:
        conn.close()
