#!/usr/bin/env python3
"""
Amber-Hunter v1.2.20
Huper琥珀本地感知引擎

兼容 huper v1.0.0（DID 身份层）
"""
from __future__ import annotations

import os, sys, json, time, secrets, sqlite3, hashlib, base64, gc, threading, logging, traceback
from pathlib import Path
from typing import Optional

# ── 核心模块 ────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from core.crypto import derive_key, encrypt_content, decrypt_content, generate_salt
from core.keychain import (
    get_master_password, set_master_password,
    get_api_token, get_huper_url,
    ensure_config_dir, CONFIG_PATH,
    get_os, is_headless,
)
from core.db import (init_db, insert_capsule, get_capsule, list_capsules, count_capsules, mark_synced,
    get_unsynced_capsules, get_config, set_config,
    queue_insert, queue_list_pending, queue_get, queue_set_status, queue_update,
    insert_memory_hit, update_capsule_hit,
    save_tag_feedback, get_tag_feedback,
    _get_conn)
from core.session import get_current_session_key, build_session_summary, get_recent_files
from core.models import CapsuleIn, CapsuleUpdate
from core.llm import get_llm, LLM_AVAILABLE as LLM_READY, load_llm_config, save_llm_config, LLMConfig

# ── FastAPI ─────────────────────────────────────────────
import uvicorn
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware as StarletteCORSMiddleware
from starlette.responses import Response

# ── 语义模型缓存（模块级，只加载一次）────────────────────
_EMBED_MODEL = None
_SEMANTIC_AVAILABLE = None  # 缓存语义搜索可用性检查结果
_MODEL_LOADING = False      # 是否正在加载中
_MODEL_LOAD_ERROR = None    # 加载失败原因
_MODEL_LOAD_LOCK = threading.Lock()  # 防止并发重复加载


# ── 通用辅助函数 ─────────────────────────────────────────
def _extract_bearer_token(request, authorization: str = None) -> str:
    """
    从请求中提取 Bearer token。
    优先级：query_param.token > Authorization header
    返回格式化的 'Bearer xxx' 字符串。
    """
    raw_token = request.query_params.get("token") if request else None
    if not raw_token:
        raw_token = authorization
    else:
        raw_token = f"Bearer {raw_token}"
    return raw_token


# ── MFS 路径推断 v1.2.11 ─────────────────────────────────
# 分级路径索引：按 category_path 组织胶囊，支持路径前缀搜索

_MFS_PATH_KEYWORDS = {
    "projects/amber-hunter": ["amber-hunter", "skill", "openclaw", "mcp", "amber hunter"],
    "projects/huper": ["huper", "琥珀", "huper.org", "网站", "部署", "nginx"],
    "projects/wake-fog": ["wake-fog", "品牌", "品牌项目"],
    "knowledge/python": ["python", "pip", "venv", "import", "pip install", "conda"],
    "knowledge/devops": ["ssh", "linux", "docker", "systemctl", "nginx", "vps", "部署"],
    "knowledge/llm": ["gpt", "claude", "gemini", "模型", "token", "prompt", "llm", "openai"],
    "knowledge/macos": ["macos", "homebrew", "brew", "osx"],
    "reflections/daily": ["今天", "复盘", "总结", "日报", "每日", "daily"],
    "reflections/weekly": ["本周", "周总结", "weekly", "周报"],
    "context/vps-sessions": ["ssh", "root@", "vps", "sshpass", "服务器"],
    "context/error-debugs": ["错误", "bug", "error", "调试", "修复", "exception", "failed"],
    "people/leo": ["leo", "chen", "安克"],
    "creative/writing": ["写作", "文章", "blog", "post"],
}

def _infer_category_path(memo: str, content: str = "", tags: str = "") -> str:
    """
    根据胶囊内容自动推断 category_path。
    返回最匹配的路径，默认为 'general/default'。
    """
    full_text = f"{memo} {content} {tags}".lower()

    best_match = "general/default"
    best_score = 0

    for path, keywords in _MFS_PATH_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in full_text)
        if score > best_score:
            best_score = score
            best_match = path

    return best_match


def backfill_category_paths(dry_run: bool = True) -> dict:
    """
    一次性脚本：遍历所有胶囊，填充 category_path。
    返回归类统计。
    dry_run=True 时只报告不实际写入。
    """
    from core.db import _get_conn

    conn = _get_conn()
    c = conn.cursor()

    # 只更新 general/default 的胶囊（有明确分类的不动）
    rows = c.execute(
        "SELECT id, memo, content, tags, category_path FROM capsules WHERE category_path = 'general/default'"
    ).fetchall()

    stats = {"total": len(rows), "updated": 0, "by_path": {}}
    for row in rows:
        cap_id, memo, content, tags, old_path = row
        new_path = _infer_category_path(memo or "", content or "", tags or "")

        if new_path != "general/default":
            if not dry_run:
                c.execute(
                    "UPDATE capsules SET category_path = ? WHERE id = ?",
                    (new_path, cap_id)
                )
            stats["updated"] += 1
            stats["by_path"][new_path] = stats["by_path"].get(new_path, 0) + 1

    if not dry_run:
        conn.commit()

    return stats


# ── 本地轻量标签生成（无需网络/ML，关键词匹配）────────────────────────
# ── v0.8.9: 可扩展 Topic 分类系统 ─────────────────────────

# 默认 16 个 topic（用户可在 config.json 里自定义扩展）
DEFAULT_TOPICS = [
    {
        "name": "工作",
        "emoji": "💼",
        "keywords": ["项目", "客户", "周报", "deadline", "需求", "任务", "汇报", "职场", "上班", "老板"],
    },
    {
        "name": "技术",
        "emoji": "⚙️",
        "keywords": ["代码", "bug", "api", "部署", "服务器", "python", "数据库", "架构", "接口", "调试"],
    },
    {
        "name": "学习",
        "emoji": "📚",
        "keywords": ["课程", "教程", "学习", "读书", "研究", "论文", "理解", "概念", "知识点"],
    },
    {
        "name": "创意",
        "emoji": "💡",
        "keywords": ["灵感", "创意", "idea", "想法", "创新", "方案", "思路", "设计", "构思"],
    },
    {
        "name": "偏好",
        "emoji": "❤️",
        "keywords": ["我喜欢", "我一般", "我比较", "i prefer", "i like", "i usually",
                     "我不喜欢", "我偏向", "我的习惯", "我宁愿"],
    },
    {
        "name": "健康",
        "emoji": "🏃",
        "keywords": ["健康", "运动", "锻炼", "睡眠", "减肥", "身体", "医生", "体检", "饮食"],
    },
    {
        "name": "财务",
        "emoji": "💰",
        "keywords": ["钱", "投资", "理财", "收入", "支出", "预算", "存款", "股票", "工资", "报销"],
    },
    {
        "name": "生活",
        "emoji": "🌿",
        "keywords": ["做饭", "吃饭", "天气", "周末", "购物", "家务", "日用品", "生活琐事"],
    },
    {
        "name": "人际",
        "emoji": "🤝",
        "keywords": ["朋友", "同事", "合作", "沟通", "社交", "关系", "聚会", "人情"],
    },
    {
        "name": "家庭",
        "emoji": "🏠",
        "keywords": ["家", "父母", "孩子", "宝宝", "伴侣", "亲人", "结婚", "装修", "育儿"],
    },
    {
        "name": "旅行",
        "emoji": "✈️",
        "keywords": ["旅行", "旅游", "出行", "机票", "酒店", "行程", "签证", "景点", "度假"],
    },
    {
        "name": "娱乐",
        "emoji": "🎬",
        "keywords": ["电影", "音乐", "游戏", "剧", "综艺", "小说", "追剧", "演唱会"],
    },
    {
        "name": "灵感",
        "emoji": "✨",
        "keywords": ["突然想到", "灵感", "一闪", "冒出来", "game changer", "有意思",
                     "没想到", "原来如此", "竟然", " breakthrough"],
    },
    {
        "name": "决策",
        "emoji": "🎯",
        "keywords": ["决定", "确定", "选择", "方案定了", "decided", "going with",
                     "最终方案", "采用", "放弃", "取舍"],
    },
    {
        "name": "情绪",
        "emoji": "🌧️",
        "keywords": ["开心", "高兴", "沮丧", "焦虑", "兴奋", "压力大", "累", "疲惫",
                     "期待", "失望", "感动"],
    },
    {
        "name": "项目",
        "emoji": "📦",
        "keywords": ["项目", "里程碑", "迭代", "上线", "发布", "验收", "需求评审", "PRD"],
    },
]

# 敏感类 topic（必须有明确信号词才打，不能只用关键词命中）
EXPLICIT_ONLY_TOPICS = {"偏好", "情绪", "决策"}


def _get_topics_from_config() -> list[dict]:
    """从 config.json 读取用户自定义 topics，缺失时返回默认 topics."""
    try:
        cfg_path = HOME / ".amber-hunter" / "config.json"
        if cfg_path.exists():
            import json as _json
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = _json.load(f)
            custom = cfg.get("topics", [])
            if custom and isinstance(custom, list) and len(custom) > 0:
                return custom
    except Exception:
        pass
    return DEFAULT_TOPICS


def _get_embed_model(blocking: bool = True):
    """
    懒加载向量模型（all-MiniLM-L6-v2），线程安全。

    Args:
        blocking: True=同步等待加载完成；False=若正在加载则立即返回 None（不阻塞）
    Returns:
        模型实例或 None
    """
    global _EMBED_MODEL, _SEMANTIC_AVAILABLE, _MODEL_LOADING, _MODEL_LOAD_ERROR

    if _EMBED_MODEL is not None:
        return _EMBED_MODEL

    with _MODEL_LOAD_LOCK:
        # 双重检查（获取锁后）
        if _EMBED_MODEL is not None:
            return _EMBED_MODEL

        if _MODEL_LOADING:
            if not blocking:
                return None
            # 等待中：释放锁后由外层再次尝试...

        _MODEL_LOADING = True
        _MODEL_LOAD_ERROR = None
        try:
            from sentence_transformers import SentenceTransformer
            _EMBED_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
            _SEMANTIC_AVAILABLE = True
            return _EMBED_MODEL
        except Exception as e:
            _MODEL_LOAD_ERROR = str(e)
            _SEMANTIC_AVAILABLE = False
            _EMBED_MODEL = None
            return None
        finally:
            _MODEL_LOADING = False


def _preload_embed_model():
    """后台线程预加载语义模型，不阻塞主线程。"""
    def _background_load():
        _get_embed_model(blocking=True)
    t = threading.Thread(target=_background_load, daemon=True, name="amber-embed-preload")
    t.start()


def _cosine_sim(a: list, b: list) -> float:
    """计算两个向量的 cosine similarity."""
    import math
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _classify_llm(text: str) -> str:
    """LLM-powered topic classification (v1.2.0).

    Uses MiniMax with extended thinking. May need retry with higher tokens
    if thinking consumes all allocated output tokens.
    """
    if not LLM_READY:
        return ""
    try:
        llm = get_llm()
        if not llm.config.api_key:
            return ""
        prompt = (
            "You are a topic classifier. Given a text in Chinese or English, return 1-3 comma-separated topic tags.\n"
            "Valid tags: 工作,技术,学习,创意,偏好,健康,财务,生活,旅行,家庭,社交,娱乐,灵感,决策,情绪\n"
            "Return ONLY the comma-separated tags on a single line, no explanation.\n"
            "If the text is ambiguous or too short, respond with a single hyphen (-).\n\n"
            f"Text: {text[:500]}\nTags:"
        )
        # Try with 200 tokens first; if no text block appears, retry with 400
        for max_t in (200, 400):
            result = llm.complete(prompt, max_tokens=max_t)
            if result.startswith("[ERROR"):
                return ""
            first_line = result.strip().split("\n")[0].strip()
            if first_line and first_line != "-":
                tags = [t.strip() for t in first_line.split(",") if t.strip()]
                seen = set()
                cleaned = []
                for t in tags:
                    if t and len(t) <= 6 and " " not in t and t not in seen:
                        seen.add(t)
                        cleaned.append(t)
                if cleaned:
                    return ",".join(cleaned[:3])
        return ""
    except Exception:
        return ""


def classify_topics(text: str, existing_tags: str = "") -> str:
    """
    v0.8.9: 可扩展 topic 分类。

    策略：
    1. 关键词匹配（所有用户可用）
    2. 向量模型精调（有模型时）：text vs topic vectors，cosine similarity

    敏感类（偏好/情绪/决策）：必须命中显式关键词，不走向量
    其他类：关键词命中 ≥ 1 → 进入候选；向量 top1 > 0.35 → 加入结果
    最多返回 3 个 topic。
    """
    if not text:
        return existing_tags or ""


# ── v1.2.8: LLM structured memory extraction (Proactive V4) ───────────

_MEMORY_EXTRACT_SYSTEM = """You are a memory analyst. Your task is to extract important, non-obvious facts from a conversation transcript.

Extract memories that are:
- FACT: verifiable information (project names, decisions made, numbers, schedules)
- DECISION: explicit choices or commitments made
- PREFERENCE: expressed likes/dislikes/habits
- ERROR: mistakes, bugs, or failures mentioned
- INSIGHT: non-obvious observations or lessons learned

Rules:
- Only extract memories with importance >= 0.6 (on a 0.0–1.0 scale)
- Return at most 10 memories, sorted by importance descending
- importance = affected_scope × frequency × time_relevance (0.0–1.0)
- tags = max 4 keywords; entities = names/projects/locations found
- Return STRICT JSON only — no markdown, no explanation, no thinking
- If no valuable memories exist, return []"""

_MEMORY_EXTRACT_USER = """Extract structured memories from this conversation:

{conversation}

Existing tags in this user's memory (avoid duplicates, prefer more specific):
{existing_tags}

Return exactly this JSON format:
{{
  "memories": [
    {{
      "type": "fact|decision|preference|error|insight",
      "summary": "one-sentence description of this memory",
      "importance": 0.0-1.0,
      "tags": ["tag1", "tag2"],
      "entities": ["entity1"],
      "expires_at": null
    }}
  ]
}}"""


def _get_existing_tag_context(limit: int = 20) -> str:
    """获取现有标签样本 + 用户修正历史，用于引导 LLM 生成不同标签"""
    db_path = Path.home() / ".amber-hunter" / "hunter.db"
    all_tags = set()

    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        rows = c.execute(
            "SELECT tags FROM capsules ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        for (tags,) in rows:
            for t in (tags or "").split(","):
                t = t.strip()
                if t and not t.startswith("#"):
                    all_tags.add(t.lower())

    # 加入用户修正反馈（用户改过的标签 → 优先用修正后的）
    import json as _json
    feedback_tags = set()
    if db_path.exists():
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        fb_rows = c.execute(
            "SELECT value FROM config WHERE key LIKE 'tag_feedback:%'"
        ).fetchall()
        conn.close()
        for (val,) in fb_rows:
            try:
                corrections = _json.loads(val)
                for corr in corrections:
                    feedback_tags.add(corr)
            except Exception:
                pass

    combined = all_tags | feedback_tags
    return ", ".join(sorted(combined)) if combined else "（无）"


def _normalize_tag(tag: str) -> str:
    """标签归一化：小写化空格 trim，同义词映射"""
    tag = tag.strip().lower()
    SYNONYMS = {
        "py": "python", "js": "javascript",
        "ml": "ai", "llm": "ai",
        "kecheng": "course", "shu": "book", "book": "book",
        "react": "react", "vue": "vue", "angular": "angular",
        "postgres": "postgresql", "postgres": "postgresql",
        "pg": "postgresql",
    }
    return SYNONYMS.get(tag, tag)


def _parse_hierarchical_tag(tag: str) -> tuple[str, str]:
    """返回 (prefix, value)，如 'project:huper' -> ('project', 'huper')"""
    if ":" in tag:
        parts = tag.split(":", 1)
        return parts[0].strip(), parts[1].strip()
    return "", tag


def _normalize_tags(tags_str: str) -> str:
    """对逗号分隔的标签字符串做归一化处理"""
    if not tags_str:
        return tags_str
    parts = []
    for t in tags_str.split(","):
        t = t.strip()
        if not t:
            continue
        # 去掉 # 前缀（如果有）
        if t.startswith("#"):
            t = t[1:]
        normalized = _normalize_tag(t)
        if normalized:
            parts.append(normalized)
    return ",".join(parts)


def _llm_extract_memories(text: str) -> list[dict]:
    """
    v1.2.8: Use LLM to extract structured memories from raw conversation text.

    Returns a list of memory dicts with keys: type, summary, importance, tags, entities, expires_at.
    Only memories with importance >= 0.6 are returned.
    """
    if not text or not LLM_READY:
        return []
    if len(text.strip()) < 50:
        return []

    try:
        llm = get_llm()
        if not llm.config.api_key:
            return []

        existing_tags = _get_existing_tag_context()
        user_prompt = _MEMORY_EXTRACT_USER.format(conversation=text[:6000], existing_tags=existing_tags)
        raw = llm.acomplete(user_prompt, system=_MEMORY_EXTRACT_SYSTEM,
                             max_tokens=1024, temperature=0.3)
        if raw.startswith("[ERROR"):
            return []

        # Parse JSON response
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:] if lines[0].startswith("```") else lines)
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        import json as _json
        data = _json.loads(cleaned)
        memories = data.get("memories", [])

        # Filter: only importance >= 0.6
        filtered = [m for m in memories if isinstance(m, dict) and m.get("importance", 0) >= 0.6]
        # Normalize tags in each memory (dedup + lowercase + synonym)
        for m in filtered:
            raw_tags = m.get("tags", [])
            if isinstance(raw_tags, list):
                normalized = [_normalize_tag(t) for t in raw_tags]
                # Remove duplicates while preserving order
                seen = set()
                unique = []
                for t in normalized:
                    if t and t not in seen:
                        seen.add(t)
                        unique.append(t)
                m["tags"] = unique[:4]  # max 4 tags
        # Sort by importance desc, keep top 10
        filtered.sort(key=lambda x: x.get("importance", 0), reverse=True)
        return filtered[:10]

    except Exception as e:
        import sys
        print(f"[_llm_extract_memories] failed: {e}", file=sys.stderr)
        return []


# ── Insight 缓存 v1.2.17 ──────────────────────────────────────────

_INSIGHT_SYSTEM = """You are a memory analyst. Given a collection of memory capsules from the same category, generate a concise structured summary."""

_INSIGHT_USER = """请将以下同路径的记忆胶囊压缩成一段有组织的摘要：

{capsules}

要求：
- 保留核心事实、关键洞察和决策
- 去除重复信息
- 100字以内
- 纯文本，不要列表
- 用连贯的段落叙述"""


def _generate_insight(path: str, capsule_ids: list[str], memos: list[str], hotness: float) -> dict | None:
    """
    压缩同路径胶囊为 insight 字典。
    失败返回 None。
    """
    if not memos:
        return None
    try:
        llm = get_llm()
        capsules_text = "\n---\n".join(f"[{i+1}] {m}" for i, m in enumerate(memos))
        prompt = _INSIGHT_USER.format(capsules=capsules_text[:3000])
        summary = llm.complete(prompt, system=_INSIGHT_SYSTEM, max_tokens=256, temperature=0.2)
        if summary.startswith("[ERROR"):
            return None
        import time as _time
        import uuid as _uuid
        return {
            "id": _uuid.uuid4().hex[:12],
            "capsule_ids": json.dumps(capsule_ids),
            "summary": summary.strip(),
            "path": path,
            "hotness_score": hotness,
            "created_at": _time.time(),
            "updated_at": _time.time(),
        }
    except Exception as e:
        import sys
        print(f"[_generate_insight] failed: {e}", file=sys.stderr)
        return None


    topics = _get_topics_from_config()
    text_lower = text.lower()
    candidate_topics = []
    topic_scores = {}

    # ── Step 1: 关键词匹配 ────────────────────────────
    for topic in topics:
        name = topic["name"]
        kws = topic.get("keywords", [])
        hit_count = sum(1 for kw in kws if kw.lower() in text_lower)

        # 敏感类：必须显式命中关键词
        if name in EXPLICIT_ONLY_TOPICS:
            if hit_count > 0:
                candidate_topics.append(name)
                topic_scores[name] = 1.0
            continue

        if hit_count > 0:
            candidate_topics.append(name)
            topic_scores[name] = min(hit_count / 2.0, 1.0)  # 归一化 0~1

    # ── Step 2: 向量模型精调（有模型时）───────────────
    model = _get_embed_model()
    if model and text.strip():
        try:
            text_vec = model.encode(text[:1000])  # 截断避免太长
            for topic in topics:
                name = topic["name"]
                # 跳过敏感类（已在上一步处理）
                if name in EXPLICIT_ONLY_TOPICS:
                    continue
                # 用 keywords 作为 topic 向量的代理
                kws = topic.get("keywords", [])
                if not kws:
                    continue
                kw_text = " ".join(kws[:8])  # 最多8个关键词
                topic_vec = model.encode(kw_text)
                sim = _cosine_sim(text_vec.tolist(), topic_vec.tolist())
                if sim > 0.35 and name not in topic_scores:
                    candidate_topics.append(name)
                    topic_scores[name] = sim
                elif sim > topic_scores.get(name, 0):
                    topic_scores[name] = sim
        except Exception:
            pass

    # ── Step 3: 合并已有标签，取 top 3 ─────────────────
    existing = [t.strip() for t in existing_tags.split(",") if t.strip()] if existing_tags else []
    result = list(dict.fromkeys(existing))

    # 按 score 排序，取 top 3（不含已有的）
    for name in sorted(candidate_topics, key=lambda n: topic_scores.get(n, 0), reverse=True)[:3]:
        if name not in result:
            result.append(name)

    return ",".join(result) if result else existing_tags or ""


# 兼容旧名称
_auto_tag_local = classify_topics


from pydantic import BaseModel

HOME = Path.home()
ensure_config_dir()

# ── FastAPI App ────────────────────────────────────────
app = FastAPI(title="Amber Hunter", version="1.2.15")

# CORS：仅允许 huper.org（生产）和 localhost（开发）
# 使用 Starlette CORS middleware（更稳定）
app.add_middleware(
    StarletteCORSMiddleware,
    allow_origins=["https://huper.org", "http://localhost:18998", "http://127.0.0.1:18998"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)

# ── Private Network Access middleware ──────────────────
# Chrome 要求 HTTPS 页面访问 localhost 时，服务端必须在 OPTIONS 预检及实际响应中
# 返回 Access-Control-Allow-Private-Network: true，否则请求被浏览器直接拦截。
@app.middleware("http")
async def private_network_access_middleware(request, call_next):
    response = await call_next(request)
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response

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
    """手动给 Response 添加 CORS origin 头（private-network 由 middleware 处理）"""
    origin = request.headers.get("origin", "")
    h = {}
    if origin in ALLOWED_ORIGINS:
        h["access-control-allow-origin"] = origin
        h["access-control-allow-credentials"] = "true"
    return h

# ── Topic 分类接口（无认证，供 amber-proactive 调用）─────
@app.get("/classify")
def api_classify(request: Request, text: str = ""):
    """对一段文本进行 topic 分类，返回逗号分隔的标签字符串.

    策略：
    1. 关键词匹配（所有用户可用，无网络依赖）
    2. LLM 分类（关键词匹配为空时触发，需要配置 LLM API key）
    """
    headers = add_cors_headers(request)
    if not text or len(text.strip()) < 5:
        return JSONResponse({"topics": ""}, headers=headers)
    topics = classify_topics(text)
    # Fallback to LLM if keyword matching returned little
    if not topics or len(topics.split(",")) < 2:
        topics_llm = _classify_llm(text)
        if topics_llm:
            # Merge without duplicates
            existing = set(t.strip() for t in topics.split(",") if t.strip()) if topics else set()
            new_tags = [t for t in topics_llm.split(",") if t.strip() and t.strip() not in existing]
            all_tags = list(existing) + new_tags
            topics = ",".join(all_tags[:5])
    return JSONResponse({"topics": topics}, headers=headers)


# ── v1.2.8: Proactive V4 — LLM structured memory extraction ─────────
class ExtractIn(BaseModel):
    text: str
    source: str = "unknown"


@app.post("/extract")
async def extract_memories(request: Request, body: ExtractIn, authorization: str = Header(None)):
    """
    LLM-powered structured memory extraction from raw conversation text (Proactive V4).

    Body: {"text": "...", "source": "session_id or description"}
    Returns: {"memories": [{"type": "...", "summary": "...", "importance": 0.0-1.0, "tags": [], "entities": []}]}

    认证：Bearer Token
    """
    headers = add_cors_headers(request)
    if authorization:
        verify_token(authorization)
    if not body.text or len(body.text.strip()) < 50:
        return JSONResponse({"memories": []}, headers=headers)

    memories = _llm_extract_memories(body.text)
    return JSONResponse({"source": body.source, "memories": memories, "count": len(memories)}, headers=headers)


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

# ── B3: 场景预加载记忆 v1.2.18 ────────────────────────────────
@app.get("/session/preload")
def get_session_preload(request: Request, session_id: str = ""):
    """
    返回指定 session 的预加载记忆（供 Agent heartbeat 读取）。
    session_id='' 时返回最新的预加载文件。
    """
    h = add_cors_headers(request)
    preload_dir = HOME / ".amber-hunter" / "preload"
    if not preload_dir.exists():
        return JSONResponse({"scene": "none", "category_path": "", "memories": []}, headers=h)

    if session_id:
        preload_file = preload_dir / f"{session_id}_preload.json"
        if not preload_file.exists():
            return JSONResponse({"scene": "none", "category_path": "", "memories": []}, headers=h)
        data = json.loads(preload_file.read_text())
        return JSONResponse(data, headers=h)
    else:
        files = sorted(preload_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not files:
            return JSONResponse({"scene": "none", "category_path": "", "memories": []}, headers=h)
        data = json.loads(files[0].read_text())
        return JSONResponse(data, headers=h)

@app.api_route("/freeze", methods=["GET", "POST", "OPTIONS"])
def trigger_freeze(request: Request, authorization: str = Header(None)):
    """触发 freeze：返回预填数据（需认证）
    
    认证方式（按优先级）：
    1. Query param: ?token=xxx（解决浏览器混合内容限制）
    2. Header: Authorization: Bearer xxx
    """
    # 处理 CORS preflight
    if request.method == "OPTIONS":
        h = add_cors_headers(request)
        h["access-control-allow-methods"] = "GET, POST, OPTIONS"
        h["access-control-allow-headers"] = "Authorization, Content-Type"
        return JSONResponse({}, headers=h)

    # 优先从 query param 读取 token（兼容混合内容场景）
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)
    session_key = get_current_session_key()
    session_data = build_session_summary(session_key) if session_key else {}
    files = get_recent_files(limit=5)
    prefill = session_data.get("last_topic", "") or ""
    if files:
        file_names = "; ".join([f"{f['path']}" for f in files])
        prefill = f"{prefill}\n\n相关文件：{file_names}" if prefill else file_names

    h = add_cors_headers(request)
    # 如果用户开启了 auto_sync，freeze 时自动触发后台同步
    _spawn_sync_if_enabled()
    return JSONResponse({
        "session_key": session_key,
        "prefill": prefill[:500],
        "summary": session_data.get("summary", ""),
        "preferences": session_data.get("preferences", []),
        "files": files[:5],
        "timestamp": time.time(),
    }, headers=h)

# ── 胶囊 CRUD（需认证）──────────────────────────────────
@app.get("/memories")
def get_memories(limit: int = 20, request: Request = None):
    """
    本地记忆快照——无需账号，仅限 localhost 访问。
    让新用户装完立刻看到价值，注册 huper.org 后可跨设备同步。
    """
    if request and request.client and request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="仅限本地访问 / localhost only")
    capsules = list_capsules(limit=max(1, min(limit, 100)))
    h = add_cors_headers(request) if request else {}
    items = []
    for c in capsules:
        items.append({
            "id":          c["id"],
            "memo":        c["memo"],
            "tags":        c["tags"],
            "category":    c.get("category") or "",
            "source_type": c.get("source_type") or "manual",
            "source":      c.get("window_title") or c.get("session_id") or "unknown",
            "created_at":  c["created_at"],
            "synced":      bool(c["synced"]),
            "encrypted":   bool(c.get("salt")),
        })
    return JSONResponse({
        "total":    len(items),
        "memories": items,
        "hint":     (
            "这是你的本地记忆快照，数据已加密存储在本机。"
            "注册 huper.org 账号后可跨设备同步，并通过 AI 主动召回相关记忆。"
        ),
    }, headers=h)


@app.get("/capsules")
def list_capsules_handler(
    authorization: str = Header(None),
    request: Request = None,
    limit: int = 50,
    category_path: str = "",
):
    """
    列出胶囊列表（支持分页和路径过滤）。
    - limit: 返回数量（默认50，最大300）
    - category_path: MFS路径过滤，支持前缀匹配
    """
    verify_token(authorization)
    limit = max(1, min(limit, 300))
    capsules = list_capsules(limit=limit, category_path=category_path)
    total = count_capsules()
    h = add_cors_headers(request) if request else {}
    return JSONResponse({
        "total":          total,
        "returned":      len(capsules),
        "category_path": category_path,
        "capsules": [
            {
                "id":                    c["id"],
                "memo":                  c["memo"],
                "content":               c.get("content") or "",
                "tags":                  c["tags"],
                "category":              c.get("category") or "",
                "category_path":         c.get("category_path") or "general/default",
                "source_type":           c.get("source_type") or "manual",
                "session_id":            c["session_id"],
                "window_title":          c["window_title"],
                "created_at":            c["created_at"],
                "synced":                bool(c["synced"]),
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

    # 本地自动打标签（E2E 架构：标签在本地生成，加密后上传，服务端不处理内容）
    final_tags = _auto_tag_local(capsule.content or "", capsule.tags or "")

    insert_capsule(
        capsule_id=capsule_id,
        memo=capsule.memo,
        content=ct_b64,
        tags=final_tags,
        session_id=capsule.session_id,
        window_title=capsule.window_title,
        url=capsule.url,
        created_at=now,
        salt=salt_b64,
        nonce=nonce_b64,
        encrypted_len=len(ct_b64) if ct_b64 else 0,
        content_hash=content_hash,
        source_type=getattr(capsule, 'source_type', 'manual'),
        category=getattr(capsule, 'category', '') or _infer_category(capsule.memo or ""),
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


@app.patch("/capsules/{capsule_id}")
def update_capsule(
    capsule_id: str,
    body: CapsuleUpdate,
    authorization: str = Header(None),
    request: Request = None,
):
    """更新胶囊的 memo / tags / category（部分更新）"""
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)

    from core.db import get_capsule
    existing = get_capsule(capsule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="胶囊不存在")

    # 只更新提供的字段
    updates = []
    values = []
    if body.memo is not None:
        updates.append("memo = ?")
        values.append(body.memo)
    if body.tags is not None:
        updates.append("tags = ?")
        values.append(body.tags)
    if body.category is not None:
        updates.append("category = ?")
        values.append(body.category)
    if body.category_path is not None:
        updates.append("category_path = ?")
        values.append(body.category_path)

    if updates:
        updates.append("synced = 0")       # P0-1: 本地修改后必须重新同步
        updates.append("updated_at = ?")   # P0-2: 记录更新时间
        values.append(time.time())
        values.append(capsule_id)
        conn = _get_conn()
        conn.execute(f"UPDATE capsules SET {', '.join(updates)} WHERE id = ?", values)
        conn.commit()

    h = add_cors_headers(request) if request else {}
    return JSONResponse({"status": "ok"}, headers=h)


# ── 主动回忆（需认证）──────────────────────────────────
@app.get("/recall")
def recall_memories(
    request: Request,
    q: str = "",
    limit: int = 3,
    mode: str = "auto",
    rerank: bool = False,
    category_path: str = "",
    use_insights: bool = True,
    authorization: str = Header(None),
):
    """
    AI 在回复前调用此端点，用返回的记忆补充上下文。

    参数：
      q: 搜索查询（用户当前消息）
      limit: 返回记忆数量（默认 3）
      mode: keyword | semantic | auto/hybrid（默认 auto）
      rerank: 是否用 LLM 重排序（默认 False，LLM调用昂贵）
      category_path: MFS路径过滤（如 "projects/huper"），支持前缀匹配

    v1.2.3: hybrid 模式对全量胶囊做语义+关键词联合评分，不再只对关键词候选做语义
    """
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)

    if not q or len(q.strip()) < 2:
        return JSONResponse({"memories": [], "query": q, "mode": mode, "count": 0},
                            headers=add_cors_headers(request))

    q_lower = q.lower().strip()

    # ── Insight 缓存查询 v1.2.17 ─────────────────────────────
    # 如果 use_insights=True 且指定了 category_path（且非默认路径），优先返回 insight 摘要
    if use_insights and category_path and category_path != "general/default":
        conn = _get_conn()
        c = conn.cursor()
        row = c.execute(
            "SELECT id, capsule_ids, summary, path, hotness_score FROM insights "
            "WHERE path=? ORDER BY hotness_score DESC LIMIT 1",
            (category_path,)
        ).fetchone()
        if row:
            import json as _json
            capsule_ids = _json.loads(row[1]) if row[1] else []
            return JSONResponse({
                "type":          "insight",
                "summary":       row[2],
                "source_ids":    capsule_ids,
                "path":          row[3],
                "hotness_score": row[4],
                "count":         len(capsule_ids),
            }, headers=add_cors_headers(request))
        del conn, c

    # ── 读取所有胶囊（含 category_path）────────────────
    conn = _get_conn()
    c = conn.cursor()

    # category_path 前缀过滤（支持路径路由）
    if category_path:
        # 例如 category_path="projects" 时匹配 "projects/huper" 和 "projects/amber-hunter"
        rows = c.execute(
            "SELECT id,memo,content,tags,session_id,window_title,url,created_at,salt,nonce,synced,source_type,category,category_path "
            "FROM capsules WHERE category_path LIKE ? || '%' ORDER BY created_at DESC LIMIT 300",
            (category_path,)
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT id,memo,content,tags,session_id,window_title,url,created_at,salt,nonce,synced,source_type,category,category_path "
            "FROM capsules ORDER BY created_at DESC LIMIT 300"
        ).fetchall()
    # 注意：不断开连接，由连接池管理

    keys = ["id","memo","content","tags","session_id","window_title","url",
            "created_at","salt","nonce","synced","source_type","category","category_path"]
    capsules_raw = [dict(zip(keys, r)) for r in rows]

    # ── v1.2.10+: keyword模式两阶段：先用memo+tags预筛，避免全量解密 ──
    # 第一阶段：只靠未加密的memo+tags评分，不解密任何content
    def _kw_score_memo_tags(cap) -> float:
        score = 0
        qw = q_lower.split()
        memo = (cap.get("memo") or "").lower()
        tags = (cap.get("tags") or "").lower()
        for w in qw:
            score += memo.count(w) * 3
            score += tags.count(w) * 2
        if q_lower in memo: score += 10
        return float(score)

    # keyword模式：只取memo+tags评分最高的50条再解密，避免全量AES解密
    PRE_DECRYPT_LIMIT = 50
    if mode == "keyword":
        # 第一阶段：全部300条只评分不解密
        scored = [(_kw_score_memo_tags(c), c) for c in capsules_raw]
        scored.sort(key=lambda x: x[0], reverse=True)
        # 取top N或所有有分数的
        top_candidates = [c for _, c in scored[:PRE_DECRYPT_LIMIT]]
        # 解密content只对候选者
        master_pw = get_master_password()
        parsed = []
        for cap in top_candidates:
            content = cap.get("content") or ""
            if cap.get("salt") and cap.get("nonce") and content and master_pw:
                try:
                    import base64 as _b64
                    salt = _b64.b64decode(cap["salt"])
                    nonce = _b64.b64decode(cap["nonce"])
                    ciphertext = _b64.b64decode(content)
                    key = derive_key(master_pw, salt)
                    plaintext = decrypt_content(ciphertext, key, nonce)
                    content = plaintext.decode("utf-8") if plaintext else ""
                except Exception as e:
                    import sys
                    print(f"[recall] decrypt failed for {cap.get('id','?')}: {e}", file=sys.stderr)
                    content = ""
            cap["_text"] = f"{cap.get('memo','')}\n{content}"
            cap["_plain_content"] = content
            parsed.append(cap)
        capsules_raw = []  # 释放内存
    else:
        # semantic/hybrid模式：全量解密（语义编码需要完整_text）
        master_pw = get_master_password()
        parsed = []
        for cap in capsules_raw:
            content = cap.get("content") or ""
            if cap.get("salt") and cap.get("nonce") and content and master_pw:
                try:
                    import base64 as _b64
                    salt = _b64.b64decode(cap["salt"])
                    nonce = _b64.b64decode(cap["nonce"])
                    ciphertext = _b64.b64decode(content)
                    key = derive_key(master_pw, salt)
                    plaintext = decrypt_content(ciphertext, key, nonce)
                    content = plaintext.decode("utf-8") if plaintext else ""
                except Exception as e:
                    import sys
                    print(f"[recall] decrypt failed for {cap.get('id','?')}: {e}", file=sys.stderr)
                    content = ""
            cap["_text"] = f"{cap.get('memo','')}\n{content}"
            cap["_plain_content"] = content
            parsed.append(cap)
        capsules_raw = []

    # ── 关键词评分（解密后）────────────────────────
    def _kw_score(cap) -> float:
        score = 0
        qw = q_lower.split()
        memo = (cap.get("memo") or "").lower()
        tags = (cap.get("tags") or "").lower()
        text = (cap.get("_plain_content") or "").lower()
        for w in qw:
            score += memo.count(w) * 3
            score += tags.count(w) * 2
            score += text.count(w)
        if q_lower in memo: score += 10
        if q_lower in text: score += 5
        return float(score)

    kw_scores = [(_kw_score(c), c) for c in parsed]
    max_kw = max((s for s, _ in kw_scores), default=1.0) or 1.0
    # 归一化关键词分到 0-1
    kw_norm = [(s / max_kw, c) for s, c in kw_scores]

    # ── 语义评分（全量，v1.2.3 修复：不再只对关键词候选做）────
    search_mode = mode
    sem_scores: dict[str, float] = {}  # capsule_id -> semantic similarity

    if mode in ("auto", "semantic", "hybrid"):
        try:
            import numpy as _np
            model = _get_embed_model(blocking=False)
            if model is None:
                if _MODEL_LOADING:
                    return JSONResponse({
                        "error": "语义模型正在加载中，请稍后重试",
                        "code": "MODEL_LOADING",
                        "retry_after": 10,
                    }, status_code=503, headers=add_cors_headers(request))
                raise ImportError("embedding model not available")
            q_vec = model.encode(q)
            texts = [c["_text"][:512] for c in parsed]
            if texts:
                cap_vecs = model.encode(texts)
                norms = _np.linalg.norm(cap_vecs, axis=1) * _np.linalg.norm(q_vec) + 1e-8
                sims = _np.dot(cap_vecs, q_vec) / norms
                for i, cap in enumerate(parsed):
                    sem_scores[cap["id"]] = float(sims[i])
            search_mode = "hybrid" if mode in ("auto", "hybrid") else "semantic"
        except ImportError:
            if mode == "semantic":
                return JSONResponse(
                    {"error": "语义搜索需要 sentence-transformers，请运行：pip install sentence-transformers"},
                    status_code=400, headers=add_cors_headers(request)
                )
            search_mode = "keyword"

    # ── 混合评分 + 排序（含 recency/hotness）────────────────
    now_ts = time.time()
    combined = []
    for kw_n, cap in kw_norm:
        sem = sem_scores.get(cap["id"], 0.0)
        # 时间衰减：90天完全衰减到 0.37
        days_old = (now_ts - cap.get("created_at", now_ts)) / 86400
        recency = max(0.0, 1.0 - days_old / 90)
        # 热力值：已有 hit 数据用于加权
        hotness = min(1.0, cap.get("hotness_score", 0) / 10)
        if search_mode == "hybrid":
            final = 0.35 * kw_n + 0.40 * sem + 0.15 * recency + 0.10 * hotness
        elif search_mode == "semantic":
            final = 0.70 * sem + 0.20 * recency + 0.10 * hotness
        else:
            final = 0.80 * kw_n + 0.15 * recency + 0.05 * hotness
        combined.append((final, kw_n, sem, recency, hotness, cap))

    # 过滤掉完全无信号的结果
    if search_mode == "keyword":
        combined = [(s, kw_n, sem, r, h, c) for s, kw_n, sem, r, h, c in combined if s > 0]
    else:
        combined = [(s, kw_n, sem, r, h, c) for s, kw_n, sem, r, h, c in combined if s > 0.05]

    combined.sort(key=lambda x: x[0], reverse=True)
    top = combined[:limit]

    # ── 相关记忆关联（v1.2.8）──────────────────────────────
    def _keyword_overlap(words1: set, words2: set) -> float:
        if not words1 or not words2:
            return 0.0
        return len(words1 & words2) / min(len(words1), len(words2))

    def _find_related(cap_id: str, memo: str, tags: str, all_caps: list, top_ids: set, limit: int = 3) -> list:
        """基于关键词重叠找 top-limit 个相关记忆 ID"""
        memo_w = set(memo.lower().split())
        tag_w = set(tags.lower().split())
        query_w = memo_w | tag_w
        scores = []
        for c in all_caps:
            if c["id"] == cap_id or c["id"] in top_ids:
                continue
            c_memo = (c.get("memo") or "").lower()
            c_tags = (c.get("tags") or "").lower()
            c_w = set(c_memo.split()) | set(c_tags.split())
            overlap = _keyword_overlap(query_w, c_w)
            if overlap > 0:
                scores.append((overlap, c["id"]))
        scores.sort(key=lambda x: x[0], reverse=True)
        return [cid for _, cid in scores[:limit]]

    top_ids = {c["id"] for _, _, _, _, _, c in top}
    related_map: dict[str, list] = {}
    for _, _, _, _, _, cap in top:
        memo = cap.get("memo", "") or ""
        tags = cap.get("tags", "") or ""
        plain = cap.get("_plain_content", "") or ""
        try:
            related_map[cap["id"]] = _find_related(cap["id"], memo + " " + plain, tags, parsed, top_ids)
        except Exception as e:
            import sys
            print(f"[recall] _find_related failed for {cap.get('id','?')}: {e}", file=sys.stderr)
            related_map[cap["id"]] = []

    # ── 组装返回 ─────────────────────────────────
    def _build_memory(score: float, kw_n: float, sem: float, recency: float, hotness: float, cap: dict, related_ids: list) -> dict:
        memo = cap.get("memo", "")
        plain = cap.get("_plain_content", "")
        tags = cap.get("tags", "")
        cat = cap.get("category", "") or ""
        created = cap.get("created_at", 0)
        cat_label = f" [{cat}]" if cat else ""
        injected = (
            f"[琥珀记忆{cat_label} | {tags}]\n"
            f"记忆：{memo}\n"
            f"内容：{plain[:400]}{'...' if len(plain) > 400 else ''}"
        )
        # 生成中文 reason
        parts = []
        if kw_n > 0.5:
            parts.append("关键词命中")
        if sem > 0.5:
            parts.append("语义相似")
        if recency > 0.8:
            parts.append("近期记忆")
        elif recency < 0.3:
            parts.append("久远记忆")
        if hotness > 0.6:
            parts.append("高热记忆")
        reason = "、".join(parts) if parts else "综合相关"
        return {
            "id":              cap["id"],
            "memo":            memo,
            "content":         plain[:500],
            "tags":            tags,
            "category":        cat,
            "category_path":    cap.get("category_path", "general/default"),
            "source_type":     cap.get("source_type", ""),
            "created_at":      created,
            "relevance_score": round(score, 3),
            "breakdown": {
                "keyword_score": round(kw_n, 3),
                "semantic_score": round(sem, 3),
                "recency_score": round(recency, 3),
                "hotness_score": round(hotness, 3),
            },
            "related_ids":     related_ids,
            "reason":          reason,
            "injected_prompt": injected,
            "hit_url":         f"/recall/{cap['id']}/hit",
        }

    memories = [_build_memory(s, kw_n, sem, r, h, c, related_map.get(c["id"], [])) for s, kw_n, sem, r, h, c in top]

    # 清理解密明文
    del parsed
    gc.collect()

    # 可选：LLM 重排序
    if rerank and memories:
        memories = _rerank_memories_llm(q, memories)

    return JSONResponse({
        "memories":          memories[:limit],
        "query":             q,
        "mode":              search_mode,
        "count":             len(memories),
        "semantic_available": _semantic_available(),
    }, headers=add_cors_headers(request))


# ── v1.2.8: Hit tracking ───────────────────────────────────────────
class HitIn(BaseModel):
    session_id: Optional[str] = None
    search_query: Optional[str] = None
    relevance_score: Optional[float] = None


@app.patch("/recall/{capsule_id}/hit")
def record_hit(
    capsule_id: str,
    body: HitIn,
    request: Request,
    authorization: str = Header(None),
):
    """
    Record that a recalled memory was useful (hit tracking v1.2.8).

    Body: {"session_id": "...", "search_query": "...", "relevance_score": 0.85}
    Returns: {"ok": True}
    """
    headers = add_cors_headers(request)
    verify_token(authorization)

    hit_id = secrets.token_hex(8)
    rel = body.relevance_score if body.relevance_score is not None else 0.5

    insert_memory_hit(hit_id, capsule_id, body.session_id, body.search_query, rel)
    update_capsule_hit(capsule_id, rel)

    return JSONResponse({"ok": True}, headers=headers)


def _rerank_memories_llm(query: str, memories: list[dict]) -> list[dict]:
    """Re-rank a list of memory candidates using LLM.

    Sends the query + all memory summaries to the LLM and asks it to score
    and reorder them by relevance to the query. Returns reordered list.

    If LLM is unavailable or fails, returns the original list unchanged.
    """
    if not memories or not LLM_READY:
        return memories

    try:
        llm = get_llm()
        if not llm.config.api_key:
            return memories
    except Exception:
        return memories

    # Build a compact summary of each memory for the LLM context
    mem_lines = []
    for i, m in enumerate(memories):
        memo = (m.get("memo") or "").strip()
        content = (m.get("content") or "")[:200].strip()
        tags = (m.get("tags") or "").strip()
        mem_lines.append(f"[{i}] [{tags}] {memo} | {content}")

    mem_context = "\n".join(mem_lines)

    prompt = (
        "You are a relevance ranker. Given a user query and a list of memory entries, "
        "score each entry 0-10 for how relevant it is to the query, then return the top entries.\n\n"
        f"Query: {query}\n\n"
        f"Memories:\n{mem_context}\n\n"
        "Your task: Rate each memory [0-10] for relevance to the query, "
        "then return the top 3-5 most relevant memories in JSON format.\n"
        "Return STRICTLY valid JSON only, no markdown, no explanation:\n"
        "[{\"index\": N, \"score\": S, \"reason\": \"brief reason\"}, ...]\n"
        "Score guide: 10=directly answers query, 7-9=highly relevant, 4-6=somewhat relevant, 0-3=irrelevant."
    )

    try:
        result = llm.complete(prompt, max_tokens=400)
        if result.startswith("[ERROR") or not result.strip():
            return memories

        # Parse JSON response
        import json as _json
        cleaned = result.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:] if lines[0].startswith("```") else lines)
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]

        scores = _json.loads(cleaned)
        if not isinstance(scores, list):
            return memories

        # Build index → score map
        score_map = {item["index"]: item["score"] for item in scores if "index" in item}

        # Reorder: scored items first (descending), then unscored
        scored = [(score_map.get(i, 0), m) for i, m in enumerate(memories)]
        scored.sort(key=lambda x: x[0], reverse=True)

        # Update relevance_score
        reranked = []
        for raw_score, m in scored:
            m = dict(m)  # copy
            m["relevance_score"] = round(min(raw_score / 10.0, 1.0), 2)
            reranked.append(m)

        return reranked

    except Exception:
        return memories


@app.post("/rerank")
async def rerank_memories(request: Request, authorization: str = Header(None)):
    """Re-rank a list of memory candidates using LLM.

    Body: {"query": "...", "memories": [...]}
    Returns: {"memories": [...reranked...]}
    """
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    query = body.get("query", "")
    memories = body.get("memories", [])

    if not query or not memories:
        return JSONResponse({"memories": memories}, headers=add_cors_headers(request))

    # Run LLM reranking in thread pool to avoid blocking event loop
    import asyncio
    reranked = await asyncio.to_thread(_rerank_memories_llm, query, memories)
    return JSONResponse({"memories": reranked}, headers=add_cors_headers(request))


def _semantic_available() -> bool:
    """检查是否安装了语义搜索依赖（带缓存）"""
    global _SEMANTIC_AVAILABLE
    if _SEMANTIC_AVAILABLE is not None:
        return _SEMANTIC_AVAILABLE
    try:
        import sentence_transformers as _
        import numpy as _
        _SEMANTIC_AVAILABLE = True
        return True
    except ImportError:
        _SEMANTIC_AVAILABLE = False
        return False


# ── 云端同步（需认证）────────────────────────────────


# ── 分类推断 helper（v1.1.9）─────────────────────────────
_CATEGORY_KEYWORDS = {
    "thought":    ["想法", "想到", "突然想", "有个念头", "脑海中", "感觉", "觉得", "意识到",
                   "realize", "just thought", "idea", "thought", "occurred to me"],
    "learning":   ["读了", "看了", "书里", "文章", "这本书", "学到", "理解了", "课程",
                   "reading", "book", "learned", "course", "study"],
    "decision":   ["决定", "选择了", "打算", "确定了", "我们选", "不再", "放弃", "要去", "方案",
                   "decided", "going with", "we chose", "commit to", "will"],
    "reflection": ["反思", "复盘", "回顾", "总结", "想清楚", "发现自己",
                   "reviewed", "reflecting", "looking back", "in retrospect", "realized", "lesson"],
    "people":     ["和.{1,8}聊", "跟.{1,8}说", "和朋友", "跟朋友", "和同事", "跟同事",
                   "聊了", "聊天", "见了", "对话", "和他", "和她",
                   "talked to", "met with", "conversation with", "catchup", "friend"],
    "life":       ["心情", "情绪", "感受", "低落", "开心", "难过", "疲惫", "疲倦", "焦虑",
                   "运动", "睡眠", "跑步", "冥想", "饮食", "健身", "休息",
                   "sleep", "exercise", "workout", "meditation", "health", "mood", "feeling", "tired"],
    "creative":   ["灵感", "创意", "设计", "想做", "想象", "写作", "作品",
                   "inspiration", "design idea", "creative", "writing"],
    "dev":        ["python", "javascript", "git", "docker", "api", "sql",
                   "error", "bug", "code", "deploy", "server", "代码", "报错", "修复", "接口", "部署"],
}

import re as _re

def _infer_category(text: str) -> str:
    """从文本推断大类，返回 category 字符串"""
    t = text.lower()
    scores = {}
    for cat, kws in _CATEGORY_KEYWORDS.items():
        score = 0
        for kw in kws:
            try:
                score += len(_re.findall(kw, t))
            except Exception:
                score += t.count(kw)
        if score > 0:
            scores[cat] = score
    if not scores:
        return ""
    return max(scores, key=scores.get)


# ── /ingest 端点（v1.1.9）─────────────────────────────────
class IngestIn(BaseModel):
    memo: str
    context: str = ""
    category: str = ""
    tags: str = ""
    source: str = "unknown"
    confidence: float = 0.7
    review_required: bool = True
    agent_tag: str = ""  # v1.2.8: agent标识标签，如 "agent:openclaw"


def _get_capsule_count() -> int:
    """获取当前胶囊数量"""
    db_path = Path.home() / ".amber-hunter" / "hunter.db"
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    row = c.execute("SELECT COUNT(*) FROM capsules").fetchone()
    conn.close()
    return row[0] if row else 0


def _insert_sample_memories() -> None:
    """首次启动时插入3条示例记忆（仅执行一次）"""
    samples = [
        ("我偏好简洁的解决方案，会拒绝过度工程化",
         "展示偏好类记忆的用途", "preference", "python, architecture"),
        ("我主要使用 Python 和 JavaScript 开发",
         "展示技能类记忆的用途", "skill", "python, javascript"),
        ("我和 Anke 合作 huper 项目，用 huper 管理项目记忆",
         "展示项目类记忆的用途", "project", "huper, anke"),
    ]
    now = time.time()
    for memo, content, cat, tags in samples:
        cap_id = secrets.token_hex(8)
        insert_capsule(
            capsule_id=cap_id,
            memo=memo,
            content=content,
            tags=f"#sample {tags}",
            session_id=None,
            window_title=None,
            url=None,
            created_at=now,
            source_type="sample",
            category=cat,
        )
        now += 0.01  # 避免时间戳完全相同


@app.post("/ingest")
def ingest_memory(body: IngestIn, request: Request = None,
                  authorization: str = Header(None)):
    """
    AI 主动写入记忆端点（v1.2.8）。
    - capsule_count==0 → 首次体验引导，直接写入并返回 welcome
    - review_required=False 且 confidence>=0.95 → 直接写入 capsules
    - 其余 → 写入 memory_queue 等待用户审核
    支持 Bearer header 或 ?token= query param。
    """
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)

    h = add_cors_headers(request) if request else {}

    # 推断缺失的 category
    category = body.category or _infer_category(body.memo + " " + body.context)

    # ── 首次 ingest：引导体验 + 样例记忆 ─────────────────────
    capsule_count = _get_capsule_count()
    is_first_ingest = (capsule_count == 0)
    final_tags = _normalize_tags(body.tags) if body.tags else ""
    if body.agent_tag:
        agent_part = _normalize_tags(body.agent_tag)
        final_tags = f"{final_tags},agent:{agent_part}" if final_tags else f"agent:{agent_part}"

    # 推断 category_path（MFS 路径）
    category_path = _infer_category_path(body.memo, body.context or "", final_tags)

    if is_first_ingest:
        # 先插入3条样例记忆（仅首次）
        _insert_sample_memories()
        # 强制直接写入，不进队列
        cap_id = secrets.token_hex(8)
        insert_capsule(
            capsule_id=cap_id,
            memo=body.memo,
            content=body.context or "",
            tags=final_tags,
            session_id=None,
            window_title=None,
            url=None,
            created_at=time.time(),
            source_type="ai_chat",
            category=category,
            category_path=category_path,
        )
        return JSONResponse({
            "queued": False,
            "capsule_id": cap_id,
            "welcome": True,
            "message": "这是你的第一条记忆！试着问我一些问题来验证它的效果。",
            "sample_count": 3,
        }, headers=h)

    # 高置信度直接写入
    if not body.review_required and body.confidence >= 0.95:
        cap_id = secrets.token_hex(8)
        insert_capsule(
            capsule_id=cap_id,
            memo=body.memo,
            content=body.context or "",
            tags=final_tags,
            session_id=None,
            window_title=None,
            url=None,
            created_at=time.time(),
            source_type="ai_chat",
            category=category,
            category_path=category_path,
        )
        return JSONResponse({"queued": False, "capsule_id": cap_id,
                             "message": "Saved directly"}, headers=h)

    # 其余进审核队列
    qid = queue_insert(
        memo=body.memo,
        context=body.context,
        category=category,
        tags=final_tags,
        source=body.source,
        confidence=body.confidence,
    )
    return JSONResponse({"queued": True, "queue_id": qid,
                         "message": "Added to review queue"}, headers=h)


# ── /queue 端点（v1.1.9）─────────────────────────────────

@app.get("/queue")
def get_queue(request: Request = None, authorization: str = Header(None)):
    """列出待审核记忆"""
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)
    h = add_cors_headers(request) if request else {}
    pending = queue_list_pending()
    return JSONResponse({"pending": pending, "count": len(pending)}, headers=h)


class QueueEditIn(BaseModel):
    memo: str = ""
    category: str = ""
    tags: str = ""


@app.post("/queue/{qid}/approve")
def approve_queue_item(qid: str, request: Request = None,
                       authorization: str = Header(None)):
    """接受待审核记忆 → 写入 capsules"""
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)
    h = add_cors_headers(request) if request else {}

    item = queue_get(qid)
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404, headers=h)
    if item["status"] != "pending":
        return JSONResponse({"error": "already processed"}, status_code=400, headers=h)

    cap_id = secrets.token_hex(8)
    cap_path = _infer_category_path(item["memo"], item.get("context") or "", item["tags"])
    insert_capsule(
        capsule_id=cap_id,
        memo=item["memo"],
        content=item.get("context") or "",
        tags=item["tags"],
        session_id=None,
        window_title=None,
        url=None,
        created_at=time.time(),
        source_type="ai_chat",
        category=item["category"],
        category_path=cap_path,
    )
    queue_set_status(qid, "approved")
    return JSONResponse({"capsule_id": cap_id, "message": "Approved and saved"}, headers=h)


@app.post("/queue/{qid}/reject")
def reject_queue_item(qid: str, request: Request = None,
                      authorization: str = Header(None)):
    """忽略待审核记忆"""
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)
    h = add_cors_headers(request) if request else {}

    item = queue_get(qid)
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404, headers=h)
    queue_set_status(qid, "rejected")
    return JSONResponse({"message": "Rejected"}, headers=h)


@app.post("/queue/{qid}/edit")
def edit_queue_item(qid: str, body: QueueEditIn, request: Request = None,
                    authorization: str = Header(None)):
    """编辑后接受待审核记忆"""
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)
    h = add_cors_headers(request) if request else {}

    item = queue_get(qid)
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404, headers=h)

    final_memo = body.memo or item["memo"]
    final_category = body.category or item["category"]
    final_tags = body.tags or item["tags"]

    # 检测用户是否修正了标签，记录反馈
    if body.tags and body.tags != item.get("tags", ""):
        original_tags = (item.get("tags") or "").split(",")
        new_tags = body.tags.split(",")
        for ot in original_tags:
            ot = ot.strip()
            if not ot:
                continue
            for nt in new_tags:
                nt = nt.strip()
                if nt and nt != ot:
                    save_tag_feedback(ot, nt)

    queue_update(qid, final_memo, final_category, final_tags)
    cap_id = secrets.token_hex(8)
    insert_capsule(
        capsule_id=cap_id,
        memo=final_memo,
        content=item.get("context") or "",
        tags=final_tags,
        session_id=None,
        window_title=None,
        url=None,
        created_at=time.time(),
        source_type="ai_chat",
        category=final_category,
    )
    return JSONResponse({"capsule_id": cap_id, "message": "Edited and saved"}, headers=h)


# ── /-review 端点（v1.2.8 — 终端友好队列审阅）────────────────

@app.get("/-review")
def review_queue(request: Request = None, authorization: str = Header(None)):
    """
    终端友好的待审核记忆列表。
    curl "http://localhost:18998/-review?token=$TOKEN"
    """
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)
    h = add_cors_headers(request) if request else {}

    pending = queue_list_pending()
    if not pending:
        return JSONResponse({"lines": ["📋 待审阅记忆 (0条)"], "count": 0}, headers=h)

    lines = [f"📋 待审阅记忆 ({len(pending)}条)\n"]
    for i, item in enumerate(pending, 1):
        memo = item.get("memo", "")[:60]
        tags = item.get("tags", "") or "无标签"
        conf = item.get("confidence", 0)
        cat = item.get("category", "") or "general"
        ctx = item.get("context", "")[:80]

        emoji = "💭" if cat == "preference" else "🎯" if cat == "decision" else "📖"
        lines.append(f"[{i}] {emoji} \"{memo}\"")
        lines.append(f"   标签: {tags} | 置信度: {conf:.2f}")
        if ctx:
            lines.append(f"   上下文: {ctx}")
        lines.append("")

    lines.append("---")
    lines.append("操作: curl -X POST \"/-review/{id}?action=approve&token=$TOKEN\"")
    lines.append("     curl -X POST \"/-review/{id}?action=reject&token=$TOKEN\"")

    return JSONResponse({"lines": lines, "count": len(pending), "items": pending}, headers=h)


@app.post("/-review/{qid}")
def review_item(qid: str, request: Request = None, authorization: str = Header(None)):
    """
    审阅单个队列项（approve / reject / edit）。
    curl -X POST "http://localhost:18998/-review/abc123?action=approve&token=$TOKEN"
    curl -X POST "http://localhost:18998/-review/abc123?action=reject&token=$TOKEN"
    """
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)
    h = add_cors_headers(request) if request else {}

    action = request.query_params.get("action") if request else None
    if action not in ("approve", "reject"):
        return JSONResponse({"error": "action must be approve or reject"}, status_code=400, headers=h)

    item = queue_get(qid)
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404, headers=h)
    if item["status"] != "pending":
        return JSONResponse({"error": "already processed"}, status_code=400, headers=h)

    if action == "approve":
        cap_id = secrets.token_hex(8)
        cap_path = _infer_category_path(item["memo"], item.get("context") or "", item["tags"])
        insert_capsule(
            capsule_id=cap_id,
            memo=item["memo"],
            content=item.get("context") or "",
            tags=item["tags"],
            session_id=None,
            window_title=None,
            url=None,
            created_at=time.time(),
            source_type="ai_chat",
            category=item["category"],
            category_path=cap_path,
        )
        queue_set_status(qid, "approved")
        return JSONResponse({"capsule_id": cap_id, "action": "approved"}, headers=h)
    else:
        queue_set_status(qid, "rejected")
        return JSONResponse({"qid": qid, "action": "rejected"}, headers=h)


# ── 后台同步 helper（供 freeze 自动触发 & 定时器共用）────────────
def _do_sync_capsules(unsynced: list, api_token: str, huper_url: str, master_pw: str) -> dict:
    """
    核心同步逻辑（v1.2.15）。
    - 单个 httpx.Client 复用连接，避免每条胶囊建立新 TCP 连接
    - payload 包含 source_type / category，确保云端字段完整
    - P1-4: 5xx 最多重试 2 次（指数退避 1s/2s）
    - P2-9: 网络可达性预检
    - 返回 {"synced": int, "total": int, "errors": list}
    """
    import httpx
    from urllib.parse import urlparse
    import socket

    synced_count = 0
    errors = []

    # P2-9: 网络可达性预检
    parsed = urlparse(huper_url)
    host = parsed.netloc or parsed.path.split("/")[0]
    try:
        sock = socket.create_connection((host, 443), timeout=3.0)
        sock.close()
    except OSError:
        return {"synced": 0, "total": len(unsynced),
                "errors": [{"error": f"network unreachable: {host}"}]}

    try:
        with httpx.Client(timeout=15.0, trust_env=False) as client:
            for capsule in unsynced:
                # ── 准备加密 payload ─────────────────────────────
                salt_b64 = capsule.get("salt")
                if not salt_b64:
                    errors.append({"id": capsule["id"], "error": "no salt, skipped"})
                    continue

                salt = base64.b64decode(salt_b64)
                key  = derive_key(master_pw, salt)

                content_enc   = capsule.get("content") or ""
                content_nonce = capsule.get("nonce")   or ""

                memo_bytes = (capsule.get("memo") or "").encode("utf-8")
                memo_ct, memo_nonce = encrypt_content(memo_bytes, key)
                memo_enc       = base64.b64encode(memo_ct).decode()
                memo_nonce_b64 = base64.b64encode(memo_nonce).decode()

                tags_bytes = (capsule.get("tags") or "").encode("utf-8")
                tags_ct, tags_nonce = encrypt_content(tags_bytes, key)
                tags_enc       = base64.b64encode(tags_ct).decode()
                tags_nonce_b64 = base64.b64encode(tags_nonce).decode()

                payload = {
                    "e2e":           True,
                    "salt":          salt_b64,
                    "memo_enc":      memo_enc,
                    "memo_nonce":    memo_nonce_b64,
                    "content_enc":   content_enc,
                    "content_nonce": content_nonce,
                    "tags_enc":      tags_enc,
                    "tags_nonce":    tags_nonce_b64,
                    "created_at":    capsule.get("created_at"),
                    "session_id":    capsule.get("session_id"),
                    "source_type":   capsule.get("source_type") or "manual",
                    "category":      capsule.get("category") or "",
                }

                # P1-4: 重试逻辑（5xx 最多重试 2 次，指数退避）
                last_err = None
                for attempt in range(3):
                    try:
                        resp = client.post(
                            f"{huper_url}/capsules",
                            json=payload,
                            headers={"Authorization": f"Bearer {api_token}"}
                        )
                        if resp.status_code in (200, 201):
                            mark_synced(capsule["id"])
                            synced_count += 1
                            last_err = None
                            break
                        elif resp.status_code >= 500:
                            # 5xx：还有重试机会则等待后重试
                            if attempt < 2:
                                time.sleep(2 ** attempt)
                                continue
                            # attempt == 2: 重试耗尽，记录错误
                            last_err = {"id": capsule["id"], "status": resp.status_code,
                                        "body": resp.text[:120]}
                        else:
                            # 4xx：不可重试，直接记录错误
                            last_err = {"id": capsule["id"], "status": resp.status_code,
                                        "body": resp.text[:120]}
                    except Exception as e:
                        last_err = {"id": capsule["id"], "error": str(e)}
                        if attempt < 2:
                            time.sleep(2 ** attempt)
                            continue
                if last_err is not None:
                    errors.append(last_err)

    except Exception as e:
        errors.append({"error": f"httpx init failed: {e}"})

    # P3-11: 同步完成后更新 last_sync_at（无论成功与否）
    set_config("last_sync_at", str(time.time()))
    return {"synced": synced_count, "total": len(unsynced), "errors": errors}


def _background_sync() -> dict:
    """后台线程同步入口（无 HTTP 上下文）。"""
    try:
        api_token = get_api_token()
        huper_url = get_huper_url() or "https://huper.org/api"
        master_pw = get_master_password()
        if not master_pw:
            logging.warning("[amber-hunter] auto-sync: master_password not set, skip")
            return {"synced": 0, "total": 0, "errors": ["master_password not set"]}
        unsynced = get_unsynced_capsules()
        if not unsynced:
            return {"synced": 0, "total": 0, "errors": []}
        result = _do_sync_capsules(unsynced, api_token, huper_url, master_pw)
        logging.info(f"[amber-hunter] auto-sync: {result['synced']}/{result['total']}")
        return result
    except Exception as e:
        logging.error(f"[amber-hunter] _background_sync error: {e}")
        set_config("sync_last_error", json.dumps({
            "ts": time.time(), "msg": str(e),
        }))
        return {"synced": 0, "total": 0, "errors": [str(e)]}


# P1-6: 同步并发锁，防止重复并发同步
_sync_lock = threading.Lock()


def _spawn_sync_if_enabled():
    """如果 auto_sync 已启用，在守护线程里执行同步（非阻塞）。"""
    if get_config("auto_sync") == "true":
        if not _sync_lock.acquire(blocking=False):
            logging.debug("[amber-hunter] sync already running, skipping")
            return
        try:
            t = threading.Thread(target=_background_sync_locked, daemon=True)
            t.start()
        except Exception:
            _sync_lock.release()
            raise


def _background_sync_locked():
    """在锁内运行的 _background_sync wrapper，释放锁"""
    try:
        _background_sync()
    finally:
        _sync_lock.release()


@app.get("/sync")
def sync_to_cloud(request: Request, authorization: str = Header(None)):
    """
    E2E 加密同步到 huper 云端。
    - memo + tags 圤本地加密后上传，服务端仅存密文
    - content 已圤本地加密，直接传输密文，无需解密
    - 服务端永远看不到任何明文内容
    """
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)

    api_token = get_api_token()
    huper_url = get_huper_url() or "https://huper.org/api"
    master_pw = get_master_password()
    if not master_pw:
        return JSONResponse(
            {"error": "master_password not set", "detail": "请在 dashboard 设置 master_password"},
            status_code=400, headers=add_cors_headers(request)
        )

    unsynced = get_unsynced_capsules()
    if not unsynced:
        return JSONResponse({"synced": 0, "total": 0, "message": "没有需要同步的胶囊"},
                            headers=add_cors_headers(request))

    # P1-5: 分批处理，每批 50 条，避免大量胶囊时超时/内存爆炸
    BATCH_SIZE = 50
    total_synced = 0
    all_errors = []
    for i in range(0, len(unsynced), BATCH_SIZE):
        batch = unsynced[i:i + BATCH_SIZE]
        result = _do_sync_capsules(batch, api_token, huper_url, master_pw)
        total_synced += result["synced"]
        all_errors.extend(result["errors"])
        if i + BATCH_SIZE < len(unsynced):
            time.sleep(0.3)  # 批次间稍作延迟，避免瞬时过载

    logging.info(f"[amber-hunter] /sync: {total_synced}/{len(unsynced)}")
    h = add_cors_headers(request)
    return JSONResponse({
        "synced": total_synced,
        "total":  len(unsynced),
        "failed": len(all_errors),
        "errors": all_errors[:10] if all_errors else None,
        "partial": total_synced > 0 and total_synced < len(unsynced),
        "all_synced": total_synced == len(unsynced),
    }, headers=h)

# ── 配置读取（Dashboard 用）────────────────────────────
class ConfigIn(BaseModel):
    auto_sync: Optional[bool] = None
    sync_interval_minutes: Optional[int] = None  # P3-13

@app.get("/config")
def get_config_handler(request: Request, authorization: str = Header(None)):
    """获取配置（auto_sync、sync_interval_minutes 等）"""
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)
    auto_sync = get_config("auto_sync")
    sync_interval = get_config("sync_interval_minutes")
    return JSONResponse({
        "auto_sync": auto_sync == "true",
        "sync_interval_minutes": int(sync_interval) if sync_interval else 30,
    }, headers=add_cors_headers(request))

@app.post("/config")
def set_config_handler(cfg_in: ConfigIn, request: Request, authorization: str = Header(None)):
    """更新配置"""
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)
    if cfg_in.auto_sync is not None:
        set_config("auto_sync", "true" if cfg_in.auto_sync else "false")
    if cfg_in.sync_interval_minutes is not None:
        set_config("sync_interval_minutes", str(cfg_in.sync_interval_minutes))
    return JSONResponse({"ok": True}, headers=add_cors_headers(request))

@app.get("/config/llm")
async def get_llm_config(request: Request, authorization: str = Header(None)):
    """获取当前 LLM provider 配置（不返回 api_key 明文）"""
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)
    cfg = load_llm_config()
    safe_config = {
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url,
    }
    return JSONResponse(safe_config, headers=add_cors_headers(request))

@app.put("/config/llm")
async def update_llm_config(request: Request, authorization: str = Header(None)):
    """更新 LLM provider 配置"""
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)
    body = await request.json()
    provider = body.get("provider")
    model = body.get("model")
    if provider not in ("minimax", "openai", "claude", "local"):
        return JSONResponse({"error": "invalid provider"}, status_code=400)
    cfg = load_llm_config()
    if provider:
        cfg.provider = provider
    if model:
        cfg.model = model
    save_llm_config(cfg)
    return JSONResponse({"ok": True, "provider": cfg.provider}, headers=add_cors_headers(request))

# ── master_password 设置（Dashboard 用）────────────────
from pydantic import BaseModel
class BindApiKeyIn(BaseModel):
    api_key: str

@app.post("/bind-apikey")
def bind_apikey_handler(payload: BindApiKeyIn, request: Request):
    """更新 Huper 云端 API Key（仅限本机请求）"""
    client = request.client
    if client and client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        import json as _json
        cfg = {}
        if CONFIG_PATH.exists():
            cfg = _json.loads(CONFIG_PATH.read_text())
        cfg["api_key"] = payload.api_key
        CONFIG_PATH.parent.mkdir(exist_ok=True)
        CONFIG_PATH.write_text(_json.dumps(cfg, indent=2))
        return JSONResponse({"ok": True}, headers=add_cors_headers(request))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500, headers=add_cors_headers(request))

class MasterPasswordIn(BaseModel):
    password: str

@app.post("/master-password")
def set_master_password_handler(password_in: MasterPasswordIn, request: Request):
    """设置 master_password（存 macOS Keychain + config.json 双备份）"""
    client = request.client
    if client and client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    ok1 = set_master_password(password_in.password)
    # 同时写到 config.json 作为 fallback（Keychain 访问可能受限）
    try:
        import json as _json
        cfg = {}
        if CONFIG_PATH.exists():
            cfg = _json.loads(CONFIG_PATH.read_text())
        cfg["master_password"] = password_in.password
        CONFIG_PATH.parent.mkdir(exist_ok=True)
        CONFIG_PATH.write_text(_json.dumps(cfg, indent=2))
        ok2 = True
    except Exception:
        ok2 = False
    return JSONResponse({"ok": ok1 or ok2, "keychain": ok1, "config": ok2}, headers=add_cors_headers(request))

# ── 本地 Token（仅 localhost 可读）──────────────────────
@app.get("/token")
def get_local_token(request: Request):
    """返回本地 API token（仅限本机请求，browser→amber-hunter 直连用）"""
    client = request.client
    if client and client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    token = get_api_token()
    if not token:
        return JSONResponse({"api_key": None}, headers=add_cors_headers(request))
    return JSONResponse({"api_key": token}, headers=add_cors_headers(request))

# ── MFS 路径归类（需认证）────────────────────────────────
@app.post("/admin/backfill-paths")
def backfill_paths(request: Request, authorization: str = Header(None), dry_run: bool = True):
    """
    批量归类历史胶囊的 category_path。
    dry_run=true（默认）只报告不写入。
    """
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)
    stats = backfill_category_paths(dry_run=dry_run)
    return JSONResponse({
        "dry_run": dry_run,
        "total_checked": stats["total"],
        "would_update": stats["updated"],
        "by_path": stats["by_path"],
    }, headers=add_cors_headers(request))

# ── Insight 缓存生成（需认证）───────────────────────────────
@app.post("/admin/generate-insights")
def generate_insights(request: Request, authorization: str = Header(None), path: str = ""):
    """
    手动触发 insight 压缩任务。
    path='' 时压缩所有路径（至少3个胶囊），否则只压缩指定路径。
    """
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)
    conn = _get_conn()
    c = conn.cursor()

    stats = {"total": 0, "by_path": {}}

    if path:
        # 指定路径压缩
        rows = c.execute(
            "SELECT id, memo, hotness_score FROM capsules WHERE category_path=? ORDER BY hotness_score DESC LIMIT 20",
            (path,)
        ).fetchall()
        if len(rows) >= 3:
            ids = [r[0] for r in rows]
            memos = [r[1] for r in rows if r[1]]
            avg_hot = sum(r[2] or 0 for r in rows) / len(rows)
            insight = _generate_insight(path, ids, memos, avg_hot)
            if insight:
                c.execute(
                    "INSERT OR REPLACE INTO insights (id, capsule_ids, summary, path, hotness_score, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (insight["id"], insight["capsule_ids"], insight["summary"], insight["path"],
                     insight["hotness_score"], insight["created_at"], insight["updated_at"])
                )
                conn.commit()
                stats["total"] = 1
                stats["by_path"][path] = 1
    else:
        # 所有路径：取至少有3个胶囊的路径
        paths = c.execute(
            "SELECT category_path FROM capsules WHERE category_path!='general/default' GROUP BY category_path HAVING COUNT(*)>=3"
        ).fetchall()
        for (p,) in paths:
            rows = c.execute(
                "SELECT id, memo, hotness_score FROM capsules WHERE category_path=? ORDER BY hotness_score DESC LIMIT 20",
                (p,)
            ).fetchall()
            if len(rows) < 3:
                continue
            # 跳过已有近期 insight 的路径（7天内更新过）
            recent = c.execute(
                "SELECT 1 FROM insights WHERE path=? AND updated_at>?",
                (p, time.time() - 86400 * 7)
            ).fetchone()
            if recent:
                continue
            ids = [r[0] for r in rows]
            memos = [r[1] for r in rows if r[1]]
            avg_hot = sum(r[2] or 0 for r in rows) / len(rows)
            insight = _generate_insight(p, ids, memos, avg_hot)
            if insight:
                c.execute(
                    "INSERT OR REPLACE INTO insights (id, capsule_ids, summary, path, hotness_score, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (insight["id"], insight["capsule_ids"], insight["summary"], insight["path"],
                     insight["hotness_score"], insight["created_at"], insight["updated_at"])
                )
                stats["total"] += 1
                stats["by_path"][p] = stats["by_path"].get(p, 0) + 1

        if stats["total"] > 0:
            conn.commit()

    return JSONResponse({
        "insights_generated": stats["total"],
        "by_path": stats["by_path"],
    }, headers=add_cors_headers(request))

# ── A2: DID 多设备身份 v1.2.20 ─────────────────────────────

@app.post("/did/setup")
def did_setup(request: Request, authorization: str = Header(None)):
    """
    在本设备设置 DID 身份（生成助记词，派生设备密钥）。
    助记词仅此一次显示，需用户备份。
    """
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)

    from core.crypto import generate_mnemonic, mnemonic_to_master, derive_identity_keypair, derive_device_key, pubkey_to_did, privkey_to_hex, pubkey_to_hex

    # 生成助记词
    mnemonic = generate_mnemonic(256)
    # 派生密钥
    master = mnemonic_to_master(mnemonic, "amber@local")
    identity_priv, identity_pub = derive_identity_keypair(master)
    device_uuid = secrets.token_hex(8)
    device_priv, device_pub = derive_device_key(master, device_uuid)
    did_str = pubkey_to_did(identity_pub)
    device_priv_hex = privkey_to_hex(device_priv)
    device_pub_hex = pubkey_to_hex(device_pub)

    # 保存到本地配置（设备私钥 hex 明文存储，用户需知情）
    import json
    did_config = {
        "did": did_str,
        "device_id": device_uuid,
        "device_pub": device_pub_hex,
        "mnemonic": mnemonic,  # 仅此一次
    }
    did_path = HOME / ".amber-hunter" / "did.json"
    did_path.parent.mkdir(parents=True, exist_ok=True)
    did_path.write_text(json.dumps(did_config))

    return JSONResponse({
        "did": did_str,
        "mnemonic": mnemonic,
        "device_id": device_uuid,
    }, headers=add_cors_headers(request))


@app.get("/did/status")
def did_status(request: Request, authorization: str = Header(None)):
    """查询本设备 DID 身份状态"""
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)

    import json
    did_path = HOME / ".amber-hunter" / "did.json"
    if not did_path.exists():
        return JSONResponse({"has_did": False})
    cfg = json.loads(did_path.read_text())
    return JSONResponse({
        "has_did": True,
        "did": cfg.get("did"),
        "device_id": cfg.get("device_id"),
    })


@app.post("/did/register-device")
def did_register_device(request: Request, authorization: str = Header(None)):
    """
    将本设备注册到云端 DID（需要云端账户已设置 DID）。
    """
    raw_token = _extract_bearer_token(request, authorization)
    verify_token(raw_token)

    import json
    did_path = HOME / ".amber-hunter" / "did.json"
    if not did_path.exists():
        return JSONResponse({"error": "请先调用 /did/setup 设置 DID 身份"}, status_code=400)

    cfg = json.loads(did_path.read_text())
    huper_url = get_huper_url()

    # 调用云端 /api/did/devices/register 注册设备公钥
    import httpx
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                f"{huper_url}/did/devices/register",
                json={
                    "device_id": cfg["device_id"],
                    "device_pub": cfg["device_pub"],
                    "did": cfg["did"],
                },
                headers={"Authorization": f"Bearer {raw_token}"}
            )
        if resp.status_code == 200:
            return JSONResponse({"ok": True, "device_id": cfg["device_id"]})
        else:
            return JSONResponse({"error": f"云端注册失败: {resp.status_code}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"网络错误: {e}"}, status_code=500)

# ── 服务状态（无需认证）────────────────────────────────
@app.get("/status")
def get_status(request: Request):
    session_key = get_current_session_key()
    master_pw = get_master_password()
    api_token = get_api_token()
    h = add_cors_headers(request)

    # v1.2.3: DB 统计 + 模型状态 + 队列信息
    db_stats = {"capsule_count": 0, "queue_pending": 0, "last_sync": None}
    try:
        db_path = HOME / ".amber-hunter" / "hunter.db"
        if db_path.exists():
            _conn = sqlite3.connect(str(db_path))
            _c = _conn.cursor()
            row = _c.execute("SELECT COUNT(*) FROM capsules").fetchone()
            db_stats["capsule_count"] = row[0] if row else 0
            row2 = _c.execute(
                "SELECT COUNT(*) FROM memory_queue WHERE status='pending'"
            ).fetchone()
            db_stats["queue_pending"] = row2[0] if row2 else 0
            _conn.close()
    except Exception:
        pass

    # P3-11: last_sync 改用独立 config 记录（不受 created_at 影响）
    last_sync_ts = get_config("last_sync_at")
    db_stats["last_sync"] = float(last_sync_ts) if last_sync_ts else None
    # P1-7: 同步错误持久化
    sync_last_err = get_config("sync_last_error")
    sync_last_error = None
    if sync_last_err:
        try:
            sync_last_error = json.loads(sync_last_err)
        except Exception:
            sync_last_error = sync_last_err

    return JSONResponse({
        "running":            True,
        "version":            "1.2.20",
        "platform":           get_os(),
        "headless":           is_headless(),
        "session_key":        session_key,
        "has_master_password": bool(master_pw),
        "has_api_token":      bool(api_token),
        "workspace":          str(HOME / ".openclaw" / "workspace"),
        "huper_url":          get_huper_url(),
        "semantic_model_loaded": _EMBED_MODEL is not None,
        "semantic_model_state": (
            "loading" if _MODEL_LOADING
            else "ready" if _EMBED_MODEL is not None
            else "error" if _MODEL_LOAD_ERROR
            else "unavailable"
        ),
        "semantic_model_error": _MODEL_LOAD_ERROR,
        "capsule_count":      db_stats["capsule_count"],
        "queue_pending":      db_stats["queue_pending"],
        "last_sync":          db_stats["last_sync"],
        "sync_last_error":   sync_last_error,
    }, headers=h)

@app.get("/")
def root(request: Request):
    h = add_cors_headers(request)
    return JSONResponse({"service": "amber-hunter", "version": "1.2.20", "docs": "/docs"}, headers=h)

# ── 启动 ───────────────────────────────────────────────
def main():
    init_db()
    print("🌙 Amber-Hunter v1.2.20 启动")
    print(f"   Session目录: {HOME / '.openclaw' / 'agents'}")
    print(f"   Workspace:   {HOME / '.openclaw' / 'workspace'}")
    print(f"   数据库:      {HOME / '.amber-hunter' / 'hunter.db'}")
    print(f"   API:        http://localhost:18998/")
    print(f"   CORS:       https://huper.org + localhost")
    print(f"   认证:       本地 API token")
    # P3-13: 启动定时同步守护线程（间隔可配置，默认 30 分钟）
    def _periodic_sync_loop():
        while True:
            interval_minutes = int(get_config("sync_interval_minutes") or 30)
            interval_seconds = interval_minutes * 60
            time.sleep(interval_seconds)   # 先休眠再执行，避免启动时立即同步
            _spawn_sync_if_enabled()
    t = threading.Thread(target=_periodic_sync_loop, daemon=True, name="amber-periodic-sync")
    t.start()
    # 后台预加载语义模型
    _preload_embed_model()

    uvicorn.run(app, host="127.0.0.1", port=18998, log_level="warning")

if __name__ == "__main__":
    main()
