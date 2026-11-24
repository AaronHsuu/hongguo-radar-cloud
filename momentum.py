"""Compare exact dates and identify series with sustained recent gains."""
from datetime import date, timedelta

WINDOWS = (7, 30)


def compare_date(day, days):
    return (date.fromisoformat(day) - timedelta(days=days)).isoformat()


def change(current, previous):
    return current - previous if current is not None and previous is not None else None


def attach_periods(growth, history):
    batches = {batch["date"]: batch["series"] for batch in history}
    day = history[-1]["date"]
    baselines = {days: batches.get(compare_date(day, days), {}) for days in WINDOWS}
    recent = [batches.get(compare_date(day, offset), {}) for offset in range(3, -1, -1)]
    stats = {str(days): {"baseDate": compare_date(day, days), "comparable": 0, "rising": 0,
                         "falling": 0, "top": [], "picks": [], "themes": []} for days in WINDOWS}
    for item in growth:
        sid = item["id"]
        hot = [batch.get(sid, {}).get("hot") for batch in recent]
        daily = [change(after, before) for before, after in zip(hot, hot[1:])]
        up_days = sum(delta is not None and delta > 0 for delta in daily)
        short_gain = change(hot[-1], hot[0])
        if short_gain is not None and short_gain > 0 and up_days >= 2 and daily[-1] > 0:
            item["signal"] = {"recentGain": short_gain, "upDays": up_days}
        item["periods"] = {}
        for days, base in baselines.items():
            span = stats[str(days)]
            past = base.get(sid, {})
            amount = change(item.get("hot"), past.get("hot"))
            fav = change(item.get("fav"), past.get("fav"))
            like = change(item.get("like"), past.get("like"))
            rate = amount / past["hot"] if amount is not None and past.get("hot") else None
            item["periods"][str(days)] = {"hot": amount, "fav": fav, "like": like, "rate": rate}
            if amount is None:
                continue
            span["comparable"] += 1
            if amount <= 0:
                span["falling"] += amount < 0
                continue
            span["rising"] += 1
            signal = {"id": sid, "title": item["title"], "type": item["type"],
                      "gain": amount, "rate": rate, "recentGain": short_gain, "upDays": up_days}
            span["top"].append(signal)
            if "signal" in item:
                span["picks"].append(signal)
    return stats
