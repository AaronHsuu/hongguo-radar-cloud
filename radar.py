"""Collect public Hongguo charts and build the static feed consumed by Miaoda."""

import argparse
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from topics import build_topics
from momentum import WINDOWS, attach_periods, compare_date
from ideas import build_ideas

SOURCE = "https://hongguoduanju.com"
CHARTS = {
    "hot": "hot-drama", "real": "hot-real-drama",
    "ai": "hot-ai-drama", "comic": "hot-comic-drama",
}
METRICS = ("hot", "fav", "like")
SNAPSHOTS = Path("data/snapshots")
FEED = Path("_site")
BEIJING = timezone(timedelta(hours=8))


class PublicPages:
    def __init__(self):
        self.lock = threading.Lock()
        self.next_request = 0.0

    def loader(self, path):
        error = None
        for delay in (0, 2, 5):
            if delay:
                time.sleep(delay)
            with self.lock:
                time.sleep(max(0, self.next_request - time.monotonic()))
                self.next_request = time.monotonic() + 0.5
            request = Request(SOURCE + path, headers={
                "Accept": "text/html",
                "User-Agent": "Mozilla/5.0 Chrome/126.0 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            try:
                with urlopen(request, timeout=18) as response:
                    html = response.read().decode("utf-8", errors="replace")
                start = re.search(r"_ROUTER_DATA\s*=\s*", html)
                if not start:
                    raise ValueError("红果页面未返回业务数据")
                return json.JSONDecoder().raw_decode(html[start.end():])[0]["loaderData"]
            except HTTPError as exc:
                if exc.code == 429:
                    try:
                        wait = min(30, max(5, float(exc.headers.get("Retry-After", "5"))))
                    except ValueError:
                        wait = 5
                    with self.lock:
                        self.next_request = max(self.next_request, time.monotonic() + wait)
                elif exc.code < 500:
                    raise
                error = exc
            except (OSError, HTTPException, ValueError, KeyError) as exc:
                error = exc
        raise RuntimeError(f"红果官网页面读取失败 {path}") from error

    def chart(self, slug):
        entries = []
        for page in range(1, 6):
            path = f"/rank/{slug}" + (f"?page={page}" if page > 1 else "")
            loaders = self.loader(path)
            content = next((value["content"] for name, value in loaders.items()
                            if name.startswith("rank_") and name.endswith("/page")), {})
            for entry in content.get("rankList", []):
                sid = str(entry.get("seriesId") or entry.get("id") or "")
                if not sid.isascii() or not sid.isdigit() or not entry["title"]:
                    raise ValueError(f"{slug} 出现无效的剧集条目")
                entries.append({"id": sid, "rank": int(entry["rank"]), "title": entry["title"]})
        if sorted(item["rank"] for item in entries) != list(range(1, 101)):
            raise ValueError(f"{slug} 未返回完整的 100 个名次")
        if len({item["id"] for item in entries}) != 100:
            raise ValueError(f"{slug} 出现重复剧集")
        return sorted(entries, key=lambda item: item["rank"])

    def detail(self, sid):
        try:
            page = self.loader(f"/detail?series_id={sid}")["detail_page"]
            actual = page["seriesDetail"]
            social = page["seriesSocialInfo"]
            return {
                "title": actual["series_name"],
                "tags": actual.get("tags") or [],
                "hot": integer((social.get("hot_score_data") or {}).get("score")),
                "fav": integer(social.get("series_favorite_count")),
                "like": integer(social.get("series_like_count")),
            }
        except (HTTPError, KeyError, ValueError, RuntimeError, TypeError):
            return None


def integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_snapshots():
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(SNAPSHOTS.glob("*.json"))]


def ranks_ids(ranks):
    return {item["id"] for entries in ranks.values() for item in entries}


def collect(today, history):
    web = PublicPages()
    with ThreadPoolExecutor(max_workers=4) as pool:
        ranks = dict(zip(CHARTS, pool.map(web.chart, CHARTS.values())))
        print("四个官网榜单各获取 100 条", flush=True)
        previous = history[-1] if history else None
        ids = ranks_ids(ranks)
        since = (today - timedelta(days=14)).isoformat()
        for older in history:
            if older["date"] >= since:
                ids.update(ranks_ids(older["ranks"]))
        if previous and previous["date"] == today.isoformat():
            ids.update(previous["series"])
        ordered = sorted(ids)
        details = dict(zip(ordered, pool.map(web.detail, ordered)))

    series = {sid: detail for sid, detail in details.items() if detail is not None}
    if len(series) < len(ids) * 0.9:
        raise RuntimeError(f"详情读取成功率过低 {len(series)}/{len(ids)}，保留上一批")
    print(f"已核对详情 {len(series)}/{len(ids)} 部", flush=True)
    if previous:
        before = previous["series"]
        if ranks == previous["ranks"] and all(before.get(sid) == detail for sid, detail in series.items()):
            print("官网榜单和剧集数据未变，继续使用已发布批次")
            return None
        if previous["date"] == today.isoformat():
            # An intermittent failure must not erase a detail already captured today.
            series.update((sid, before[sid]) for sid in ids - series.keys() if sid in before)
    return {
        "date": today.isoformat(), "fetched_at": datetime.now(timezone.utc).isoformat(),
        "ranks": ranks, "series": series,
    }


def build(history):
    if not history:
        raise RuntimeError("没有可发布的榜单快照")
    latest = history[-1]
    previous = history[-2] if len(history) > 1 else None
    batch = latest["date"]
    gap = (date.fromisoformat(batch) - date.fromisoformat(previous["date"])).days if previous else None
    week_date = (date.fromisoformat(batch) - timedelta(days=7)).isoformat()
    week = next((day["series"] for day in history if day["date"] == week_date), {})
    ranks = {}
    dates = {day["date"]: day for day in history}
    types = {}
    for kind, entries in latest["ranks"].items():
        old = {item["id"]: item["rank"] for item in previous["ranks"][kind]} if previous else {}
        seen = {item["id"] for day in history[:-1] for item in day["ranks"][kind]}
        rows = []
        comparisons = {str(days): {item["id"]: item["rank"] for item in
                       dates.get(compare_date(batch, days), {}).get("ranks", {}).get(kind, [])}
                       for days in WINDOWS}
        for entry in entries:
            sid = entry["id"]
            if kind != "hot":
                types.setdefault(sid, kind)
            delta = old[sid] - entry["rank"] if sid in old else None
            badge = ("back" if sid in seen else "new") if previous and sid not in old else None
            periods = {days: positions[sid] - entry["rank"] for days, positions in comparisons.items()
                       if sid in positions}
            rows.append(dict(entry, delta=delta, badge=badge, periods=periods))
        ranks[kind] = {"rank_date": batch, "items": rows}

    growth = []
    for sid, detail in sorted(latest["series"].items()):
        before = previous["series"].get(sid, {}) if previous else {}
        row = dict(detail, id=sid, type=types.get(sid, "unknown"))
        for metric in METRICS:
            current, older = detail[metric], before.get(metric)
            row["d_" + metric] = round((current - older) / gap) if gap and current is not None and older is not None else None
        row["r_hot"] = round(row["d_hot"] / before["hot"], 4) if row["d_hot"] is not None and before.get("hot") else None
        for metric in ("hot", "fav"):
            older = week.get(sid, {}).get(metric)
            current = detail[metric]
            row["d7_" + metric] = current - older if current is not None and older is not None else None
        growth.append(row)
    windows = attach_periods(growth, history)
    topic_data = build_topics(history, growth, windows)
    dump(FEED / "snapshot.json", {
        "status": {
            "date": batch, "fetched_at": latest["fetched_at"],
            "rank_dates": dict.fromkeys(CHARTS, batch),
            "tracked": len(set(latest["series"]) | ranks_ids(latest["ranks"])),
        },
        "ranks": ranks, "growth": {"date": batch, "gap": gap, "items": growth, "windows": windows},
    })
    dump(FEED / "topics.json", topic_data)
    dump(FEED / "ideas.json", build_ideas(history, growth))
    series_history = {}
    for day in history[-36:]:
        for sid, metrics in day["series"].items():
            series_history.setdefault(sid, []).append({"date": day["date"], **{key: metrics[key] for key in METRICS}})
    for sid, points in series_history.items():
        dump(FEED / "series" / f"{sid}.json", {"points": points})
    print(f"可发布批次 {batch}，覆盖 {len(growth)} 部剧集")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--refresh-today", action="store_true")
    options = parser.parse_args()
    history = load_snapshots()
    now = datetime.now(BEIJING)
    today = now.date()
    today_exists = bool(history and history[-1]["date"] == today.isoformat())
    if not options.build_only and (options.refresh_today or not today_exists):
        batch = collect(today, history)
        if batch:
            dump(SNAPSHOTS / f"{today}.json", batch)
            history = load_snapshots()
    elif today_exists:
        print("今日已有快照，沿用已保存批次")
    build(history)


if __name__ == "__main__":
    main()
