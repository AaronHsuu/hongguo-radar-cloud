"""Compute rank-share evidence and a rules-based 30-day opportunity index."""

from collections import defaultdict
from datetime import date, timedelta
from statistics import mean


GENERIC = {"剧情", "爱情", "现代", "古代", "反转", "成长", "短剧", "圆满结局"}


def build_forecast(entries, ranked, days):
    indexes = {day: index for index, day in enumerate(days)}
    strongest = [defaultdict(int) for _ in days]
    by_tag = defaultdict(lambda: [defaultdict(int) for _ in days])
    for sid, row in entries.items():
        ranks = [(indexes[day], position) for day, position in sorted(ranked[sid].items()) if day in indexes]
        if ranks:
            row["rankHistory"] = ranks
        for index, position in ranks:
            family = row["family"]
            weight = 101 - position
            strongest[index][family] = max(strongest[index][family], weight)
            for tag in row["tags"]:
                if len(tag) >= 2 and tag not in GENERIC:
                    cohort = by_tag[tag][index]
                    cohort[family] = max(cohort[family], weight)

    totals = [sum(items.values()) for items in strongest]
    latest = date.fromisoformat(days[-1])
    ages = [(latest - date.fromisoformat(day)).days for day in days]
    recent = [i for i, age in enumerate(ages) if age < 7 and totals[i]]
    prior = [i for i, age in enumerate(ages) if 7 <= age < 14 and totals[i]]
    older = [i for i, age in enumerate(ages) if 21 <= age < 28 and totals[i]]
    bursts = {}
    for sid, row in entries.items():
        burst = row.get("burst")
        if not burst or not row.get("rankHistory"):
            continue
        if row["rankHistory"][-1][0] not in recent:
            continue
        for tag in row["tags"]:
            if burst["gain"] > bursts.get(tag, {}).get("gain", 0):
                bursts[tag] = {"id": sid, "title": row["title"], **burst}

    results = []
    for tag, cohorts in by_tag.items():
        points = [100 * sum(cohort.values()) / total if total else None for cohort, total in zip(cohorts, totals)]
        active = len({family for i in recent for family in cohorts[i]})
        if not active and not any(cohorts[i] for i in prior):
            continue
        share = mean(points[i] for i in recent) if recent else None
        delta = share - mean(points[i] for i in prior) if len(recent) >= 4 and len(prior) >= 4 else None
        month_delta = share - mean(points[i] for i in older) if len(recent) >= 4 and len(older) >= 4 else None
        burst = bursts.get(tag)
        if not active:
            state = "正在降温"
        elif delta is None:
            state = "信号待观察"
        elif delta >= max(.12, share * .13):
            state = "正在升温"
        elif delta <= -max(.12, share * .15):
            state = "正在降温"
        elif share >= 2 and active >= 3:
            state = "持续高热"
        else:
            state = "走势平稳"

        # Rank future opportunities using persistence, breadth and acceleration observed so far.
        # No feature in this score comes from after the last available snapshot.
        score = (max(-22, min(22, (delta or 0) * 24))
                 + max(-12, min(12, (month_delta or 0) * 12))
                 + min(18, active * 2) + min(10, (share or 0) * 4)
                 + (12 if burst else 0))
        if active and (score >= 24 or burst):
            outlook = "值得跟进"
        elif state == "正在降温":
            outlook = "谨慎跟进"
        else:
            outlook = "继续观察"
        results.append({
            "tag": tag, "index": round(score, 1), "active": active, "share": round(share, 3) if share is not None else None,
            "delta": round(delta, 3) if delta is not None else None, "state": state, "outlook": outlook,
            "burst": burst, "confidence": "初步" if active >= 3 and month_delta is not None else "较低",
            "trend": [round(point, 3) if point is not None else None for point in points],
        })
    results.sort(key=lambda row: (-row["index"], row["tag"]))
    return {"dates": days, "totals": totals, "targetDate": (latest + timedelta(days=30)).isoformat(),
            "opportunities": results}
