"""dream 末尾的「可能已经结束的事」建议（3.8.0）。

只列出，不自动标记 resolved——结案由模型确认（rule.md 第 8 条）。
规则保守：未解决的动态桶，最近一次写入在 14 天以上，且之后出现了结构上同主题的新桶
（active 关系边指向更新的桶，或 tags 的 Jaccard >= 0.5 且共有至少 2 个 tag）。
最多 3 条。纯结构判断，不看内容。
"""

from datetime import datetime, timedelta

from utils import parse_bool
from ..breath.today import written_at

_MAX_ITEMS = 3
_MIN_AGE_DAYS = 14
_JACCARD_MIN = 0.5


def _tags(meta: dict) -> set[str]:
    return {str(t).strip() for t in (meta.get("tags") or []) if str(t).strip()}


def _eligible(b: dict) -> bool:
    meta = b.get("metadata") or {}
    return (
        str(meta.get("type") or "dynamic") == "dynamic"
        and not meta.get("resolved", False)
        and not parse_bool(meta.get("anchor"), default=False)
        and not parse_bool(meta.get("pinned"), default=False)
        and not parse_bool(meta.get("protected"), default=False)
    )


def find_possibly_closed(all_buckets: list[dict], now: datetime | None = None) -> list[tuple[dict, dict]]:
    """返回 [(旧桶, 之后同主题的新桶)]，最多 _MAX_ITEMS 条，按旧桶写入时间从早到晚。"""
    now = now or datetime.now()
    cutoff = now - timedelta(days=_MIN_AGE_DAYS)
    stamped = [(written_at(b), b) for b in all_buckets if _eligible(b)]
    stamped = [(t, b) for t, b in stamped if t is not None]
    stamped.sort(key=lambda x: x[0])
    out: list[tuple[dict, dict]] = []
    for i, (t_old, old) in enumerate(stamped):
        if t_old > cutoff:
            break
        old_meta = old["metadata"]
        old_tags = _tags(old_meta)
        linked = {
            str(link.get("target_bucket_id"))
            for link in (old_meta.get("relation_links") or [])
            if isinstance(link, dict) and link.get("status", "active") == "active"
        }
        for t_new, new in stamped[i + 1:]:
            if t_new - t_old < timedelta(days=1):
                continue
            new_tags = _tags(new["metadata"])
            shared = old_tags & new_tags
            union = old_tags | new_tags
            if new["id"] in linked or (
                len(shared) >= 2 and union and len(shared) / len(union) >= _JACCARD_MIN
            ):
                out.append((old, new))
                break
        if len(out) >= _MAX_ITEMS:
            break
    return out


def render_closure_hint(pairs: list[tuple[dict, dict]]) -> str:
    if not pairs:
        return ""
    lines = ["=== 可能已经结束的事（只是建议，没有改动任何状态）==="]
    for old, new in pairs:
        o, n = old["metadata"], new["metadata"]
        lines.append(
            f"《{o.get('title') or o.get('name') or old['id']}》[bucket_id:{old['id']}]"
            f" 之后有同主题的新记忆《{n.get('title') or n.get('name') or new['id']}》"
            f"[bucket_id:{new['id']}]"
        )
    lines.append("若确认已经结束：trace(bucket_id, resolved=1)；还没结束就不用管。")
    return "\n".join(lines)
