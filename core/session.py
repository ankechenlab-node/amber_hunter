"""
core/session.py — OpenClaw Session 读取与解析
"""

import json, re
from pathlib import Path
from datetime import datetime

HOME = Path.home()
AGENTS_DIR = HOME / ".openclaw" / "agents"
SESSIONS_FILE = AGENTS_DIR / "main" / "sessions" / "sessions.json"
WORKSPACE_DIR = HOME / ".openclaw" / "workspace"


def get_current_session_key() -> str | None:
    """找到最近一次活跃的 session key（跳过 cron/sub-agent）"""
    try:
        if not SESSIONS_FILE.exists():
            return None
        sessions = json.loads(SESSIONS_FILE.read_text())
        if not sessions:
            return None
        for key, meta in sorted(
            sessions.items(),
            key=lambda x: x[1].get("updatedAt", 0),
            reverse=True
        ):
            if "cron:" in key or "sub-agent" in key:
                continue
            return key
    except Exception as e:
        print(f"[session] get_current_session_key error: {e}")
    return None


def _strip_telegram_meta(text: str) -> str:
    """
    去除 Telegram 元数据，提取实际用户文本。
    加了 try/except，任何正则失败都不崩溃。
    """
    try:
        # 去除 System: [timestamp]
        text = re.sub(r'System:\s*\[[^\]]+\]\s*', '', text)
        # 去除 Conversation info ... ```json ... ```
        text = re.sub(
            r'Conversation info[^`]*`{3,}json.*?`{3,}',
            '', text, flags=re.DOTALL
        )
        # 去除 Sender ... ```json ... ```
        text = re.sub(
            r'Sender[^`]*`{3,}json.*?`{3,}',
            '', text, flags=re.DOTALL
        )
    except Exception as e:
        print(f"[session] strip_telegram_meta error: {e}")
    return text.strip()


def read_session_messages(session_key: str, limit: int = 100) -> list[dict]:
    """读取 session JSONL，返回消息列表（加 try/except 保护）"""
    try:
        if not SESSIONS_FILE.exists():
            return []
        sessions = json.loads(SESSIONS_FILE.read_text())
        meta = sessions.get(session_key, {})
        session_id = meta.get("sessionId", "") or session_key.replace("agent:main:", "").replace(":", "_")
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
                        if isinstance(content, list):
                            for item in content:
                                if not isinstance(item, dict):
                                    continue
                                if item.get("type") == "text":
                                    raw = item.get("text", "")
                                    cleaned = _strip_telegram_meta(raw)
                                    if cleaned:
                                        text_parts.append(cleaned)
                        elif isinstance(content, str):
                            text_parts.append(_strip_telegram_meta(content))
                        if text_parts:
                            messages.append({
                                "role": role,
                                "text": " ".join(text_parts)[:500],
                                "timestamp": obj.get("timestamp", ""),
                            })
                except Exception as e:
                    # 单条消息解析失败不影响其他
                    continue
        return messages[-limit:]
    except Exception as e:
        print(f"[session] read_session_messages error: {e}")
        return []


def build_session_summary(session_key: str) -> dict:
    """构建 session 摘要"""
    messages = read_session_messages(session_key, limit=100)
    if not messages:
        return {"session_key": session_key, "summary": "", "messages": []}
    user_msgs = [m["text"] for m in messages if m["role"] == "user" and len(m["text"]) > 5]
    last_topic = next((m for m in reversed(user_msgs) if len(m) > 10), "")
    return {
        "session_key": session_key,
        "summary": f"最近对话：{last_topic[:200]}" if last_topic else "当前 session 无用户对话内容",
        "last_user_message": last_topic[:300] if last_topic else None,
        "message_count": len(messages),
        "recent_messages": messages[-6:],
    }


def get_recent_files(limit: int = 10) -> list[dict]:
    """返回 workspace 最近修改的文件（加 try/except 保护）"""
    try:
        files = []
        if not WORKSPACE_DIR.exists():
            return []
        all_files = [
            f for f in WORKSPACE_DIR.rglob("*")
            if f.is_file() and not f.name.startswith(".")
        ]
        for f in sorted(all_files, key=lambda x: -x.stat().st_mtime)[:limit]:
            try:
                files.append({
                    "path": str(f.relative_to(HOME)),
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "size_kb": round(f.stat().st_size / 1024, 1),
                })
            except Exception:
                continue
        return files
    except Exception as e:
        print(f"[session] get_recent_files error: {e}")
        return []
