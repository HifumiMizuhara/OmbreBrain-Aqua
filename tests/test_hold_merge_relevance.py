import pytest
from unittest.mock import MagicMock
import tools._runtime as rt
from tools.hold import dispatch as hold


@pytest.mark.asyncio
async def test_merge_preserves_existing_bucket_title_and_reports_correctly(
    bucket_mgr, monkeypatch
):
    class Dehydrator:
        async def analyze(self, content):
            if "拿铁" not in content:
                return {
                    "domain": ["日常"],
                    "valence": 0.5,
                    "arousal": 0.3,
                    "tags": ["原标签"],
                    "suggested_name": "原始事件标题",
                }
            return {
                "domain": ["日常"],
                "valence": 0.5,
                "arousal": 0.3,
                "tags": ["新标签"],
                "suggested_name": "完全不同的后续标题",
            }

        async def judge_same_event(self, old_memory, new_content):
            return {"same_event": True, "confidence": 0.95, "reason": "同事件测试"}

        def invalidate_cache(self, _content):
            return None

    class Decay:
        async def ensure_started(self):
            return None

    monkeypatch.setattr(rt, "config", {"limits": {}, "merge_threshold": 30})
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "dehydrator", Dehydrator())
    monkeypatch.setattr(rt, "decay_engine", Decay())
    monkeypatch.setattr(rt, "logger", MagicMock())
    monkeypatch.setattr(rt, "fire_webhook", None)
    monkeypatch.setattr(rt, "mark_op", None)

    # 1. 写入初始桶
    res1 = await hold(content="初始事件记录：今天去买了咖啡。", title="原始事件标题")
    assert res1.startswith("新建→")
    bucket_id = res1.split("→", 1)[1].split()[0]

    b1 = await bucket_mgr.get(bucket_id)
    assert b1["metadata"]["title"] == "原始事件标题"

    real_search = bucket_mgr.search
    async def mock_search(query, *args, **kwargs):
        if "拿铁" in query:
            b = await bucket_mgr.get(bucket_id)
            assert b is not None
            b["score"] = 90.0
            return [b]
        return await real_search(query, *args, **kwargs)

    monkeypatch.setattr(bucket_mgr, "search", mock_search)

    # 2. 模拟合并写入新内容（高相似内容触发合并）
    res2 = await hold(
        content="初始事件记录：今天去买了咖啡。后来喝到了燕麦拿铁。",
        title="完全不同的后续标题",
    )
    assert res2.startswith("合并→")
    # 验证返回说明包含的是原始标题，而不是被覆盖的新标题！
    assert "已并入《原始事件标题》" in res2
    assert "未新建" in res2
    assert "完全不同的后续标题" not in res2

    # 3. 验证存储中的元数据，原始标题和名称没有被恶意篡改覆盖
    b2 = await bucket_mgr.get(bucket_id)
    assert b2["metadata"]["title"] == "原始事件标题"
    assert "原始事件标题" in b2["metadata"]["name"]


@pytest.mark.asyncio
async def test_search_relevance_only_excludes_temporal_and_touch_boost(bucket_mgr):
    # 创建一个早先创建、激活次数为0的语义相关桶
    id_relevant = await bucket_mgr.create(
        content="机器学习模型训练",
        tags=["AI", "ML"],
        importance=5,
        domain=["技术"],
        title="机器学习",
    )

    # 创建一个高激活、但内容完全不相关的桶
    id_recent = await bucket_mgr.create(
        content="西红柿炒鸡蛋做饭食谱",
        tags=["生活"],
        importance=10,
        domain=["生活"],
        title="做饭食谱",
    )
    # 人工拉高激活次数
    await bucket_mgr.update(id_recent, activation_count=20, bump_active=True)

    # 普通搜索：包含时效、情绪与触碰加权
    normal_results = await bucket_mgr.search("机器学习模型训练", limit=5)
    assert any(b["id"] == id_relevant for b in normal_results)

    # 纯相关性搜索（用于合并）：排除 time/touch/importance 虚高加分
    rel_results = await bucket_mgr.search(
        "机器学习模型训练", limit=5, relevance_only=True
    )
    rel_dict = {b["id"]: b["score"] for b in rel_results}
    assert id_relevant in rel_dict
    recent_score = rel_dict.get(id_recent, 0)
    assert rel_dict[id_relevant] > recent_score


@pytest.mark.asyncio
async def test_anti_sponge_protection_prevents_infinite_merges(
    bucket_mgr, monkeypatch
):
    class Dehydrator:
        async def analyze(self, content):
            return {
                "domain": ["测试"],
                "valence": 0.5,
                "arousal": 0.3,
                "tags": [],
                "suggested_name": "测试桶",
            }

        async def judge_same_event(self, old_memory, new_content):
            # 即使 LLM 每次都判定为同一事件
            return {"same_event": True, "confidence": 0.99, "reason": "强制同事件"}

        def invalidate_cache(self, _content):
            return None

    class Decay:
        async def ensure_started(self):
            return None

    monkeypatch.setattr(rt, "config", {"limits": {}, "merge_threshold": 10})
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "dehydrator", Dehydrator())
    monkeypatch.setattr(rt, "decay_engine", Decay())
    monkeypatch.setattr(rt, "logger", MagicMock())
    monkeypatch.setattr(rt, "fire_webhook", None)
    monkeypatch.setattr(rt, "mark_op", None)

    # 首次新建
    res1 = await hold(content="事件片段 0")
    assert res1.startswith("新建→")

    # 模拟多次连续合并
    merged_count = 0
    created_new = False
    for i in range(1, 10):
        # 保持前缀一致以保证通过 threshold
        res = await hold(content=f"事件片段 0 补充 {i}")
        if res.startswith("合并→"):
            merged_count += 1
        elif res.startswith("新建→"):
            created_new = True
            break

    # 防黑洞机制应该在达到上限后停止无休止合并，转为新建！
    assert created_new is True
    assert merged_count <= 4
