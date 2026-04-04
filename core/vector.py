"""
core/vector.py — LanceDB 向量存储与检索（v1.2.23 P0-1）
向量胶囊写入、top_k 语义检索、删除
"""
from __future__ import annotations

import sys, shutil
from pathlib import Path

HOME = Path.home()
VECTOR_DIR = HOME / ".amber-hunter" / "lance_db"
_EMBED_MODEL = None  # 模块级缓存


def _get_embed_model():
    """获取 MiniLM 向量模型（lazy load，线程安全）"""
    global _EMBED_MODEL
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL
    from sentence_transformers import SentenceTransformer
    _EMBED_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return _EMBED_MODEL


def init_vector_db() -> "lancedb.db.LanceDB":
    """初始化 LanceDB 目录和 capsule_vectors 表。"""
    import lancedb
    import pyarrow as pa

    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(VECTOR_DIR))

    try:
        if "capsule_vectors" not in db.list_tables():
            schema = pa.schema([
                ("capsule_id", pa.string()),
                ("text", pa.string()),
                ("vector", pa.list_(pa.float32(), 384)),  # FixedSizeList(384)
                ("created_at", pa.float64()),
            ])
            db.create_table("capsule_vectors", schema=schema)
    except Exception:
        pass  # 表可能已被其他进程创建

    return db


def index_capsule(capsule_id: str, memo: str, created_at: float) -> bool:
    """
    计算 memo 的 384 维 embedding，存入 LanceDB。
    memo 为解密后原文（memo 字段不加密，content 字段才加密）。
    """
    try:
        db = init_vector_db()
        model = _get_embed_model()
        vec = model.encode(memo[:512])  # 截断避免超长
        tbl = db.open_table("capsule_vectors")
        tbl.add([{
            "capsule_id": capsule_id,
            "text": memo[:512],
            "vector": vec.tolist(),
            "created_at": created_at,
        }])
        return True
    except Exception as e:
        import sys
        print(f"[vector] index_capsule failed: {e}", file=sys.stderr)
        return False


def search_vectors(query: str, limit: int = 20) -> list[dict]:
    """
    LanceDB top_k 语义检索。
    返回 [{capsule_id, lance_score, text}, ...]
    lance_score = 1 - normalized_distance（越大越相关，1=完全匹配）
    """
    try:
        db = init_vector_db()
        model = _get_embed_model()
        q_vec = model.encode(query[:512])
        tbl = db.open_table("capsule_vectors")
        rs = tbl.search(q_vec.tolist(), vector_column_name="vector") \
              .limit(limit) \
              .to_list()
        return [
            {
                "capsule_id": r["capsule_id"],
                "lance_score": max(0.0, 1.0 - r["_distance"]),
                "text": r.get("text", ""),
            }
            for r in rs
        ]
    except Exception as e:
        import sys
        print(f"[vector] search_vectors failed: {e}", file=sys.stderr)
        return []


def delete_vector(capsule_id: str) -> bool:
    """删除指定 capsule_id 的向量（胶囊删除时调用）。"""
    try:
        db = init_vector_db()
        tbl = db.open_table("capsule_vectors")
        tbl.delete(f"capsule_id = '{capsule_id}'")
        return True
    except Exception as e:
        import sys
        print(f"[vector] delete_vector failed: {e}", file=sys.stderr)
        return False


def get_vector_stats() -> dict:
    """返回向量库统计信息（用于调试和 /status）。"""
    try:
        db = init_vector_db()
        if "capsule_vectors" not in db.list_tables():
            return {"count": 0, "vector_db_size_mb": 0}
        tbl = db.open_table("capsule_vectors")
        count = tbl.count_rows()
        size_mb = sum(
            (VECTOR_DIR / f).stat().st_size
            for f in VECTOR_DIR.iterdir()
            if f.is_file()
        ) / (1024 * 1024)
        return {"count": count, "vector_db_size_mb": round(size_mb, 2)}
    except Exception:
        return {"count": 0, "vector_db_size_mb": 0}
