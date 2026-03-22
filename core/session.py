"""core/session.py — 多 AI 客户端 Session 读取与解析

支持来源：
  - OpenClaw:       ~/.openclaw/agents/main/sessions/*.jsonl
  - Claude Cowork:  ~/Library/Application Support/Claude/local-agent-mode-sessions/**/*.jsonl
"""
import json, re, sys
from pathlib import Path
from datetime import datetime

HOME = Path.home()

# ── OpenClaw paths ───────────────────────────────────────
AGENTS_DIR = HOME / ".openclaw" / "agents"
SESSIONS_FILE = AGENTS_DIR / "main" / "sessions" / "sessions.json"
WORKSPACE_DIR = HOME / ".openclaw" / "workspace"

# ── Claude Cowork paths (macOS only) ─────────────────────
if sys.platform == "darwin":
    CLAUDE_SESSIONS_BASE = (
        HOME / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"
    )
else:
    CLAUDE_SESSIONS_BASE = None

# 用于区分来源的前缀
_CLAUDE_PREFIX = "claude::"


def _strip_telegram_meta(text: str) -> str:
    """去除 Telegram 元数据，提取实际用户文本。"""
    try:
        text = re.sub(r'System:\s*\[[^\]]+\]\s*', '', text)
        text = re.sub(
            r'Conversation info[^`]*`{3,}json.*?`{3,}',
            '', text, flags=re.DOTALL
        )
        text = re.sub(
            r'Sender[^`]*`{3,}json.*?`{3,}',
            '', text, flags=re.DOTALL
        )
    except Exception as e:
        print(f"[session] strip_telegram_meta error: {e}")
    return text.strip()


# ── 通用 JSONL 解析（兼容 OpenClaw 和 Claude Cowork 格式）──

def _read_jsonl_messages(file_path: Path, limit: int = 100) -> list[dict]:
    """
    从任意 JSONL 文件读取消息，兼容两种格式：
      OpenClaw:      {"type": "message", "message": {"role": ..., "content": [...]}}
      Claude Cowork: {"type": "user"|"assistant", "message": {"role": ..., "content": ...}}
    """
    messages = []
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    msg_type = obj.get("type", "")

                    # OpenClaw: type == "message"
                    if msg_type == "message":
                        msg = obj.get("message", {})
                        if not isinstance(msg, dict):
                            continue
                        role = msg.get("role", "")
                        content = msg.get("content", [])

                    # Claude Cowork: type == "user" or "assistant"
                    elif msg_type in ("user", "assistant"):
                        role = msg_type
                        msg = obj.get("message", {})
                        if isinstance(msg, dict):
                            content = msg.get("content", [])
                        else:
                            content = obj.get("content", [])
                    else:
                        continue

                    # 提取文本
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
                except Exception:
                    continue
    except Exception as e:
        print(f"[session] _read_jsonl_messages error: {e}")
    return messages[-limit:]


# ── OpenClaw session 定位 ────────────────────────────────

def _get_openclaw_session_key() -> str | None:
    """返回 OpenClaw 最近活跃的 session key。"""
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
        print(f"[session] _get_openclaw_session_key error: {e}")
    return None


def _openclaw_key_to_path(session_key: str) -> Path | None:
    """将 OpenClaw session key 转换为 .jsonl 文件路径。"""
    try:
        sessions = json.loads(SESSIONS_FILE.read_text())
        meta = sessions.get(session_key, {})
        session_id = (
            meta.get("sessionId", "")
            or session_key.replace("agent:main:", "").replace(":", "_")
        )
        path = AGENTS_DIR / "main" / "sessions" / f"{session_id}.jsonl"
        return path if path.exists() else None
    except Exception:
        return None


def _openclaw_session_mtime(session_key: str) -> float:
    """返回 OpenClaw session 的最后修改时间（UNIX 时间戳）。"""
    try:
        sessions = json.loads(SESSIONS_FILE.read_text())
        meta = sessions.get(session_key, {})
        # updatedAt 可能是毫秒或秒
        t = meta.get("updatedAt", 0)
        if t > 1e12:
            t /= 1000  # 毫秒 → 秒
        return float(t)
    except Exception:
        return 0.0


# ── Claude Cowork session 定位 ───────────────────────────

def _find_latest_claude_session() -> Path | None:
    """
    在 Claude Cowork 会话目录中找到最近修改的 .jsonl 文件。
    跳过：audit.jsonl、subagents 目录下的文件。
    """
    if CLAUDE_SESSIONS_BASE is None or not CLAUDE_SESSIONS_BASE.exists():
        return None
    try:
        latest: Path | None = None
        latest_mtime = 0.0
        for jsonl in CLAUDE_SESSIONS_BASE.rglob("*.jsonl"):
            if jsonl.name == "audit.jsonl":
                continue
            if "subagents" in jsonl.parts:
                continue
            try:
                mtime = jsonl.stat().st_mtime
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest = jsonl
            except Exception:
                continue
        return latest
    except Exception as e:
        print(f"[session] _find_latest_claude_session error: {e}")
    return None


# ── 统一接口 ─────────────────────────────────────────────

def get_current_session_key() -> str | None:
    """
    返回最近活跃 session 的 key。
    - OpenClaw session:  原有格式的 key 字符串
    - Claude Cowork:     "claude::<绝对路径>"
    自动选择两者中更新的一个。
    """
    # OpenClaw
    oc_key = _get_openclaw_session_key()
    oc_mtime = _openclaw_session_mtime(oc_key) if oc_key else 0.0

    # Claude Cowork
    cl_path = _find_latest_claude_session()
    cl_mtime = cl_path.stat().st_mtime if cl_path else 0.0

    if cl_mtime > oc_mtime and cl_path is not None:
        return f"{_CLAUDE_PREFIX}{cl_path}"
    return oc_key  # 可能为 None


def read_session_messages(session_key: str, limit: int = 100) -> list[dict]:
    """读取 session 消息，自动按来源路由。"""
    if not session_key:
        return []

    if session_key.startswith(_CLAUDE_PREFIX):
        path = Path(session_key[len(_CLAUDE_PREFIX):])
        return _read_jsonl_messages(path, limit)

    # OpenClaw
    try:
        path = _openclaw_key_to_path(session_key)
        if path is None:
            return []
        return _read_jsonl_messages(path, limit)
    except Exception as e:
        print(f"[session] read_session_messages error: {e}")
        return []


def build_session_summary(session_key: str) -> dict:
    """构建 session 摘要（对 amber_hunter.py 保持同一接口）。"""
    messages = read_session_messages(session_key, limit=100)
    if not messages:
        return {"session_key": session_key, "summary": "", "messages": []}

    user_msgs = [m["text"] for m in messages if m["role"] == "user" and len(m["text"]) > 5]
    last_topic = next((m for m in reversed(user_msgs) if len(m) > 10), "")

    # 给 session_key 加来源标注用于显示
    source = "Claude Cowork" if session_key.startswith(_CLAUDE_PREFIX) else "OpenClaw"

    return {
        "session_key": session_key,
        "source": source,
        "summary": f"最近对话：{last_topic[:200]}" if last_topic else "当前 session 无用户对话内容",
        "last_user_message": last_topic[:300] if last_topic else None,
        "message_count": len(messages),
        "recent_messages": messages[-6:],
    }


# ── 工作区文件（不变）────────────────────────────────────

def get_recent_files(limit: int = 10) -> list[dict]:
    """返回 workspace 最近修改的文件。"""
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
