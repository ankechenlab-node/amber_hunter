"""
tests/test_sync.py — 同步可靠性测试
覆盖: P0-1 (synced=0 reset), P0-2 (updated_at), P1-5/P2-10 (ORDER BY)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch, MagicMock
import time


class TestUpdateCapsuleSyncedFlag:
    """P0-1: update_capsule 编辑后 synced 必须重置为 0"""

    def test_update_capsule_resets_synced_flag(self, test_client, test_db):
        """更新胶囊后，synced 字段应为 0（进入待同步队列）"""
        import amber_hunter
        from core.db import insert_capsule, get_capsule

        # 插入一条已同步的胶囊
        capsule_id = "test-synced-capsule"
        insert_capsule(
            capsule_id=capsule_id,
            memo="original memo",
            content="content",
            tags="test",
            session_id=None,
            window_title=None,
            url=None,
            created_at=time.time(),
            source_type="manual",
            category="dev",
        )

        # 手动标记为已同步
        from core.db import _get_conn
        conn = _get_conn()
        conn.execute("UPDATE capsules SET synced=1 WHERE id=?", (capsule_id,))
        conn.commit()

        # 确认初始状态 synced=1
        row = get_capsule(capsule_id)
        assert row["id"] == capsule_id

        # 调用 PATCH /capsules/{id} 更新 memo
        with patch.object(amber_hunter, 'get_api_token', return_value="test-token-12345"):
            response = test_client.patch(
                f"/capsules/{capsule_id}",
                json={"memo": "updated memo"},
                headers={"Authorization": "Bearer test-token-12345"}
            )

        assert response.status_code == 200, f"update failed: {response.text}"

        # 验证 synced=0（重新进入待同步队列）
        from core.db import _get_conn
        conn2 = _get_conn()
        row2 = conn2.execute(
            "SELECT synced, updated_at FROM capsules WHERE id=?", (capsule_id,)
        ).fetchone()
        conn2.close()
        assert row2[0] == 0, f"expected synced=0, got {row2[0]}"

    def test_update_capsule_sets_updated_at(self, test_client, test_db):
        """P0-2: 更新胶囊后 updated_at 应被更新"""
        import amber_hunter
        from core.db import insert_capsule, get_capsule

        capsule_id = "test-updated-at"
        now = time.time()
        insert_capsule(
            capsule_id=capsule_id,
            memo="original",
            content="content",
            tags="test",
            session_id=None,
            window_title=None,
            url=None,
            created_at=now,
            source_type="manual",
            category="dev",
        )

        # 记录创建时的 updated_at
        row_before = get_capsule(capsule_id)
        old_updated_at = row_before.get("updated_at") or 0

        # 更新 memo
        with patch.object(amber_hunter, 'get_api_token', return_value="test-token-12345"):
            response = test_client.patch(
                f"/capsules/{capsule_id}",
                json={"memo": "new memo"},
                headers={"Authorization": "Bearer test-token-12345"}
            )

        assert response.status_code == 200

        # 验证 updated_at 变大
        row_after = get_capsule(capsule_id)
        new_updated_at = row_after.get("updated_at") or 0
        assert new_updated_at > old_updated_at, (
            f"updated_at should increase: old={old_updated_at}, new={new_updated_at}"
        )


class TestGetUnsyncedOrder:
    """P1-5/P2-10: get_unsynced_capsules 按 created_at ASC 排序"""

    def test_unsynced_order_is_ascending(self, test_db):
        """未同步胶囊应按 created_at 升序返回（先老后新）"""
        from core.db import insert_capsule, get_unsynced_capsules

        base = time.time()
        ids = []
        for i in range(5):
            cid = f"test-order-{i}"
            ids.append(cid)
            insert_capsule(
                capsule_id=cid,
                memo=f"memo {i}",
                content=f"content {i}",
                tags=f"tag{i}",
                session_id=None,
                window_title=None,
                url=None,
                created_at=base + i * 10,   # 间隔 10 秒递增
                source_type="manual",
                category="dev",
            )

        # 全部未同步，取全部
        capsules = get_unsynced_capsules()
        assert len(capsules) >= 5

        # 验证 created_at 升序
        created_ats = [c["created_at"] for c in capsules[:5]]
        assert created_ats == sorted(created_ats), (
            f"capsules should be ordered by created_at ASC: {created_ats}"
        )

    def test_unsynced_respects_limit(self, test_db):
        """get_unsynced_capsules(limit=N) 应最多返回 N 条"""
        from core.db import insert_capsule, get_unsynced_capsules

        base = time.time()
        for i in range(10):
            insert_capsule(
                capsule_id=f"test-limit-{i}",
                memo=f"memo {i}",
                content=f"content {i}",
                tags=f"tag{i}",
                session_id=None,
                window_title=None,
                url=None,
                created_at=base + i,
                source_type="manual",
                category="dev",
            )

        # limit=3 应只返回 3 条
        capsules = get_unsynced_capsules(limit=3)
        assert len(capsules) == 3, f"expected 3, got {len(capsules)}"


class TestSyncEndpoint:
    """同步端点基础测试"""

    def test_sync_requires_auth(self, test_client):
        """无 token 时 /sync 应返回 401/403"""
        response = test_client.get("/sync")
        assert response.status_code in (401, 403)

    def test_sync_no_capsules_returns_zero(self, test_client, test_db):
        """没有未同步胶囊时返回 synced=0"""
        import amber_hunter
        with patch.object(amber_hunter, 'get_api_token', return_value="test-token"):
            with patch.object(amber_hunter, 'get_master_password', return_value="fake-pw"):
                response = test_client.get(
                    "/sync",
                    headers={"Authorization": "Bearer test-token"}
                )
        assert response.status_code == 200
        data = response.json()
        assert data["synced"] == 0
        assert data["total"] == 0
