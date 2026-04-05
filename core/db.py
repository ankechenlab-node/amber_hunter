"""
core/db.py — SQLite 数据库操作
v1.1.9: 新增 source_type/category 列 + memory_queue 表
v1.2.11: thread-local 连接缓存，减少连接开销
v1.2.18: insights 表（压缩记忆摘要缓存）
"""
from __future__ import annotations

import sqlite3, json, secrets, time
import threading
from pathlib import Path
from typing import Optional

HOME = Path.home()
DB_PATH = HOME / ".amber-hunter" / "hunter.db"

# ── Thread-local 连接缓存 ─────────────────────────────────
# 每个线程缓存一个连接，避免频繁开闭
_thread_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """获取当前线程的缓存连接，没有则创建新的"""
    if not hasattr(_thread_local, "conn") or _thread_local.conn is None:
        _thread_local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _thread_local.conn.row_factory = sqlite3.Row
    return _thread_local.conn


def _close_conn():
    """关闭并清理当前线程的缓存连接"""
    conn = getattr(_thread_local, "conn", None)
    if conn is not None:
        conn.close()
        _thread_local.conn = None


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

    # D2: 密钥来源字段（'did' 或 'pbkdf2'）
    try:
        c.execute("ALTER TABLE capsules ADD COLUMN key_source TEXT DEFAULT 'pbkdf2'")
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

    # v1.2.11+: category_path for MFS hierarchical indexing
    for col in ["category_path TEXT DEFAULT 'general/default'"]:
        try:
            c.execute(f"ALTER TABLE capsules ADD COLUMN {col}")
        except Exception:
            pass

    # v1.2.15+: updated_at for conflict detection
    try:
        c.execute("ALTER TABLE capsules ADD COLUMN updated_at REAL DEFAULT 0")
    except Exception:
        pass

    # v1.2.23+: vector_id — LanceDB 向量库外键
    try:
        c.execute("ALTER TABLE capsules ADD COLUMN vector_id TEXT")
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

    # v1.2.17+: insights — compressed memory summaries by category_path
    c.execute("""
        CREATE TABLE IF NOT EXISTS insights (
            id               TEXT PRIMARY KEY,
            capsule_ids      TEXT,          -- JSON array of source capsule IDs
            summary          TEXT,           -- LLM-compressed summary
            path             TEXT,          -- category_path
            hotness_score   REAL DEFAULT 0,
            created_at      REAL DEFAULT (strftime('%s', 'now')),
            updated_at      REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_insights_path ON insights(path)")
    except Exception:
        pass

    # v1.2.27: user_profile 表（P1-1 Structured User Profile）
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            id           TEXT PRIMARY KEY,
            section      TEXT NOT NULL UNIQUE,
            content      TEXT NOT NULL,
            source       TEXT DEFAULT 'manual',
            session_id   TEXT,
            hotness      REAL DEFAULT 0.0,
            created_at   REAL DEFAULT (strftime('%s', 'now')),
            updated_at   REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_profile_section ON user_profile(section)")
    except Exception:
        pass

    # v1.2.29 G1: correction_log — 自我校正闭环事件日志
    c.execute("""
        CREATE TABLE IF NOT EXISTS correction_log (
            id              TEXT PRIMARY KEY,
            field          TEXT NOT NULL,      -- 'tag' | 'category' | 'reject'
            original_value  TEXT NOT NULL,
            corrected_value TEXT NOT NULL,
            source          TEXT DEFAULT 'queue_edit',  -- 'queue_edit' | 'recall_feedback'
            session_id     TEXT,
            queue_id       TEXT,
            confidence     REAL DEFAULT 1.0,
            created_at     REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_correction_field ON correction_log(field)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_correction_original ON correction_log(original_value)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_correction_created ON correction_log(created_at DESC)")
    except Exception:
        pass

    # v1.2.10+: 常用查询索引
    for index_sql in [
        "CREATE INDEX IF NOT EXISTS idx_capsules_synced ON capsules(synced)",
        "CREATE INDEX IF NOT EXISTS idx_capsules_created_at ON capsules(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_capsules_session_id ON capsules(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_capsules_category ON capsules(category)",
        "CREATE INDEX IF NOT EXISTS idx_capsules_category_path ON capsules(category_path)",
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
    category_path: str = "general/default",
    updated_at: float = 0.0,
    key_source: str = "pbkdf2",
    vector_id: str | None = None,
) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO capsules
              (id,memo,content,tags,session_id,window_title,url,created_at,
               salt,nonce,encrypted_len,content_hash,synced,source_type,category,category_path,updated_at,key_source,vector_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (capsule_id, memo, content, tags, session_id, window_title,
              url, created_at, salt, nonce, encrypted_len, content_hash,
              0, source_type, category, category_path, updated_at or created_at, key_source, vector_id))
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
            "salt,nonce,encrypted_len,content_hash,synced,source_type,category,category_path,updated_at,key_source,vector_id "
            "FROM capsules WHERE id=?", (capsule_id,)
        ).fetchone()
        if not row:
            return None
        keys = ["id","memo","content","tags","session_id","window_title","url",
                "created_at","salt","nonce","encrypted_len","content_hash","synced",
                "source_type","category","category_path","updated_at","key_source","vector_id"]
        return dict(zip(keys, row))
    finally:
        conn.close()


def count_capsules() -> int:
    """返回胶囊总数"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    row = c.execute("SELECT COUNT(*) FROM capsules").fetchone()
    conn.close()
    return row[0] if row else 0


def list_capsules(limit: int = 50, category_path: str = "") -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    if category_path:
        rows = c.execute(
            "SELECT id,memo,content,tags,session_id,window_title,created_at,"
            "salt,nonce,synced,source_type,category,category_path,updated_at,vector_id "
            "FROM capsules WHERE category_path LIKE ? || '%' ORDER BY created_at DESC LIMIT ?",
            (category_path, limit)
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT id,memo,content,tags,session_id,window_title,created_at,"
            "salt,nonce,synced,source_type,category,category_path,updated_at,vector_id "
            "FROM capsules ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    keys = ["id","memo","content","tags","session_id","window_title","created_at",
            "salt","nonce","synced","source_type","category","category_path","updated_at","vector_id"]
    return [dict(zip(keys, r)) for r in rows]


def mark_synced(capsule_id: str):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("UPDATE capsules SET synced=1 WHERE id=?", (capsule_id,))
    conn.commit()
    conn.close()


def get_unsynced_capsules(limit: int = 0, since: float = 0.0) -> list[dict]:
    """
    获取未同步或自 since 以来修改过的胶囊（支持增量 sync）。
    - 未同步胶囊：synced=0
    - 增量同步：updated_at > since（即使已标记 synced）
    """
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    keys = ["id","memo","content","tags","session_id","window_title","url",
            "created_at","salt","nonce","encrypted_len","content_hash","synced",
            "source_type","category","category_path","updated_at","key_source","vector_id"]
    if since > 0:
        sql = (f"SELECT {','.join(keys)} FROM capsules "
               f"WHERE synced=0 OR updated_at > ? ORDER BY updated_at ASC")
        rows = c.execute(sql, (since,)).fetchall()
    else:
        sql = (f"SELECT {','.join(keys)} FROM capsules "
               f"WHERE synced=0 ORDER BY created_at ASC")
        if limit > 0:
            sql += f" LIMIT {limit}"
        rows = c.execute(sql).fetchall()
    conn.close()
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
    except Exception as e:
        import sys
        print(f"[db] insert_memory_hit failed: {e}", file=sys.stderr)
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


# ── User Profile CRUD (P1-1) ────────────────────────────────────────────────

def insert_profile(
    section: str,
    content: str,
    source: str = "manual",
    session_id: str | None = None,
) -> bool:
    """插入或替换 profile section"""
    import secrets
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    try:
        pid = secrets.token_hex(8)
        now = time.time()
        c.execute("""
            INSERT OR REPLACE INTO user_profile
              (id, section, content, source, session_id, hotness, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 0.0, ?, ?)
        """, (pid, section, content, source, session_id, now, now))
        conn.commit()
        return True
    finally:
        conn.close()


def update_profile(
    section: str,
    content: str,
    source: str = "manual",
    session_id: str | None = None,
) -> bool:
    """更新已有 profile section"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    try:
        now = time.time()
        c.execute("""
            UPDATE user_profile
            SET content = ?, source = ?, session_id = ?, updated_at = ?
            WHERE section = ?
        """, (content, source, session_id, now, section))
        conn.commit()
        return True
    finally:
        conn.close()


def get_profile(section: str) -> dict | None:
    """读取单个 profile section"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    try:
        row = c.execute(
            "SELECT id, section, content, source, session_id, hotness, created_at, updated_at "
            "FROM user_profile WHERE section = ?",
            (section,)
        ).fetchone()
        if not row:
            return None
        keys = ["id", "section", "content", "source", "session_id", "hotness", "created_at", "updated_at"]
        return dict(zip(keys, row))
    finally:
        conn.close()


def list_profile() -> dict:
    """读取所有 profile sections，返回 {section: content}"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    rows = c.execute(
        "SELECT section, content, source, hotness, updated_at FROM user_profile ORDER BY section"
    ).fetchall()
    conn.close()
    return {r[0]: {"content": r[1], "source": r[2], "hotness": r[3], "updated_at": r[4]} for r in rows}


# ── G1: Correction Self-Correction Loop ──────────────────────────────────────

def record_correction(
    field: str,
    original_value: str,
    corrected_value: str,
    source: str = "queue_edit",
    session_id: str | None = None,
    queue_id: str | None = None,
    confidence: float = 1.0,
) -> str:
    """记录一次校正事件，返回 log id"""
    import secrets
    log_id = secrets.token_hex(8)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO correction_log
              (id, field, original_value, corrected_value, source, session_id, queue_id, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (log_id, field, original_value, corrected_value, source, session_id, queue_id, confidence, time.time()))
        conn.commit()
    finally:
        conn.close()
    return log_id


def get_correction_stats(field: str = "") -> dict:
    """
    统计校正数据。
    返回 {corrections: [{original, corrected, count, last_corrected}], total: N}
    如果指定 field 则只统计该类型。
    """
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    sql = """
        SELECT original_value, corrected_value, COUNT(*) as cnt, MAX(created_at) as last_at
        FROM correction_log
        WHERE field = ?
        GROUP BY original_value, corrected_value
        ORDER BY cnt DESC, last_at DESC
    """
    rows = c.execute(sql, (field,)).fetchall()
    conn.close()
    corrections = [
        {"original": r[0], "corrected": r[1], "count": r[2], "last_corrected": r[3]}
        for r in rows
    ]
    total = sum(r[2] for r in rows)
    return {"corrections": corrections, "total": total, "field": field}


def get_correction_suggestions(threshold: int = 3) -> list[dict]:
    """
    生成校正建议：original_value 被纠正 >= threshold 次 → 建议自动替换。
    返回 [{original, suggested, count, confidence}, ...]
    """
    suggestions = []
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    # 找出被多次纠正为同一值的 original
    rows = c.execute("""
        SELECT original_value, corrected_value, COUNT(*) as cnt
        FROM correction_log
        WHERE field = 'tag'
        GROUP BY original_value, corrected_value
        HAVING cnt >= ?
        ORDER BY cnt DESC
    """, (threshold,)).fetchall()
    conn.close()
    for r in rows:
        suggestions.append({
            "original": r[0],
            "suggested": r[1],
            "count": r[2],
            "confidence": min(1.0, r[2] / (r[2] + 5)),  # 次数越多置信度越高
            "field": "tag",
        })
    return suggestions


def apply_correction_suggestion(original: str, corrected: str, field: str = "tag") -> bool:
    """
    采纳建议：把 original → corrected 的映射记录到 config。
    下次 auto-tag 时遇到 original 会自动替换为 corrected。
    """
    key = f"tag_correction:{original.lower()}"
    import json
    existing = get_config(key)
    mapping = {}
    if existing:
        try:
            mapping = json.loads(existing)
        except Exception:
            mapping = {}
    mapping[corrected.lower()] = mapping.get(corrected.lower(), 0) + 1
    set_config(key, json.dumps(mapping))
    return True


def get_tag_corrections() -> dict[str, str]:
    """
    返回所有已采纳的 tag 校正映射：{original_tag: most_likely_corrected_tag}
    用于 _auto_tag_local 时替换。
    """
    import json
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    rows = c.execute("SELECT key, value FROM config WHERE key LIKE 'tag_correction:%'").fetchall()
    conn.close()
    result = {}
    for key, value in rows:
        original = key.replace("tag_correction:", "")
        try:
            mapping = json.loads(value)
            if mapping:
                suggested = max(mapping, key=mapping.get)
                result[original] = suggested
        except Exception:
            pass
    return result

