"""Build the public comparison library used to score short-drama ideas."""
from collections import defaultdict
from datetime import date, timedelta
import json

from topics import ARCHIVE, family
from idea_forecast import build_forecast

BOARDS = ("hot", "real", "ai", "comic")


def build_ideas(history, growth):
    entries = {}
    active = {item["id"]: item for item in growth}
    latest = history[-1]["date"]
    collected = {batch["date"] for batch in history}
    short_days = {(date.fromisoformat(latest) - timedelta(days=i)).isoformat() for i in range(3)}
    recent_days = {(date.fromisoformat(latest) - timedelta(days=i)).isoformat() for i in range(6)}
    recent = defaultdict(dict)
    ranked = defaultdict(dict)

    def remember(sid, title):
        return entries.setdefault(sid, {"id": sid, "title": title, "tags": set()})

    def rank(sid, day, positions):
        best_board = min(positions, key=lambda board: (positions[board], BOARDS.index(board)))
        position = positions[best_board]
        row = entries[sid]
        ranked[sid][day] = position
        if "best" not in row or position <= row["best"][1]:
            row["best"] = [best_board, position, day]
        if day in recent_days:
            recent[sid][day] = max(recent[sid].get(day, 0), 101 - position)

    for batch in json.loads(ARCHIVE.read_text(encoding="utf-8")):
        day = batch["date"]
        if day in collected or day > latest:
            continue
        collected.add(day)
        for archived in batch["entries"]:
            sid = archived["id"]
            remember(sid, archived["title"])["tags"].update(archived["tags"])
            rank(sid, day, archived["ranks"])

    for batch in history:
        day = batch["date"]
        for sid, metrics in batch["series"].items():
            row = remember(sid, metrics["title"])
            row["title"] = metrics["title"]
            row["tags"].update(metrics.get("tags") or [])
            if metrics.get("intro"):
                row["intro"] = metrics["intro"]
            row["sample"] = [day, metrics["hot"], metrics["fav"]]
        positions = defaultdict(dict)
        for board, shows in batch["ranks"].items():
            for show in shows:
                sid = show["id"]
                remember(sid, show["title"])
                positions[sid][board] = show["rank"]
        for sid, values in positions.items():
            rank(sid, day, values)

    for sid, row in entries.items():
        row["tags"] = sorted(row["tags"])
        row["family"] = family(row["title"])
        row["rankPoints"] = [
            sum(recent[sid].get(day, 0) for day in days) / len(days & collected)
            if days & collected else None
            for days in (short_days, recent_days - short_days)
        ]
        if sid in active:
            source = active[sid]
            row["periods"] = {
                days: [period["hot"], period["fav"]] for days, period in source["periods"].items()
            }
            if source.get("burst"):
                row["burst"] = source["burst"]
    days = sorted(collected)[-90:]
    research = build_forecast(entries, ranked, days)
    return {"version": 1, "date": latest, "start": min(collected), "days": len(collected), "research": research,
            "series": list(entries.values())}
