#!/usr/bin/env python3
"""
Amber-Hunter v1.2.0
Huper琥珀本地感知引擎

兼容 huper v1.0.0（DID 身份层）
"""

import os, sys, json, time, secrets, sqlite3, hashlib, base64, gc, threading, logging
from pathlib import Path

# ── 核心模块 ────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from core.crypto import derive_key, encrypt_content, decrypt_content, generate_salt
from core.keychain import (
    get_master_password, set_master_password,
    get_api_token, get_huper_url,
    ensure_config_dir, CONFIG_PATH,
    get_os, is_headless,
)
from core.db import (init_db, insert_capsule, get_capsule, list_capsules, mark_synced,
    get_unsynced_capsules, get_config, set_config,
    queue_insert, queue_list_pending, queue_get, queue_set_status, queue_update)
from core.session import get_current_session_key, build_session_summary, get_recent_files
from core.models import CapsuleIn
from core.llm import get_llm, LLM_AVAILABLE as LLM_READY

# ── FastAPI ─────────────────────────────────────────────
import uvicorn
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware as StarletteCORSMiddleware
from starlette.responses import Response

# ── 语义模型缓存（模块级，只加载一次）────────────────────
_EMBED_MODEL = None

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



def _get_embed_model():
    """懒加载向量模型（all-MiniLM-L6-v2）."""
    global _EMBED_MODEL
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL
    try:
        from sentence_transformers import SentenceTransformer
        _EMBED_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        return _EMBED_MODEL
    except Exception:
        return None


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
app = FastAPI(title="Amber Hunter", version="0.9.6")

# CORS：仅允许 huper.org（生产）和 localhost（开发）
# 使用 Starlette CORS middleware（更稳定）
app.add_middleware(
    StarletteCORSMiddleware,
    allow_origins=["https://huper.org", "http://localhost:18998", "http://127.0.0.1:18998"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
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
    """手动给 Response 添加 CORS 头（含 Private Network Access）"""
    origin = request.headers.get("origin", "")
    h = {"access-control-allow-private-network": "true"}
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
    raw_token = request.query_params.get("token")
    if not raw_token:
        raw_token = authorization
    else:
        raw_token = f"Bearer {raw_token}"  # verify_token 期望 Bearer 前缀
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
            "id":         c["id"],
            "memo":       c["memo"],
            "tags":       c["tags"],
            "source":     c.get("window_title") or c.get("session_id") or "unknown",
            "created_at": c["created_at"],
            "synced":     bool(c["synced"]),
            "encrypted":  bool(c.get("salt")),
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
def list_capsules_handler(authorization: str = Header(None), request: Request = None):
    verify_token(authorization)
    capsules = list_capsules(limit=50)
    h = add_cors_headers(request) if request else {}
    return JSONResponse({
        "capsules": [
            {
                "id": c["id"],
                "memo": c["memo"],
                "content": c.get("content") or "",
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

# ── 主动回忆（需认证）──────────────────────────────────
@app.get("/recall")
def recall_memories(
    request: Request,
    q: str = "",
    limit: int = 3,
    mode: str = "auto",
    rerank: bool = False,
    authorization: str = Header(None),
):
    """
        else:
    AI 在回复前调用此端点，用返回的记忆补充上下文。

    参数：
      q: 搜索查询（用户当前消息）
      limit: 返回记忆数量（默认 3）
      mode: keyword | semantic | auto（默认 auto）
      rerank: 是否用 LLM 重排序（默认 False，需要 LLM API key）

    返回：
      memories: 相关记忆列表，每条含 injected_prompt（注入用的提示文本）
    """
    raw_token = request.query_params.get("token")
    if not raw_token:
        raw_token = authorization
    else:
        raw_token = f"Bearer {raw_token}"
    verify_token(raw_token)

    if not q or len(q.strip()) < 2:
        return JSONResponse({"memories": [], "query": q, "mode": mode, "count": 0},
                            headers=add_cors_headers(request))

    q_lower = q.lower().strip()

    # ── 读取所有胶囊 ────────────────────────────────
    conn = sqlite3.connect(str(HOME / ".amber-hunter" / "hunter.db"))
    c = conn.cursor()
    rows = c.execute(
        "SELECT id,memo,content,tags,session_id,window_title,url,created_at,salt,nonce,synced "
        "FROM capsules ORDER BY created_at DESC LIMIT 200"
    ).fetchall()
    conn.close()

    keys = ["id","memo","content","tags","session_id","window_title","url","created_at","salt","nonce","synced"]
    capsules = [dict(zip(keys, r)) for r in rows]

    # ── 解密 content ──────────────────────────────
    master_pw = get_master_password()
    parsed_capsules = []
    for cap in capsules:
        content = cap.get("content") or ""
        if cap.get("salt") and cap.get("nonce") and content and master_pw:
            try:
                import base64
                salt = base64.b64decode(cap["salt"])
                nonce = base64.b64decode(cap["nonce"])
                ciphertext = base64.b64decode(content)
                key = derive_key(master_pw, salt)
                plaintext = decrypt_content(ciphertext, key, nonce)
                content = plaintext.decode("utf-8") if plaintext else ""
            except Exception:
                content = ""
        cap["_decrypted_content"] = content
        parsed_capsules.append(cap)

    # ── 关键词搜索 ────────────────────────────────
    def score_keyword(cap):
        score = 0
        qw = q_lower.split()
        memo = (cap.get("memo") or "").lower()
        tags = (cap.get("tags") or "").lower()
        content = (cap.get("_decrypted_content") or "").lower()
        for w in qw:
            score += memo.count(w) * 3
            score += tags.count(w) * 2
            score += content.count(w)
        # 包含完整查询句子的加分
        if q_lower in memo: score += 10
        if q_lower in content: score += 5
        return score

    scored = [(score_keyword(c), c) for c in parsed_capsules]
    scored = [(s, c) for s, c in scored if s > 0]
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:limit]

    # ── 构建注入提示 ──────────────────────────────
    memories = []
    for score, cap in top:
        content = cap.get("_decrypted_content", "")
        memo = cap.get("memo", "")
        tags = cap.get("tags", "")
        created = cap.get("created_at", 0)

        injected = f"""[琥珀记忆 | {tags} | {created:.0f}]
记忆：{memo}
内容：{content[:300]}{"..." if len(content) > 300 else ""}"""
        memories.append({
            "id": cap["id"],
            "memo": memo,
            "content": content[:500],
            "tags": tags,
            "created_at": created,
            "relevance_score": round(min(score / 10, 1.0), 2),
            "injected_prompt": injected,
        })

    # ── 语义搜索（可选）───────────────────────────
    search_mode = mode
    if mode == "auto" or mode == "semantic":
        try:
            global _EMBED_MODEL
            if _EMBED_MODEL is None:
                from sentence_transformers import SentenceTransformer
                _EMBED_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
            model = _EMBED_MODEL
            q_vec = model.encode(q)
            cap_vecs = model.encode([c.get("_decrypted_content", "")[:512] for _, c in scored[:50]])
            # 简单余弦相似度
            import numpy as np
            sims = np.dot(cap_vecs, q_vec) / (np.linalg.norm(cap_vecs, axis=1) * np.linalg.norm(q_vec) + 1e-8)
            top_semantic = sorted(
                [(float(sims[i]), scored[i][1]) for i in range(len(sims))],
                key=lambda x: x[0], reverse=True
            )[:limit]
            # 合并去重
            seen = {m["id"] for m in memories}
            for s, c in top_semantic:
                if c["id"] not in seen:
                    memories.append({
                        "id": c["id"],
                        "memo": c.get("memo", ""),
                        "content": c.get("_decrypted_content", "")[:500],
                        "tags": c.get("tags", ""),
                        "created_at": c.get("created_at", 0),
                        "relevance_score": round(float(s), 2),
                        "injected_prompt": f"[琥珀记忆 | {c.get('tags','')}]\n记忆：{c.get('memo','')}\n内容：{c.get('_decrypted_content','')[:300]}",
                    })
                    seen.add(c["id"])
                    if len(memories) >= limit:
                        break
            search_mode = "semantic+keyword"
        except ImportError:
            if mode == "semantic":
                return JSONResponse(
                    {"error": "semantic search requires sentence-transformers. Install: pip install sentence-transformers"},
                    status_code=400, headers=add_cors_headers(request)
                )
            search_mode = "keyword"

    # 缩短解密明文在内存中的存活窗口
    del parsed_capsules
    gc.collect()

    # 可选：LLM 重排序（异步，不阻塞主流程）
    if rerank and memories:
        memories = _rerank_memories_llm(q, memories)

    return JSONResponse({
        "memories": memories[:limit],
        "query": q,
        "mode": search_mode,
        "count": len(memories),
        "semantic_available": _semantic_available(),
    }, headers=add_cors_headers(request))


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
    raw_token = request.query_params.get("token")
    if not raw_token:
        raw_token = authorization
    else:
        raw_token = f"Bearer {raw_token}"
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
    """检查是否安装了语义搜索依赖"""
    try:
        import sentence_transformers as _
        import numpy as _
        return True
    except ImportError:
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


@app.post("/ingest")
def ingest_memory(body: IngestIn, request: Request = None,
                  authorization: str = Header(None)):
    """
    AI 主动写入记忆端点（v1.1.9）。
    - review_required=False 且 confidence>=0.95 → 直接写入 capsules
    - 其余 → 写入 memory_queue 等待用户审核
    支持 Bearer header 或 ?token= query param。
    """
    raw_token = request.query_params.get("token") if request else None
    if raw_token:
        raw_token = f"Bearer {raw_token}"
    else:
        raw_token = authorization
    verify_token(raw_token)

    h = add_cors_headers(request) if request else {}

    # 推断缺失的 category
    category = body.category or _infer_category(body.memo + " " + body.context)

    # 高置信度直接写入
    if not body.review_required and body.confidence >= 0.95:
        cap_id = secrets.token_hex(8)
        insert_capsule(
            capsule_id=cap_id,
            memo=body.memo,
            content="",
            tags=body.tags,
            session_id=None,
            window_title=None,
            url=None,
            created_at=time.time(),
            source_type="ai_chat",
            category=category,
        )
        return JSONResponse({"queued": False, "capsule_id": cap_id,
                             "message": "Saved directly"}, headers=h)

    # 其余进审核队列
    qid = queue_insert(
        memo=body.memo,
        context=body.context,
        category=category,
        tags=body.tags,
        source=body.source,
        confidence=body.confidence,
    )
    return JSONResponse({"queued": True, "queue_id": qid,
                         "message": "Added to review queue"}, headers=h)


# ── /queue 端点（v1.1.9）─────────────────────────────────

@app.get("/queue")
def get_queue(request: Request = None, authorization: str = Header(None)):
    """列出待审核记忆"""
    raw_token = request.query_params.get("token") if request else None
    if raw_token:
        raw_token = f"Bearer {raw_token}"
    else:
        raw_token = authorization
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
    raw_token = request.query_params.get("token") if request else None
    if raw_token:
        raw_token = f"Bearer {raw_token}"
    else:
        raw_token = authorization
    verify_token(raw_token)
    h = add_cors_headers(request) if request else {}

    item = queue_get(qid)
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404, headers=h)
    if item["status"] != "pending":
        return JSONResponse({"error": "already processed"}, status_code=400, headers=h)

    cap_id = secrets.token_hex(8)
    insert_capsule(
        capsule_id=cap_id,
        memo=item["memo"],
        content="",
        tags=item["tags"],
        session_id=None,
        window_title=None,
        url=None,
        created_at=time.time(),
        source_type="ai_chat",
        category=item["category"],
    )
    queue_set_status(qid, "approved")
    return JSONResponse({"capsule_id": cap_id, "message": "Approved and saved"}, headers=h)


@app.post("/queue/{qid}/reject")
def reject_queue_item(qid: str, request: Request = None,
                      authorization: str = Header(None)):
    """忽略待审核记忆"""
    raw_token = request.query_params.get("token") if request else None
    if raw_token:
        raw_token = f"Bearer {raw_token}"
    else:
        raw_token = authorization
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
    raw_token = request.query_params.get("token") if request else None
    if raw_token:
        raw_token = f"Bearer {raw_token}"
    else:
        raw_token = authorization
    verify_token(raw_token)
    h = add_cors_headers(request) if request else {}

    item = queue_get(qid)
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404, headers=h)

    final_memo = body.memo or item["memo"]
    final_category = body.category or item["category"]
    final_tags = body.tags or item["tags"]

    queue_update(qid, final_memo, final_category, final_tags)
    cap_id = secrets.token_hex(8)
    insert_capsule(
        capsule_id=cap_id,
        memo=final_memo,
        content="",
        tags=final_tags,
        session_id=None,
        window_title=None,
        url=None,
        created_at=time.time(),
        source_type="ai_chat",
        category=final_category,
    )
    return JSONResponse({"capsule_id": cap_id, "message": "Edited and saved"}, headers=h)


# ── 后台同步 helper（供 freeze 自动触发 & 定时器共用）────────────
def _background_sync() -> dict:
    """
    无 HTTP 上下文的同步实现，可安全在后台线程调用。
    返回 {"synced": int, "total": int, "errors": list}
    """
    try:
        import httpx
        api_token = get_api_token()
        huper_url = get_huper_url() or "https://huper.org/api"
        master_pw = get_master_password()
        if not master_pw:
            logging.warning("[amber-hunter] auto-sync: master_password not set, skip")
            return {"synced": 0, "total": 0, "errors": ["master_password not set"]}

        unsynced = get_unsynced_capsules()
        if not unsynced:
            return {"synced": 0, "total": 0, "errors": []}

        synced_count = 0
        errors = []

        for capsule in unsynced:
            try:
                salt_b64 = capsule.get("salt")
                if not salt_b64:
                    errors.append({"id": capsule["id"], "error": "no salt"})
                    continue

                salt = base64.b64decode(salt_b64)
                key  = derive_key(master_pw, salt)

                content_enc   = capsule.get("content") or ""
                content_nonce = capsule.get("nonce")   or ""

                memo = (capsule.get("memo") or "").encode("utf-8")
                memo_ct, memo_nonce = encrypt_content(memo, key)
                memo_enc       = base64.b64encode(memo_ct).decode()
                memo_nonce_b64 = base64.b64encode(memo_nonce).decode()

                tags = (capsule.get("tags") or "").encode("utf-8")
                tags_ct, tags_nonce = encrypt_content(tags, key)
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
                }

                with httpx.Client(timeout=15.0, trust_env=False) as client:
                    resp = client.post(
                        f"{huper_url}/capsules",
                        json=payload,
                        headers={"Authorization": f"Bearer {api_token}"}
                    )

                if resp.status_code in (200, 201):
                    mark_synced(capsule["id"])
                    synced_count += 1
                else:
                    errors.append({"id": capsule["id"], "status": resp.status_code})

            except Exception as e:
                errors.append({"id": capsule["id"], "error": str(e)})

        logging.info(f"[amber-hunter] auto-sync done: {synced_count}/{len(unsynced)}")
        return {"synced": synced_count, "total": len(unsynced), "errors": errors}

    except Exception as e:
        logging.error(f"[amber-hunter] _background_sync error: {e}")
        return {"synced": 0, "total": 0, "errors": [str(e)]}


def _spawn_sync_if_enabled():
    """如果 auto_sync 已启用，在守护线程里执行同步（非阻塞）。"""
    if get_config("auto_sync") == "true":
        t = threading.Thread(target=_background_sync, daemon=True)
        t.start()


@app.get("/sync")
def sync_to_cloud(request: Request, authorization: str = Header(None)):
    """
    E2E 加密同步到 huper 云端。
    - memo + tags 圤本地加密后上传，服务端仅存密文
    - content 已圤本地加密，直接传输密文，无需解密
    - 服务端永远看不到任何明文内容
    """
    raw_token = request.query_params.get("token")
    if not raw_token:
        raw_token = authorization
    else:
        raw_token = f"Bearer {raw_token}"
    verify_token(raw_token)

    api_token = get_api_token()
    huper_url = get_huper_url() or "https://huper.org/api"
    master_pw = get_master_password()
    if not master_pw:
        return JSONResponse(
            {"error": "master_password not set", "detail": "请在 dashboard 设置 master_password"},
            status_code=400, headers=add_cors_headers(request)
        )

    import httpx
    unsynced = get_unsynced_capsules()
    if not unsynced:
        return JSONResponse({"synced": 0, "message": "没有需要同步的胶囊"}, headers=add_cors_headers(request))

    synced_count = 0
    errors = []

    for capsule in unsynced:
        try:
            salt_b64 = capsule.get("salt")
            if not salt_b64:
                # 无盐值说明没有加密 content，跳过此胶囊
                errors.append({"id": capsule["id"], "error": "no salt, capsule not encrypted"})
                continue

            salt = base64.b64decode(salt_b64)
            key  = derive_key(master_pw, salt)

            # ── content：已在本地加密，直接传密文 ──────────
            content_enc   = capsule.get("content") or ""
            content_nonce = capsule.get("nonce")   or ""

            # ── memo：本地加密 ───────────────────────────
            memo = (capsule.get("memo") or "").encode("utf-8")
            memo_ct, memo_nonce = encrypt_content(memo, key)
            memo_enc   = base64.b64encode(memo_ct).decode()
            memo_nonce_b64 = base64.b64encode(memo_nonce).decode()

            # ── tags：本地加密 ───────────────────────────
            tags = (capsule.get("tags") or "").encode("utf-8")
            tags_ct, tags_nonce = encrypt_content(tags, key)
            tags_enc   = base64.b64encode(tags_ct).decode()
            tags_nonce_b64 = base64.b64encode(tags_nonce).decode()

            # ── 上传密文到 huper 云端 ────────────────────
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
            }

            with httpx.Client(timeout=15.0, trust_env=False) as client:
                resp = client.post(
                    f"{huper_url}/capsules",
                    json=payload,
                    headers={"Authorization": f"Bearer {api_token}"}
                )

            if resp.status_code in (200, 201):
                mark_synced(capsule["id"])
                synced_count += 1
            else:
                errors.append({"id": capsule["id"], "status": resp.status_code, "body": resp.text[:100]})

        except Exception as e:
            errors.append({"id": capsule["id"], "error": str(e)})

    h = add_cors_headers(request)
    return JSONResponse({
        "synced": synced_count,
        "total":  len(unsynced),
        "errors": errors if errors else None,
    }, headers=h)

# ── 配置读取（Dashboard 用）────────────────────────────
class ConfigIn(BaseModel):
    auto_sync: bool | None = None

@app.get("/config")
def get_config_handler(request: Request, authorization: str = Header(None)):
    """获取配置（auto_sync 等）"""
    raw_token = request.query_params.get("token")
    if not raw_token:
        raw_token = authorization
    else:
        raw_token = f"Bearer {raw_token}"
    verify_token(raw_token)
    auto_sync = get_config("auto_sync")
    return JSONResponse({
        "auto_sync": auto_sync == "true",
    }, headers=add_cors_headers(request))

@app.post("/config")
def set_config_handler(cfg_in: ConfigIn, request: Request, authorization: str = Header(None)):
    """更新配置"""
    raw_token = request.query_params.get("token")
    if not raw_token:
        raw_token = authorization
    else:
        raw_token = f"Bearer {raw_token}"
    verify_token(raw_token)
    if cfg_in.auto_sync is not None:
        set_config("auto_sync", "true" if cfg_in.auto_sync else "false")
    return JSONResponse({"ok": True}, headers=add_cors_headers(request))

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

# ── 服务状态（无需认证）────────────────────────────────
@app.get("/status")
def get_status(request: Request):
    session_key = get_current_session_key()
    master_pw = get_master_password()
    api_token = get_api_token()
    h = add_cors_headers(request)
    return JSONResponse({
        "running": True,
        "version": "1.1.9",
        "platform": get_os(),
        "headless": is_headless(),
        "session_key": session_key,
        "has_master_password": bool(master_pw),
        "has_api_token": bool(api_token),
        "workspace": str(HOME / ".openclaw" / "workspace"),
        "huper_url": get_huper_url(),
    }, headers=h)

@app.get("/")
def root(request: Request):
    h = add_cors_headers(request)
    return JSONResponse({"service": "amber-hunter", "version": "1.1.9", "docs": "/docs"}, headers=h)

# ── 启动 ───────────────────────────────────────────────
def main():
    init_db()
    print("🌙 Amber-Hunter v1.2.0 启动")
    print(f"   Session目录: {HOME / '.openclaw' / 'agents'}")
    print(f"   Workspace:   {HOME / '.openclaw' / 'workspace'}")
    print(f"   数据库:      {HOME / '.amber-hunter' / 'hunter.db'}")
    print(f"   API:        http://localhost:18998/")
    print(f"   CORS:       https://huper.org + localhost")
    print(f"   认证:       本地 API token")
    # 启动 30 分钟定时同步守护线程
    def _periodic_sync_loop():
        while True:
            time.sleep(30 * 60)          # 先休眠再执行，避免启动时立即同步
            _spawn_sync_if_enabled()
    t = threading.Thread(target=_periodic_sync_loop, daemon=True, name="amber-periodic-sync")
    t.start()

    uvicorn.run(app, host="127.0.0.1", port=18998, log_level="warning")

if __name__ == "__main__":
    main()
