"""
========================================
tools/breath/episodic.py — 分层情景浮现（3.9.0，默认关闭）
========================================

`surfacing.style: episodic` 时，无参 breath() 按「想起来」的方式分段：
核心准则（不变）→ 接上次 → 今天 → 挂在心上 → 忽然想起。每段有独立选取规则与预算。

展示粒度按分数位置分三档（只是浮现时的详细程度，不是检索命中的返回——
检索命中仍返回全文，按 ID 取全文的入口始终存在）：
清晰（前 30%）全文 / 一般：标题·时间·情绪词·why_remembered·id / 模糊：标题·id。
不显示 [权重:xx]，改用「清晰 / 有点模糊 / 很淡了」。

主题簇只用结构信号（active 关系边、连续写入、tags Jaccard），不调 LLM；
只影响浮现展示，不改任何数据。importance 只在簇内相对排名，存储值不动。

返回 None 表示这次没有可展示的内容，由调用方退回 classic（沿用它的空库/平静文案）。
========================================
"""

import random
from datetime import datetime

from utils import count_tokens_approx, parse_bool, parse_iso_datetime

from .. import _runtime as rt
from ..plan.core import is_letter_bucket
from ._date_range import bucket_in_created_range
from ._shared import bucket_has_tags, footprint_reader
from ._verbatim import render_stored_bucket
from .today import render_today, today_config, written_at

_CLUSTER_MAX = 2
_JACCARD_MIN = 0.5
_CLUSTER_POOL = 120
_FULL_RATIO, _MID_RATIO = 0.3, 0.7
_HINT = "想细看哪件事，breath_search(query=\"完整 bucket_id\") 取全文。"


def emotion_word(meta: dict) -> str:
    """valence/arousal 四象限查表，不调用 LLM。"""
    try:
        v = float(meta.get("valence", 0.5))
        a = float(meta.get("arousal", 0.3))
    except (TypeError, ValueError):
        return ""
    if v >= 0.5:
        return "兴奋" if a >= 0.5 else "安心"
    return "焦虑/激动" if a >= 0.5 else "低落"


def _title(b: dict) -> str:
    m = b.get("metadata") or {}
    return str(m.get("title") or m.get("name") or b["id"]).strip()


def build_clusters(buckets: list[dict], burst_gap_minutes: int, burst_min_size: int) -> dict[str, str]:
    """并查集：返回 bucket_id → 簇代表 id。三个结构信号任一成立就并入同一簇。"""
    parent = {b["id"]: b["id"] for b in buckets}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 1) active 关系边
    for b in buckets:
        for link in (b.get("metadata") or {}).get("relation_links") or []:
            if isinstance(link, dict) and link.get("status", "active") == "active":
                t = str(link.get("target_bucket_id") or "")
                if t in parent:
                    union(b["id"], t)
    # 2) 连续写入（同今天通道的判定：相邻间隔 <= gap 且连续条数 >= min_size）
    stamped = sorted(
        ((written_at(b), b["id"]) for b in buckets if written_at(b) is not None)
    )
    run: list[str] = []
    prev = None
    for t, bid in stamped + [(None, None)]:
        if prev is not None and t is not None and (t - prev).total_seconds() <= burst_gap_minutes * 60:
            run.append(bid)
        else:
            if len(run) >= burst_min_size:
                for other in run[1:]:
                    union(run[0], other)
            run = [bid] if bid else []
        prev = t
    # 3) tags Jaccard（只在前 _CLUSTER_POOL 个里做，控制 O(n²)）
    tagsets = [
        (b["id"], {str(x).strip() for x in (b.get("metadata") or {}).get("tags") or [] if str(x).strip()})
        for b in buckets[:_CLUSTER_POOL]
    ]
    for i, (ida, ta) in enumerate(tagsets):
        if not ta:
            continue
        for idb, tb in tagsets[i + 1:]:
            if tb and len(ta & tb) / len(ta | tb) >= _JACCARD_MIN:
                union(ida, idb)
    return {b["id"]: find(b["id"]) for b in buckets}


def _hours_ago(t: datetime, now: datetime) -> str:
    h = max(0.0, (now - t).total_seconds() / 3600)
    return f"{int(h * 60)} 分钟" if h < 1 else (f"{h:.0f} 小时" if h < 48 else f"{h / 24:.0f} 天")


def _when(b: dict) -> str:
    t = written_at(b)
    return t.strftime("%m-%d %H:%M") if t else ""


def _tier_render(b: dict, tier: str, footprint) -> tuple[str, int]:
    m = b.get("metadata") or {}
    if tier == "full":
        return render_stored_bucket(b, f"[清晰] [bucket_id:{b['id']}]", footprint(b))
    if tier == "mid":
        why = str(m.get("why_remembered") or "").strip()
        bits = [f"《{_title(b)}》", _when(b), emotion_word(m)]
        if why:
            bits.append(why)
        text = f"[有点模糊] {' · '.join(x for x in bits if x)} · [bucket_id:{b['id']}]"
    else:
        text = f"[很淡了] 《{_title(b)}》 [bucket_id:{b['id']}]"
    return text, count_tokens_approx(text)


async def surface_episodic(
    max_results: int,
    max_tokens: int,
    tag_filter: list,
    created_from: "datetime | None" = None,
    created_to: "datetime | None" = None,
) -> "str | None":
    from .surface import _can_surface, _created_at  # 避免循环导入

    try:
        all_buckets = await rt.bucket_mgr.list_all(include_archive=False)
    except Exception as e:
        rt.logger.error(f"episodic: list failed: {e}")
        return None

    surfacing_cfg = rt.config.get("surfacing", {}) or {}
    footprint = footprint_reader()
    in_range = (
        [b for b in all_buckets if bucket_in_created_range(b, created_from, created_to)]
        if (created_from is not None or created_to is not None)
        else all_buckets
    )
    now = datetime.now()
    budget = max_tokens

    # --- 核心准则：与 classic 一致 ---
    core_parts, core_omitted = [], 0
    for b in all_buckets:
        m = b["metadata"]
        if not (m.get("pinned") or m.get("type") == "permanent"):
            continue
        if parse_bool(m.get("protected"), default=False) or not _can_surface(b) \
                or is_letter_bucket(b) or m.get("anchor", False):
            continue
        try:
            text, cost = render_stored_bucket(b, f"📌 [核心准则] [bucket_id:{b['id']}]", footprint(b))
        except Exception:
            continue
        if cost > budget:
            core_omitted += 1
            continue
        core_parts.append(text)
        budget -= cost

    # --- 核心认知（I 与 You，紧凑一行流）---
    cognition_parts = []
    try:
        from ..i.core import is_pending_candidate, _aspect_of
        i_items = [
            b for b in all_buckets
            if (b.get("metadata", {}).get("type") == "i" or is_pending_candidate(b))
            and not is_letter_bucket(b)
        ]
        i_items.sort(
            key=lambda b: str(b.get("metadata", {}).get("last_active") or b.get("metadata", {}).get("created") or ""),
            reverse=True,
        )
        for b in i_items[:2]:
            m = b.get("metadata") or {}
            aspect = _aspect_of(m) or "认知"
            body = (b.get("content") or "").strip().splitlines()[0]
            if len(body) > 45:
                body = body[:43] + "…"
            cognition_parts.append(f"- [I:{aspect}] {body} [id:{b['id']}]")
    except Exception as e:
        rt.logger.warning(f"episodic I extraction failed: {e}")

    try:
        you_service = getattr(rt, "you_service", None)
        if you_service and you_service.status().enabled and you_service.status().scope:
            claims = you_service.store.list_claims(you_service.status().scope, callable_only=False)
            claims_sorted = sorted(
                claims,
                key=lambda c: (1 if c.lifecycle == "formal" else 0, str(getattr(c, "created_at", "") or "")),
                reverse=True,
            )
            aspect_map = {
                "preferred_address": "称呼",
                "explicit_boundary": "边界",
                "stable_fact": "事实",
                "communication_preference": "沟通",
                "interaction_habit": "习惯",
            }
            for c in claims_sorted[:2]:
                aspect_label = aspect_map.get(c.aspect, c.aspect or "习惯")
                content = c.content.strip().splitlines()[0]
                if len(content) > 45:
                    content = content[:43] + "…"
                status_mark = " (已生效)" if c.lifecycle == "formal" else " (观察中)"
                cognition_parts.append(f"- [You:{aspect_label}] {content}{status_mark} [id={c.id}]")
    except Exception as e:
        rt.logger.warning(f"episodic You extraction failed: {e}")

    # --- 活跃待办（Active Plans，紧凑一行流）---
    plan_parts = []
    try:
        import re
        plans = [
            b for b in all_buckets
            if b.get("metadata", {}).get("type") == "plan"
            and not is_letter_bucket(b)
            and b.get("metadata", {}).get("status", "active") == "active"
        ]
        plans.sort(key=lambda b: str(b.get("metadata", {}).get("created") or ""), reverse=True)
        for b in plans[:3]:
            m = b.get("metadata") or {}
            created = str(m.get("created") or "")[:10]
            title = _title(b)
            if re.match(r"^\d{4}-\d{2}-\d{2}", title):
                body = (b.get("content") or "").strip().splitlines()[0]
                desc = body if body else title
            else:
                desc = title
            if len(desc) > 35:
                desc = desc[:33] + "…"
            date_str = f" ({created})" if created else ""
            plan_parts.append(f"- [id:{b['id']}] 《{desc}》{date_str}")
    except Exception as e:
        rt.logger.warning(f"episodic plans extraction failed: {e}")

    extra_cost = count_tokens_approx("\n".join(cognition_parts + plan_parts))
    budget = max(0, budget - extra_cost)

    def _base(b: dict) -> bool:
        m = b["metadata"]
        return (
            _can_surface(b)
            and not m.get("anchor", False)
            and str(m.get("type") or "dynamic") == "dynamic"
            and not is_letter_bucket(b)
            and not m.get("pinned", False)
            and not parse_bool(m.get("protected"), default=False)
            and not m.get("dont_surface", False)
            and bucket_has_tags(m, tag_filter)
        )

    pool = [b for b in in_range if _base(b)]
    shown: set[str] = set()
    section_budget = budget

    # --- 接上次：最近 1~2 条写入 ---
    last_parts: list[str] = []
    stamped = sorted(
        ((written_at(b), b) for b in pool if written_at(b) is not None),
        key=lambda x: (x[0], x[1]["id"]),
        reverse=True,
    )
    last_cap = int(section_budget * 0.15)
    for t, b in stamped[:2]:
        try:
            text, cost = render_stored_bucket(b, f"🔗 [接上次 · 距上次写入 {_hours_ago(t, now)}] [bucket_id:{b['id']}]", footprint(b))
        except Exception:
            continue
        if cost > last_cap:
            continue
        last_parts.append(text)
        shown.add(b["id"])
        last_cap -= cost
        budget -= cost

    # --- 今天（复用 P2）---
    today_parts, today_omitted = [], 0
    tcfg = today_config(surfacing_cfg)
    if tcfg["enabled"]:
        tcfg = dict(tcfg, max_tokens=min(tcfg["max_tokens"], int(section_budget * 0.3)))
        today_parts, today_shown, used, today_omitted = render_today(
            [b for b in pool if b["id"] not in shown], tcfg, budget, footprint, now
        )
        shown |= today_shown
        budget -= used

    # --- 挂在心上：未解决、按分数、簇去重、分档 ---
    recall_reserve = int(section_budget * 0.15)
    mind_budget = max(0, budget - recall_reserve)
    scored = sorted(
        (b for b in pool if not b["metadata"].get("resolved", False) and b["id"] not in shown),
        key=lambda b: rt.decay_engine.calculate_score(b["metadata"]),
        reverse=True,
    )
    cluster_of = build_clusters(scored[:_CLUSTER_POOL], tcfg["burst_gap_minutes"], tcfg["burst_min_size"])
    per_cluster: dict[str, int] = {}
    picked = []
    for b in scored:
        c = cluster_of.get(b["id"], b["id"])
        if per_cluster.get(c, 0) >= _CLUSTER_MAX:
            continue
        per_cluster[c] = per_cluster.get(c, 0) + 1
        picked.append(b)
        if len(picked) >= max_results:
            break
    mind_parts, mind_omitted = [], 0
    n = len(picked)
    for i, b in enumerate(picked):
        tier = "full" if i < n * _FULL_RATIO else ("mid" if i < n * _MID_RATIO else "faint")
        try:
            text, cost = _tier_render(b, tier, footprint)
        except Exception:
            continue
        if cost > mind_budget:
            mind_omitted += 1
            continue
        mind_parts.append(text)
        shown.add(b["id"])
        mind_budget -= cost
    budget = mind_budget + recall_reserve

    # --- 忽然想起：久未浮现（<=2）+ 3% 偶遇，合并为一段 ---
    recall_parts: list[str] = []
    min_age = 24 * 3600
    stale = []
    for b in pool:
        if b["id"] in shown or b["metadata"].get("resolved", False):
            continue
        c = _created_at(b)
        if c is not None and (now - c).total_seconds() < min_age:
            continue
        m = b["metadata"]
        ac = int(m.get("activation_count") or 0)
        imp = int(m.get("importance") or 0)
        last = m.get("last_active") or m.get("created") or ""
        try:
            old = bool(last) and (now - parse_iso_datetime(last)).days >= 7
        except (TypeError, ValueError):
            old = False
        if (ac == 0 and imp >= 8) or (imp >= 9 and old):
            stale.append(b)
    random.shuffle(stale)
    extra = stale[:2]
    if random.random() < 0.03:
        resolved = [b for b in pool if b["metadata"].get("resolved", False) and b["id"] not in shown]
        random.shuffle(resolved)
        extra += resolved[:1]
    for b in extra:
        tier_text, cost = _tier_render(b, "mid", footprint)
        if cost > budget:
            continue
        recall_parts.append(f"💤 {tier_text}")
        budget -= cost

    if not (core_parts or cognition_parts or plan_parts or last_parts or today_parts or mind_parts or recall_parts):
        return None

    parts = []
    if core_parts:
        parts.append("=== 核心准则 ===\n" + "\n---\n".join(core_parts))
    if cognition_parts:
        parts.append("=== 核心认知 ===\n" + "\n".join(cognition_parts))
    if plan_parts:
        parts.append("=== 活跃待办 ===\n" + "\n".join(plan_parts))
    if last_parts:
        parts.append("=== 接上次 ===\n" + "\n---\n".join(last_parts))
    if today_parts:
        head = "=== 今天 ===" + (f"（另有 {today_omitted} 条未列出）" if today_omitted else "")
        parts.append(head + "\n" + "\n---\n".join(today_parts))
    if mind_parts:
        parts.append("=== 挂在心上 ===\n" + "\n---\n".join(mind_parts))
    if recall_parts:
        parts.append("=== 忽然想起 ===\n" + "\n---\n".join(recall_parts))
    if core_omitted:
        parts.append(f"token 预算不足：{core_omitted} 条核心准则没能返回。")
    if mind_omitted:
        parts.append(f"token 预算不足：{mind_omitted} 条挂在心上的记忆因放不下未返回；已返回的「清晰」条目正文未截断。")
    hint = "想细看哪件事，breath_search(query=\"完整 bucket_id\") 取全文；待办完成用 trace(id, status=\"resolved\")。"
    parts.append(hint)
    return "\n\n".join(parts)
