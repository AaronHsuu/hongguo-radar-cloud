"""Generate the daily topic view from the same batches used by the radar."""
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
import json
import re

from momentum import WINDOWS
from tag_radar import build_tag_radar
from topic_rules import RULES

ARCHIVE = Path(__file__).with_name("data") / "topic-archive.json"
CLASSIFIERS = [
    (name, set(direct.split()), set(context.split()), set(pairing.split()), re.compile(pattern) if pattern else None)
    for name, direct, context, pairing, pattern in RULES
]


def classify(title, tags):
    return [
        name for name, direct, context, pairing, pattern in CLASSIFIERS
        if tags & direct or (tags & context and tags & pairing) or (pattern and pattern.search(title))
    ]


def family(title):
    text = re.sub(r"[（(]仿真人[）)]$", "", title)
    text = re.sub(r"[：:，,。！？!?·\s《》【】（）()]", "", text)
    suffix = r"(?:第?[一二三四五六七八九十两0-9]+(?:季|部|卷|篇)(?:完结篇|最终章)?|完结篇|最终章|(?<![0-9])[2-9])$"
    return re.sub(suffix, "", text) or title


def weekly(values):
    clean = [value for value in values if value is not None]
    return mean(clean) if len(clean) >= 5 else None


def rank_entries(batch):
    """Each series contributes once per day, with its best official position."""
    entries = {}
    for chart in batch["ranks"].values():
        for item in chart:
            current = entries.get(item["id"])
            if current is None or item["rank"] < current["rank"]:
                entries[item["id"]] = item
    return [(sid, item["rank"], item["title"]) for sid, item in entries.items()]


def aggregate(history, growth, archive, windows):
    final_day = date.fromisoformat(history[-1]["date"])
    floor = (final_day - timedelta(days=55)).isoformat()
    by_day = {batch["date"]: batch for batch in history if batch["date"] >= floor}
    archived = {batch["date"]: batch["entries"] for batch in archive
                if floor <= batch["date"] <= final_day.isoformat() and batch["date"] not in by_day}
    dates = sorted(by_day.keys() | archived.keys())
    start = date.fromisoformat(dates[0])
    calendar = [(start + timedelta(days=i)).isoformat() for i in range((final_day - start).days + 1)]
    tags, titles, entries = defaultdict(set), {}, {}
    for day in dates:
        batch = by_day.get(day)
        if batch:
            entries[day] = rank_entries(batch)
            for sid, detail in batch["series"].items():
                tags[sid].update(detail.get("tags") or [])
                titles[sid] = detail["title"]
        else:
            entries[day] = [(row["id"], min(row["ranks"].values()), row["title"]) for row in archived[day]]
            for row in archived[day]:
                tags[row["id"]].update(row["tags"])
        for sid, _, title in entries[day]:
            titles.setdefault(sid, title)
    assignments = {sid: classify(title, tags[sid]) for sid, title in titles.items()}
    families = {sid: family(title) for sid, title in titles.items()}
    names = [name for name, *_ in CLASSIFIERS]
    shares = {name: [] for name in names}
    recent_ids = {name: set() for name in names}
    latest_titles = {name: Counter() for name in names}
    previous_families = {name: Counter() for name in names}
    latest_families = {name: Counter() for name in names}
    heat = {name: {"up": set(), "net": 0, "example": None} for name in names}
    ranked_ids = set()
    for i, day in enumerate(calendar):
        rows = entries.get(day, [])
        total = sum(101 - rank for _, rank, _ in rows)
        weights = Counter()
        for sid, rank, _ in rows:
            ranked_ids.add(sid)
            topics = assignments[sid]
            for topic in topics:
                weight = (101 - rank) / len(topics)
                weights[topic] += weight
                if i >= len(calendar) - 14:
                    recent_ids[topic].add(sid)
                    if i >= len(calendar) - 7:
                        latest_families[topic][families[sid]] += weight
                        latest_titles[topic][sid] += weight
                    else:
                        previous_families[topic][families[sid]] += weight
        for name in names:
            shares[name].append(100 * weights[name] / total if total else None)

    comparable = [row for row in growth if row.get("d_hot") is not None]
    for row in comparable:
        sid = row["id"]
        delta = row["d_hot"]
        topics = assignments.get(sid, [])
        for name in topics:
            marks = heat[name]
            marks["net"] += delta / len(topics)
            if delta > 0:
                marks["up"].add(families[sid])
                if not marks["example"] or delta > marks["example"]["delta"]:
                    marks["example"] = {"id": sid, "title": titles[sid], "delta": delta}
    coverage = {
        "start": calendar[0], "end": calendar[-1], "days": sum(bool(items) for items in entries.values()),
        "series": len(ranked_ids), "topics": len(names),
        "comparable": len(comparable), "rising": sum(row["d_hot"] > 0 for row in comparable),
        "falling": sum(row["d_hot"] < 0 for row in comparable),
        "recentDays": sum(day in entries for day in calendar[-7:]),
        "previousDays": sum(day in entries for day in calendar[-14:-7]),
    }
    series = {days: [] for days in WINDOWS}
    for name, raw in shares.items():
        if len(recent_ids[name]) < 5:
            continue
        top = latest_families[name]
        prev = previous_families[name]
        increases = [value - prev[f] for f, value in top.items() if value > prev[f]]
        representatives, seen = [], set()
        for sid, _ in latest_titles[name].most_common():
            if families[sid] not in seen:
                representatives.append({"id": sid, "title": titles[sid]})
                seen.add(families[sid])
                if len(representatives) >= 3:
                    break
        evidence = {
            "families": len(top), "concentration": round(max(top.values()) / sum(top.values()), 3) if top else None,
            "gainConcentration": round(max(increases) / sum(increases), 3) if increases else None,
            "dailyNet": round(heat[name]["net"]), "dailyRising": len(heat[name]["up"]),
            "risingExample": heat[name]["example"], "examples": representatives,
        }
        for days in WINDOWS:
            result = trend(name, raw, calendar, days)
            if result is not None and max(result["current"], result["previous"]) >= .5:
                series[days].append({**result, **evidence})
    readings = {str(days): reading(series[days]) for days in WINDOWS}
    for key, window in windows.items():
        summarize_growth(window, key, growth, assignments, families,
                         {row["name"]: row for row in series[int(key)]})
    return {"version": 1, "date": final_day.isoformat(), "coverage": coverage,
            "forecastStart": (final_day + timedelta(days=1)).isoformat(),
            "forecastEnd": (final_day + timedelta(days=7)).isoformat(),
            "dates": calendar[-43:], **readings["7"], "windows": readings,
            "tagRadar": build_tag_radar(entries, calendar, tags, families, growth, trend)}


def reading(series):
    rising = sorted((item for item in series if item["delta"] > 0), key=lambda item: -item["delta"])[:10]
    falling = sorted((item for item in series if item["delta"] < 0), key=lambda item: item["delta"])[:10]
    priority, watch = [], []
    for topic in rising:
        if (topic["hotDays"] >= 3 and topic["families"] >= 5 and topic["accelerating"]
                and topic["dailyRising"] >= 2 and (topic["gainConcentration"] or 0) < .4):
            priority.append(topic["name"])
        elif topic["hotDays"] and topic["families"] >= 5:
            watch.append(topic["name"])
    focus = [{"name": name, "priority": True} for name in priority[:3]]
    if len(focus) < 3:
        focus.extend({"name": name, "priority": False} for name in watch[:3 - len(focus)])
    return {"up": rising, "down": falling, "focus": focus}


def summarize_growth(window, key, growth, assignments, families, trends):
    net = Counter()
    rising_families = defaultdict(set)
    recent = defaultdict(Counter)
    examples = {}
    period_examples = {}
    for item in growth:
        sid = item["id"]
        amount = item["periods"][key]["hot"]
        assigned = assignments.get(sid, [])
        if amount is not None:
            for name in assigned:
                net[name] += amount / len(assigned)
                if amount > 0:
                    rising_families[name].add(families[sid])
                    if name not in period_examples or amount > period_examples[name]["delta"]:
                        period_examples[name] = {"id": sid, "title": item["title"], "delta": amount}
        if item.get("signal"):
            signal = {"id": sid, "title": item["title"], **item["signal"]}
            for name in assigned:
                recent[name][families[sid]] += signal["recentGain"] / len(assigned)
                if name not in examples or signal["recentGain"] > examples[name]["recentGain"]:
                    examples[name] = signal
    for name, row in trends.items():
        row["periodNet"] = round(net[name]) if name in net else None
        row["periodExample"] = period_examples.get(name)
    candidates = []
    for name, signals in recent.items():
        # A single hit should not promote its entire topic.
        trend = trends.get(name)
        if not trend or trend["delta"] < .10 or len(signals) < 3:
            continue
        total = sum(signals.values())
        largest = max(signals.values())
        if largest / total > .60:
            continue
        candidates.append({"name": name, "families": len(signals), "risingFamilies": len(rising_families[name]),
                           "net": trends[name]["periodNet"], "shareDelta": trend["delta"],
                           "recent": round(total), "example": examples[name]})
    window["themes"] = sorted(candidates, key=lambda item: -item["recent"])[:3]
    window["top"] = sorted(window["top"], key=lambda item: -item["gain"])[:3]
    picks, seen = [], set()
    for pick in sorted(window["picks"], key=lambda item: (-item["recentGain"], -item["upDays"], -item["gain"])):
        group = families[pick["id"]]
        if group not in seen:
            picks.append(pick)
            seen.add(group)
        if len(picks) >= 3:
            break
    window["picks"] = picks


def trend(name, raw, dates, days):
    smoothed = [
        weekly(raw[i - 6:i + 1]) if i >= 6 and value is not None else None
        for i, value in enumerate(raw)
    ]
    current, previous = weekly(raw[-7:]), weekly(raw[-days - 7:-days])
    if current is None or previous is None:
        return None
    baseline = [value for value in raw[-35:-7] if value is not None]
    hot = 0
    if len(baseline) >= 14:
        average = mean(baseline)
        threshold = max(average * 1.1, average + .1)
        for value in reversed(smoothed):
            if value is None or value < threshold:
                break
            hot += 1
    cooling = None
    delta = current - previous
    peaks = [i for i in range(max(0, len(dates) - 28), len(dates) - 1) if smoothed[i] is not None]
    if delta < 0 and peaks:
        peak = max(peaks, key=lambda i: (smoothed[i], i))
        below = 0
        threshold = smoothed[peak] * .95
        for value in reversed(smoothed[peak + 1:]):
            if value is None or value > threshold:
                break
            below += 1
        if below >= 2:
            cooling = dates[-below]
    def points(values):
        return [round(v, 4) if v is not None else None for v in values[-43:]]
    recent = smoothed[-4:]
    return {
        "name": name, "current": round(current, 4), "previous": round(previous, 4), "delta": round(delta, 4),
        "hotDays": hot, "hotSince": dates[-hot] if hot else None, "coolSince": cooling,
        "coolDays": (date.fromisoformat(dates[-1]) - date.fromisoformat(cooling)).days + 1 if cooling else 0,
        "accelerating": len(recent) == 4 and all(v is not None for v in recent)
        and recent[-1] > recent[0] and sum(a < b for a, b in zip(recent, recent[1:])) >= 2,
        "raw": points(raw), "trend": points(smoothed),
    }


def build_topics(history, growth, windows):
    archive = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    return aggregate(history, growth, archive, windows)
