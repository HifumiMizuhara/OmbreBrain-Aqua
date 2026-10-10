import pytest
from bucket_manager import BucketManager
from ombrebrain.retrieval.bucket_scoring import calc_topic_score


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
async def test_top_k_vector_recall_allows_sub_threshold_similarity():
    """验证即使余弦相似度未达到 0.55，只要处于 Top-3 且 >= 0.40，也能获得语义保底放行。"""
    sub_threshold_bucket = _make_bucket(
        "b_sub",
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
        return [sub_threshold_bucket]

    bm.list_all = fake_list_all

    # 向量相似度为 0.48（低于 0.55）
    vector_scores = {"b_sub": 0.48}
    results = await bm.search("饮品偏好", vector_scores=vector_scores)
    result_ids = [r["id"] for r in results]

    assert "b_sub" in result_ids, "排名前 3 且相似度达到 0.48 的桶应通过语义保底进入结果池"


def test_token_aware_topic_scoring_does_not_penalize_multi_token_partial_matches():
    """验证多词查询在单字段匹配时不会因为空格和未出现的子词被严重惩罚。"""
    bucket = _make_bucket(
        "b1",
        content="慢岛咖啡馆的手冲深度烘焙",
        name="咖啡常去地点",
    )
    score_multi = calc_topic_score("拿铁 咖啡", bucket)

    # 优化前仅有 0.18 左右（单词被未命中词严重稀释），优化后即便只命中“咖啡”，得分亦有显著提升（>= 0.35）
    assert score_multi >= 0.35, f"多词查询中的'咖啡'得分应有保障，实际为 {score_multi}"
