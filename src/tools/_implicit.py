"""隐式强化的配置与触发入口（3.8.0）。

边界：「展示」不强化；「主动去找」和「引用」轻微强化。
无参 breath() 浮现、mode="automatic" 的自动召回、随机杂念、archive 命中都不走这里。
节流（每桶每天一次）在 BucketManager.soft_reinforce 里。
"""

from . import _runtime as rt
from utils import parse_bool

DEFAULTS = {
    "enabled": True,
    "search_hit": 0.3,
    "search_hit_top_n": 3,
    "write_reference": 0.5,
}


def implicit_config() -> dict:
    raw = ((getattr(rt, "config", None) or {}).get("reinforce") or {}).get("implicit") or {}
    cfg = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return cfg
    cfg["enabled"] = parse_bool(raw.get("enabled", True), default=True)
    for key in ("search_hit", "write_reference"):
        try:
            cfg[key] = float(raw.get(key, DEFAULTS[key]))
        except (TypeError, ValueError):
            pass
    try:
        cfg["search_hit_top_n"] = int(raw.get("search_hit_top_n", DEFAULTS["search_hit_top_n"]))
    except (TypeError, ValueError):
        pass
    return cfg


async def reinforce_search_hits(bucket_ids: list[str]) -> int:
    """主动检索命中：只计前 top_n 条真实命中。调用方保证 mode=manual 且非 archive。"""
    cfg = implicit_config()
    if not cfg["enabled"] or cfg["search_hit"] <= 0 or cfg["search_hit_top_n"] <= 0:
        return 0
    done = 0
    for bid in bucket_ids[: cfg["search_hit_top_n"]]:
        try:
            if await rt.bucket_mgr.soft_reinforce(bid, cfg["search_hit"], "search_hit"):
                done += 1
        except Exception as exc:  # noqa: BLE001 - 强化失败不能影响检索结果
            rt.logger.warning(f"implicit reinforce failed {bid}: {exc}")
    return done


async def reinforce_reference(bucket_id: str) -> bool:
    """写入时显式引用旧桶（hold(feel=True, source_bucket=...)）。"""
    cfg = implicit_config()
    if not cfg["enabled"] or cfg["write_reference"] <= 0 or not bucket_id:
        return False
    try:
        return bool(await rt.bucket_mgr.soft_reinforce(bucket_id, cfg["write_reference"], "write_reference"))
    except Exception as exc:  # noqa: BLE001
        rt.logger.warning(f"implicit reinforce failed {bucket_id}: {exc}")
        return False
