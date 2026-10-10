"""Tests for episodic surfacing alignment in WebUI breath debug and config APIs."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import web.config_api as config_api
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
    def __init__(self, query_params: dict | None = None, headers: dict | None = None, json_body: dict | None = None):
        self.query_params = query_params or {}
        self.headers = headers or {}
        self._json_body = json_body or {}

    async def json(self):
        return self._json_body


@pytest.fixture
def fake_mcp():
    mcp = FakeMCP()
    search_api.register(mcp)
    config_api.register(mcp)
    return mcp


@pytest.mark.asyncio
async def test_breath_debug_episodic_mode(fake_mcp, monkeypatch):
    monkeypatch.setattr(search_api.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(search_api.sh, "config", {"surfacing": {"style": "episodic", "breath_max_tokens": 12000}})

    mock_episodic_res = {
        "mode": "episodic",
        "style": "episodic",
        "raw_text": "=== 核心准则 ===\n测试准则",
        "budget": {"max_tokens": 12000, "used_tokens": 150, "remaining_tokens": 11850},
        "sections": [
            {"id": "core", "title": "核心准则", "tokens": 150, "omitted": 0, "items": [{"id": "pinned_1", "name": "核心", "tokens": 150}]},
            {"id": "mind", "title": "挂在心上", "tokens": 0, "omitted": 0, "total_clusters": 1, "items": []},
        ],
    }

    from tools.breath import episodic as ep_mod
    monkeypatch.setattr(ep_mod, "surface_episodic", AsyncMock(return_value=mock_episodic_res))

    req = MockRequest(query_params={"mode": "episodic"})
    handler = fake_mcp.routes[("GET", "/api/breath-debug")]
    response = await handler(req)
    assert response.status_code == 200

    data = json.loads(response.body)
    assert data["mode"] == "episodic"
    assert data["budget"]["max_tokens"] == 12000
    assert len(data["sections"]) == 2
    assert data["sections"][0]["id"] == "core"
    assert data["raw_text"] == "=== 核心准则 ===\n测试准则"


@pytest.mark.asyncio
async def test_breath_debug_classic_mode(fake_mcp, monkeypatch):
    monkeypatch.setattr(search_api.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(search_api.sh, "config", {"surfacing": {"style": "classic"}})

    mock_classic_res = {
        "mode": "classic",
        "style": "classic",
        "raw_text": "经典浮现内容",
        "budget": {"max_tokens": 20000, "used_tokens": 300, "remaining_tokens": 19700},
        "sections": [
            {"id": "core", "title": "核心准则", "tokens": 100, "omitted": 0, "items": []},
            {"id": "scored", "title": "加权浮现", "tokens": 200, "omitted": 0, "items": []},
        ],
    }

    from tools.breath import surface as sf_mod
    monkeypatch.setattr(sf_mod, "_surface_classic", AsyncMock(return_value=mock_classic_res))

    req = MockRequest(query_params={"mode": "classic"})
    handler = fake_mcp.routes[("GET", "/api/breath-debug")]
    response = await handler(req)
    assert response.status_code == 200

    data = json.loads(response.body)
    assert data["mode"] == "classic"
    assert data["budget"]["remaining_tokens"] == 19700
    assert len(data["sections"]) == 2


@pytest.mark.asyncio
async def test_breath_debug_search_mode(fake_mcp, monkeypatch):
    monkeypatch.setattr(search_api.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(search_api.sh, "config", {"surfacing": {}})

    fake_bucket = {
        "id": "b1",
        "content": "测试内容",
        "metadata": {
            "name": "测试桶",
            "importance": 5,
            "created": "2026-10-01T00:00:00",
            "last_active": "2026-10-01T00:00:00",
        },
    }

    fake_mgr = MagicMock()
    fake_mgr.list_all = AsyncMock(return_value=[fake_bucket])
    fake_mgr.w_topic = 4.0
    fake_mgr.w_emotion = 2.0
    fake_mgr.w_time = 2.5
    fake_mgr.w_importance = 1.0
    fake_mgr.content_weight = 0.5
    fake_mgr.fuzzy_threshold = 50.0
    monkeypatch.setattr(search_api.sh, "bucket_mgr", fake_mgr)

    req = MockRequest(query_params={"mode": "search", "q": "测试"})
    handler = fake_mcp.routes[("GET", "/api/breath-debug")]
    response = await handler(req)
    assert response.status_code == 200

    data = json.loads(response.body)
    assert data["mode"] == "search"
    assert "weights" in data
    assert data["weights"]["topic"] == 4.0
    assert len(data["results"]) == 1
    assert data["results"][0]["id"] == "b1"


@pytest.mark.asyncio
async def test_config_surfacing_style_persistence(fake_mcp, monkeypatch):
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda request: None)
    persisted = {"surfacing": {"style": "classic", "shadow_episodic": False}}

    def fake_read():
        return dict(persisted)

    def fake_atomic_update(mutator):
        mutator(persisted)
        return dict(persisted)

    monkeypatch.setattr(config_api, "read_config_yaml", fake_read)
    monkeypatch.setattr(config_api, "atomic_update_config_yaml", fake_atomic_update)
    monkeypatch.setattr(config_api.sh, "config", {"surfacing": {"style": "classic", "shadow_episodic": False}})
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: False)

    # 1. GET /api/config
    get_handler = fake_mcp.routes[("GET", "/api/config")]
    get_req = MockRequest()
    res = await get_handler(get_req)
    assert res.status_code == 200
    cfg = json.loads(res.body)
    assert cfg["surfacing"]["style"] == "classic"
    assert cfg["surfacing"]["shadow_episodic"] is False

    # 2. POST /api/config with updated style
    post_handler = fake_mcp.routes[("POST", "/api/config")]
    post_req = MockRequest(
        json_body={
            "persist": True,
            "surfacing": {
                "style": "episodic",
                "shadow_episodic": True,
                "breath_max_results": 25,
                "breath_max_tokens": 15000,
            }
        }
    )
    res_post = await post_handler(post_req)
    assert res_post.status_code == 200

    assert persisted["surfacing"]["style"] == "episodic"
    assert persisted["surfacing"]["shadow_episodic"] is True
    assert config_api.sh.config["surfacing"]["style"] == "episodic"
    assert config_api.sh.config["surfacing"]["shadow_episodic"] is True
