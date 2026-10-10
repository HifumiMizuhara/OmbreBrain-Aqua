"""Tests for /api/network optimization and bounding against huge graphs."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import web.search as search_api


class FakeMCP:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(fn):
            for method in methods:
                self.routes[(method, path)] = fn
            return fn

        return decorator


class MockRequest:
    def __init__(self, query_params: dict | None = None, headers: dict | None = None):
        self.query_params = query_params or {}
        self.headers = headers or {}


@pytest.fixture
def fake_mcp():
    mcp = FakeMCP()
    search_api.register(mcp)
    return mcp


@pytest.mark.asyncio
async def test_network_concept_mode_bounded(fake_mcp, monkeypatch):
    monkeypatch.setattr(search_api.sh, "_require_auth", lambda request: None)

    # 构造包含多个 wikilink 和 tag 的测试桶
    fake_buckets = [
        {
            "id": f"b{i}",
            "content": f"关于 [[概念A]] 和 [[概念B]] 以及 [[概念C_{i}]] 的笔记",
            "metadata": {"name": f"桶{i}", "tags": ["tag1", f"tag_{i}"]},
        }
        for i in range(20)
    ]

    fake_mgr = MagicMock()
    fake_mgr.list_all = AsyncMock(return_value=fake_buckets)
    monkeypatch.setattr(search_api.sh, "bucket_mgr", fake_mgr)

    handler = fake_mcp.routes[("GET", "/api/network")]

    # 1. 默认请求
    res = await handler(MockRequest(query_params={"mode": "concept"}))
    assert res.status_code == 200
    data = json.loads(res.body)
    assert data["mode"] == "concept"
    assert "total_nodes" in data
    assert "total_edges" in data
    assert data["total_nodes"] >= 20
    assert len(data["nodes"]) <= 600

    # 2. 指定 limit=5
    res_limit = await handler(MockRequest(query_params={"mode": "concept", "limit": "5"}))
    assert res_limit.status_code == 200
    data_limit = json.loads(res_limit.body)
    assert len(data_limit["nodes"]) == 5
    assert data_limit["total_nodes"] == data["total_nodes"]
    # 验证边的两端都在返回的 5 个节点中
    node_ids = {n["id"] for n in data_limit["nodes"]}
    for edge in data_limit["edges"]:
        assert edge["source"] in node_ids
        assert edge["target"] in node_ids

    # 3. 指定 min_weight 过滤
    res_mw = await handler(MockRequest(query_params={"mode": "concept", "min_weight": "5"}))
    assert res_mw.status_code == 200
    data_mw = json.loads(res_mw.body)
    for edge in data_mw["edges"]:
        assert edge["weight"] >= 5


@pytest.mark.asyncio
async def test_network_embedding_mode_bounded(fake_mcp, monkeypatch):
    monkeypatch.setattr(search_api.sh, "_require_auth", lambda request: None)

    fake_buckets = [
        {
            "id": f"b{i}",
            "metadata": {"name": f"桶{i}", "importance": i % 10},
        }
        for i in range(150)
    ]

    fake_mgr = MagicMock()
    fake_mgr.list_all = AsyncMock(return_value=fake_buckets)
    monkeypatch.setattr(search_api.sh, "bucket_mgr", fake_mgr)

    fake_engine = MagicMock()
    fake_engine.enabled = False
    monkeypatch.setattr(search_api.sh, "embedding_engine", fake_engine)

    handler = fake_mcp.routes[("GET", "/api/network")]
    res = await handler(MockRequest(query_params={"mode": "embedding"}))
    assert res.status_code == 200
    data = json.loads(res.body)
    assert data["mode"] == "embedding"
    # embedding 模式针对海量桶做了 120 节点上限保护
    assert len(data["nodes"]) <= 120
    assert data["total_nodes"] <= 120
