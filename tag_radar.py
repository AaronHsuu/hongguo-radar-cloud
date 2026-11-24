"""Rank official labels by changes in chart share and support from different series."""
from collections import Counter, defaultdict
from math import log1p
from statistics import mean

from momentum import WINDOWS

GENERIC_TAGS = {"剧情", "古代", "现代", "古装", "都市", "爱情", "家庭", "成长", "恋爱", "反转"}


def build_tag_radar(entries, calendar, tags, families, growth, trend):
    item_tags = {sid: values - GENERIC_TAGS for sid, values in tags.items()}
    daily = []
    latest = defaultdict(Counter)
    recent_families = defaultdict(set)
    for day in calendar:
        rows = entries.get(day, [])
        total = sum(101 - rank for _, rank, _ in rows)
        weights = Counter()
        for sid, rank, _ in rows:
            available = item_tags.get(sid, set())
            for tag in available:
                fractional = (101 - rank) / len(available)
                weights[tag] += fractional
                if day >= calendar[-7]:
                    latest[tag][sid] += fractional
                    recent_families[tag].add(families[sid])
        daily.append({tag: 100 * score / total for tag, score in weights.items()} if total else None)

    by_tag = defaultdict(list)
    for item in growth:
        for tag in item_tags.get(item["id"], ()):
            by_tag[tag].append(item)

    windows = {}
    for window in WINDOWS:
        groups = {"up": [], "down": []}
        for tag, support in recent_families.items():
            if len(support) < 3:
                continue
            raw = [scores.get(tag, 0) if scores is not None else None for scores in daily]
            recent = [value for value in raw[-3:] if value is not None]
            before = [value for value in raw[-6:-3] if value is not None]
            row = trend(tag, raw, calendar, window)
            if not row or len(recent) < 2 or len(before) < 2 or max(row["current"], row["previous"]) < .035:
                continue
            change = mean(recent) - mean(before)
            if abs(change) < .005:
                continue
            direction = "up" if change > 0 else "down"
            sign = 1 if direction == "up" else -1
            aligned = sign * row["delta"] > 0
            linked = by_tag.get(tag, [])
            moves = {families[item["id"]] for item in linked
                     if sign * (item["periods"][str(window)]["hot"] or 0) > 0}
            signals = {families[item["id"]] for item in linked if item.get("signal")}
            # Short-window movement stays dominant even in the 30-day view.
            period_bonus = min(abs(change) * 1.5, max(0, sign * row["delta"]) * .40)
            breadth = len(signals) if direction == "up" else len(moves)
            score = (abs(change) + period_bonus) * (1 + .14 * log1p(len(support)) + .08 * log1p(breadth))
            if not aligned:
                score *= .65
            groups[direction].append({
                **row, "shortDelta": round(change, 4), "aligned": aligned, "families": len(support),
                "movingFamilies": len(moves), "recentRisers": len(signals), "score": round(score, 6),
                "shows": related(linked, latest[tag], families, window, sign),
            })
        windows[str(window)] = {
            direction: sorted(items, key=lambda row: (-row["score"], row["name"]))[:20]
            for direction, items in groups.items()
        }
    return windows


def related(items, weights, families, days, sign):
    def ordering(row):
        delta = row["periods"][str(days)]["hot"]
        return (sign * (delta or 0) > 0, weights[row["id"]] > 0, sign * (delta or 0),
                weights[row["id"]], row["hot"] or 0, row["id"])

    shows = []
    seen = set()
    for row in sorted(items, key=ordering, reverse=True):
        key = families[row["id"]]
        if key in seen:
            continue
        seen.add(key)
        shows.append({"id": row["id"], "title": row["title"],
                      "hot": row["hot"], "delta": row["periods"][str(days)]["hot"]})
        if len(shows) == 5:
            break
    return shows
