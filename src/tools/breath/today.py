"""
========================================
tools/breath/today.py — 「今天」通道（3.7.0）
========================================

无参 breath() 里，最近 window_hours 小时内写下（created 或 last_merged_at）的
动态桶必然在场，不论 resolved、不论权重。按时间正序，像日记一样从早到晚。

连续写入折叠：相邻两条写入间隔 <= burst_gap_minutes，且连续条数 >= burst_min_size，
整段折叠成一行，只列模型自己写的标题，ID 全部保留。折叠只看写入时间是否相邻，
不判断内容是否相关（不做认知层）。

不做什么：不调用 touch()、不强化、不改任何桶。
========================================
"""

from datetime import datetime, timedelta

from utils import count_tokens_approx, parse_iso_datetime
from ._verbatim import render_stored_bucket

DEFAULTS = {
    "enabled": True,
    "window_hours": 24,
    "max_items": 8,
    "max_tokens": 4000,
    "burst_gap_minutes": 30,
    "burst_min_size": 5,
}


def today_config(surfacing_cfg: dict) -> dict:
    raw = surfacing_cfg.get("today") or {}
    cfg = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return cfg
    for key, default in DEFAULTS.items():
        if key not in raw:
            continue
        if key == "enabled":
            from utils import parse_bool
            cfg[key] = parse_bool(raw[key], default=default)
        else:
            try:
                cfg[key] = int(raw[key])
            except (TypeError, ValueError):
                pass
    return cfg


def written_at(bucket: dict) -> datetime | None:
    """桶最近一次「写入」的时间：created 与 last_merged_at 取较晚者。"""
    meta = bucket.get("metadata") or {}
    stamps = []
    for key in ("created", "last_merged_at"):
        raw = str(meta.get(key) or "").strip()
        if not raw:
            continue
        try:
            stamps.append(parse_iso_datetime(raw))
        except (TypeError, ValueError):
            continue
    return max(stamps) if stamps else None


def select_today(buckets: list[dict], cfg: dict, now: datetime | None = None) -> list[tuple[datetime, dict]]:
    """窗口内的桶，按写入时间正序。调用方负责传入已过滤（类型/策略/tags）的候选。"""
    now = now or datetime.now()
    cutoff = now - timedelta(hours=cfg["window_hours"])
    out = []
    for b in buckets:
        t = written_at(b)
        if t is not None and cutoff <= t:
            out.append((t, b))
    out.sort(key=lambda x: (x[0], x[1]["id"]))
    return out


def fold_bursts(items: list[tuple[datetime, dict]], cfg: dict) -> list[list[tuple[datetime, dict]]]:
    """按写入时间相邻切段；每段是一个列表，长度 >= burst_min_size 的段会被折叠。"""
    groups: list[list[tuple[datetime, dict]]] = []
    gap = timedelta(minutes=cfg["burst_gap_minutes"])
    for item in items:
        if groups and item[0] - groups[-1][-1][0] <= gap:
            groups[-1].append(item)
        else:
            groups.append([item])
    return groups


def _title(bucket: dict) -> str:
    meta = bucket.get("metadata") or {}
    return str(meta.get("title") or meta.get("name") or bucket["id"]).strip()


def _render_burst(group: list[tuple[datetime, dict]]) -> str:
    start, end = group[0][0], group[-1][0]
    titles = "".join(f"《{_title(b)}》" for _, b in group[:3])
    ids = ",".join(b["id"] for _, b in group)
    return (
        f"🕐 [今天] {start:%H:%M}–{end:%H:%M} 写下了 {len(group)} 条：{titles}"
        f"{'…' if len(group) > 3 else ''}  [ids: {ids}]"
    )


def plan_today(buckets: list[dict], cfg: dict, now: datetime | None = None):
    """选取+折叠+max_items 截断。返回 (entries, omitted)，entries 为 [("burst"|"one", group)]。

    渲染方式由调用方决定：工具路径逐字渲染，/breath-hook 走脱水摘要。
    """
    groups = fold_bursts(select_today(buckets, cfg, now), cfg)
    entries = []
    for g in groups:
        if len(g) >= cfg["burst_min_size"]:
            entries.append(("burst", g))
        else:
            entries.extend(("one", [it]) for it in g)
    omitted = max(0, len(entries) - cfg["max_items"])
    if omitted:
        entries = entries[-cfg["max_items"]:]
    return entries, omitted


def render_burst(group: list[tuple[datetime, dict]]) -> str:
    return _render_burst(group)


def render_today(
    buckets: list[dict],
    cfg: dict,
    token_budget: int,
    footprint,
    now: datetime | None = None,
) -> tuple[list[str], set[str], int, int]:
    """返回 (渲染条目, 已展示的桶 id, 实际花费 token, 因上限/预算省略的条目数)。

    max_items 超出时保留最近的（折叠行计 1 条）；预算按条目整条放或整条省略。
    """
    entries, omitted = plan_today(buckets, cfg, now)

    budget = min(cfg["max_tokens"], token_budget)
    rendered, shown, used = [], set(), 0
    for kind, g in entries:
        if kind == "burst":
            text = _render_burst(g)
            cost = count_tokens_approx(text)
        else:
            b = g[0][1]
            text, cost = render_stored_bucket(
                b, f"🕐 [今天 {g[0][0]:%H:%M}] [bucket_id:{b['id']}]", footprint(b)
            )
        if used + cost > budget:
            omitted += 1
            continue
        rendered.append(text)
        shown.update(b["id"] for _, b in g)
        used += cost
    return rendered, shown, used, omitted
