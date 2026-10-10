import pytest
from bucket_manager import BucketManager


def _make_bucket(bucket_id: str, content: str, name: str = "", importance: int = 6):
    return {
        "id": bucket_id,
        "content": content,
        "metadata": {
            "name": name or bucket_id,
            "title": name or bucket_id,
            "type": "dynamic",
            "importance": importance,
            "domain": ["生活"],
            "tags": ["饮食"],
            "created": "2026-10-10T12:00:00",
            "last_active": "2026-10-10T12:00:00",
            "activation_count": 1,
            "valence": 0.5,
            "arousal": 0.3,
        },
    }


@pytest.mark.asyncio
async def test_multi_token_space_query_recalls_single_matching_token():
    """验证多词查询（如“拿铁 咖啡”）能够成功召回只包含其中一个分词的桶（如只有“咖啡”而无“拿铁”）。"""
    cafe_bucket = _make_bucket(
        "b_cafe",
        content="常去的咖啡馆是街角的慢岛咖啡，手冲和深烘豆子很香，适合下午看书。",
        name="咖啡馆常客",
        importance=6,
    )
    latte_bucket = _make_bucket(
        "b_latte",
        content="下午尝试了新品生椰拿铁，风味非常浓郁，微糖口感刚刚好。",
        name="饮品品尝记录",
        importance=6,
    )
    unrelated_bucket = _make_bucket(
        "b_unrelated",
        content="今天学习了量子力学基本概念，非常有意思。",
        name="学习笔记",
        importance=5,
    )

    bm = BucketManager.__new__(BucketManager)
    bm.max_results = 5
    bm.fuzzy_threshold = 50
    bm.vector_recall_threshold = 0.55
    bm.w_topic = 4.0
    bm.w_emotion = 2.0
    bm.w_time = 1.5
    bm.w_importance = 1.0
    bm.w_touch = 1.0
    bm.w_semantic = 2.5
    bm.w_bm25 = 1.5
    bm.content_weight = 1.0
    bm._bm25 = None
    bm._bm25_dirty = False
    bm._bm25_rebuilding = False
    bm.embedding_engine = None

    async def fake_list_all(include_archive=False):
        return [cafe_bucket, latte_bucket, unrelated_bucket]

    bm.list_all = fake_list_all

    results = await bm.search("拿铁 咖啡")
    result_ids = [r["id"] for r in results]

    # 包含“咖啡”或“拿铁”的两个相关桶都必须被成功召回
    assert "b_cafe" in result_ids, "只包含'咖啡'的桶必须被'拿铁 咖啡'成功召回"
    assert "b_latte" in result_ids, "包含'拿铁'的桶必须被成功召回"
    assert "b_unrelated" not in result_ids, "无关桶不应被召回"


@pytest.mark.asyncio
async def test_multi_token_literal_hit_recalls_with_sub_threshold_similarity():
    """验证多词查询只要字面命中有效分词，即使向量相似度未达纯语义门槛，也能通过字面放行成功召回。"""
    bucket = _make_bucket(
        "b_tea",
        content="平时休息时喜欢在安静的茶室喝白茶和乌龙茶。",
        name="茶饮习惯",
        importance=5,
    )

    bm = BucketManager.__new__(BucketManager)
    bm.max_results = 5
    bm.fuzzy_threshold = 50
    bm.vector_recall_threshold = 0.55
    bm.w_topic = 4.0
    bm.w_emotion = 2.0
    bm.w_time = 1.5
    bm.w_importance = 1.0
    bm.w_touch = 1.0
    bm.w_semantic = 2.5
    bm.w_bm25 = 1.5
    bm.content_weight = 1.0
    bm._bm25 = None
    bm._bm25_dirty = False
    bm._bm25_rebuilding = False
    bm.embedding_engine = None

    async def fake_list_all(include_archive=False):
        return [bucket]

    bm.list_all = fake_list_all

    # 向量相似度仅为 0.40（低于纯语义门槛 0.55），但查询包含“乌龙茶”（分词命中）
    vector_scores = {"b_tea": 0.40}
    results = await bm.search("咖啡 乌龙茶", vector_scores=vector_scores)
    result_ids = [r["id"] for r in results]

    assert "b_tea" in result_ids, "多词查询中命中分词'乌龙茶'的桶应通过字面放行进入结果池"


@pytest.mark.asyncio
async def test_multi_token_search_recalls_when_only_one_token_present():
    """验证多词查询（如'拿铁 咖啡'）在只有部分词（如'咖啡'）出现时，能够通过字面拆分成功召回。"""
    bucket = _make_bucket(
        "b_coffee",
        content="慢岛咖啡馆的手冲深度烘焙",
        name="常去地点",
        importance=5,
    )
    bm = BucketManager.__new__(BucketManager)
    bm.max_results = 5
    bm.fuzzy_threshold = 50
    bm.vector_recall_threshold = 0.55
    bm.w_topic = 4.0
    bm.w_emotion = 2.0
    bm.w_time = 1.5
    bm.w_importance = 1.0
    bm.w_touch = 1.0
    bm.w_semantic = 2.5
    bm.w_bm25 = 1.5
    bm.content_weight = 1.0
    bm._bm25 = None
    bm._bm25_dirty = False
    bm._bm25_rebuilding = False
    bm.embedding_engine = None

    async def fake_list_all(include_archive=False):
        return [bucket]

    bm.list_all = fake_list_all

    results = await bm.search("拿铁 咖啡")
    result_ids = [r["id"] for r in results]
    assert "b_coffee" in result_ids, "仅包含'咖啡'的桶在多词查询'拿铁 咖啡'下应被成功召回"
