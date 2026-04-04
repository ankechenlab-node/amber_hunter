"""
core/wal.py — Session State WAL (Write-Ahead Log) v1.2.24 P0-2
偏好/决定/修正 信号检测 + JSONL WAL 文件写入
"""
from __future__ import annotations

import json, os, time
from pathlib import Path
from typing import Optional

HOME = Path.home()
WAL_FILE = HOME / ".amber-hunter" / "session_wal.jsonl"

# ── Signal patterns ────────────────────────────────────────
PREF_SIGNALS = [
    # 中文
    "我比较", "我一般", "我通常", "我不喜欢", "我想要", "我宁愿",
    "我偏向", "我倾向于", "我比较喜欢", "我比较不", "我从来都",
    # 英文
    "i prefer", "i like", "i usually", "i typically", "i tend to",
    "i don't like", "i dislike", "i'd rather", "i'm more",
]
DECISION_SIGNALS = [
    # 中文
    "决定了", "决定用", "就选", "最终选了", "采用", "拍板",
    # 英文
    "decided on", "going with", "will use", "selected", "chose to",
]
CORRECTION_SIGNALS = [
    # 中文
    "之前说的不对", "我改一下", "更正是", "错了", "纠正",
    "不对", "实际上", "准确说是", "更准确地说",
    # 英文
    "actually", "correction", "i meant", "i made a mistake",
]


def _detect_signal_type(text: str) -> Optional[str]:
    """检测文本中包含的信号类型，返回 preference / decision / correction 或 None"""
    t = text.lower()
    for sig in CORRECTION_SIGNALS:
        if sig.lower() in t:
            return "correction"
    for sig in DECISION_SIGNALS:
        if sig.lower() in t:
            return "decision"
    for sig in PREF_SIGNALS:
        if sig.lower() in t:
            return "preference"
    return None


def write_wal_entry(
    session_id: str,
    entry_type: str,
    data: dict,
    wal_path: Path = WAL_FILE,
) -> bool:
    """追加一条 WAL 事件到文件（O_APPEND，线程安全）"""
    try:
        entry = {
            "session_id": session_id,
            "type": entry_type,
            "data": data,
            "ts": time.time(),
            "processed": False,
        }
        with open(wal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        import sys
        print(f"[wal] write_wal_entry failed: {e}", file=sys.stderr)
        return False


def read_wal_entries(
    session_id: str,
    processed: Optional[bool] = None,
    wal_path: Path = WAL_FILE,
) -> list[dict]:
    """读取指定 session 的 WAL 条目"""
    entries = []
    if not wal_path.exists():
        return entries
    try:
        with open(wal_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("session_id") == session_id:
                        if processed is None or entry.get("processed") == processed:
                            entries.append(entry)
                except Exception:
                    continue
    except Exception as e:
        import sys
        print(f"[wal] read_wal_entries failed: {e}", file=sys.stderr)
    return entries


def mark_wal_processed(entry_ts: float, wal_path: Path = WAL_FILE) -> bool:
    """标记指定 ts 的条目为已处理"""
    tmp = wal_path.with_suffix(".tmp")
    try:
        with open(wal_path, encoding="utf-8") as f, open(tmp, "w", encoding="utf-8") as out:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if abs(entry.get("ts", 0) - entry_ts) < 0.001:
                        entry["processed"] = True
                    out.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except Exception:
                    out.write(line)
        os.replace(tmp, wal_path)
        return True
    except Exception as e:
        import sys
        print(f"[wal] mark_wal_processed failed: {e}", file=sys.stderr)
        return False


def get_wal_stats() -> dict:
    """返回 WAL 统计信息"""
    if not WAL_FILE.exists():
        return {"total": 0, "by_type": {}}
    total = 0
    by_type: dict = {}
    try:
        with open(WAL_FILE, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    total += 1
                    try:
                        entry = json.loads(line)
                        t = entry.get("type", "unknown")
                        by_type[t] = by_type.get(t, 0) + 1
                    except Exception:
                        pass
    except Exception:
        pass
    return {"total": total, "by_type": by_type}
