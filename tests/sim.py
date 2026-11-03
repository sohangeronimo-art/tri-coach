"""Simulate a few weeks of training to exercise the adaptive engine.

  python -m tests.sim 2026-11-25 [out.json]

Builds fake Garmin data (some sessions done, some missed, a bad night's sleep on
'today') and runs the full pipeline offline. Used for testing and for the demo preview.
"""
import datetime as dt
import json
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import crypto  # noqa: E402
from engine.plan_builder import build_plan  # noqa: E402
from engine import run as runner  # noqa: E402


def simulate(today, miss_ids=(), seed=4):
    random.seed(seed)
    cfg = json.load(open("config/athlete.json"))
    plan = build_plan(cfg)
    acts, well = [], {}
    start = dt.date.fromisoformat(cfg["plan_start"])
    # 6 weeks of pre-plan history incl. the Husky race on Nov 1
    d = start - dt.timedelta(days=42)
    while d < start:
        for sport, mins, p in (("swim", 45, .35), ("bike", 75, .4), ("run", 45, .45)):
            if random.random() < p:
                acts.append(dict(id=f"g{len(acts)}", source="garmin", name=f"Pre {sport}", sport=sport,
                                 start=f"{d} 06:30:00", date=d.isoformat(), duration_s=mins * 60,
                                 distance_m={"swim": 2000, "bike": 30000, "run": 8000}[sport], avg_hr=142))
        d += dt.timedelta(days=1)
    acts.append(dict(id="g9999", source="garmin", name="Husky Triathlon", sport="run", start="2026-11-01 07:00:00",
                     date="2026-11-01", duration_s=2.6 * 3600, distance_m=10000, avg_hr=160))
    for s in plan["sessions"]:
        sd = dt.date.fromisoformat(s["date"])
        if sd >= today or s["id"] in miss_ids:
            continue
        mins = s["duration_min"] * random.uniform(0.9, 1.08)
        sport = s["sport"]
        dist = {"swim": mins / 2.0 * 100, "bike": mins / 60 * 28000, "run": mins * 60 / 340 * 1000}.get(sport)
        acts.append(dict(id=f"g{len(acts)}", source="garmin", name=s["title"], sport=sport,
                         start=f"{s['date']} 06:{random.randint(0, 50):02d}:00", date=s["date"],
                         duration_s=mins * 60, distance_m=dist, avg_hr=random.randint(128, 158)))
        # Strava duplicate of a few, to check de-dupe
        if random.random() < .3:
            acts.append(dict(acts[-1], id=f"s{len(acts)}", source="strava"))
    for k in range(60, -1, -1):
        day = today - dt.timedelta(days=k)
        hrv = random.gauss(64, 4)
        well[day.isoformat()] = dict(hrv=round(hrv), hrv_low=57, hrv_high=72, hrv_status="BALANCED",
                                     sleep_score=random.randint(70, 88), sleep_h=round(random.uniform(6.8, 8.3), 1),
                                     rhr=random.randint(47, 51), body_battery=random.randint(70, 95))
    for k in range(60, -1, -1):
        day = today - dt.timedelta(days=k)
        if random.random() < .6:
            well[day.isoformat()]["weight_kg"] = round(79.4 - (60 - k) * 0.045 + random.gauss(0, .35), 1)
    # rough night
    well[today.isoformat()].update(hrv=49, hrv_status="LOW", sleep_score=46, sleep_h=5.4, rhr=56, body_battery=48)
    return cfg, acts, well


def main():
    today = dt.date.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2026-12-17")
    out = sys.argv[2] if len(sys.argv) > 2 else None
    cfg, acts, well = simulate(today, miss_ids={"2026-12-15-bike_quality"})
    os.environ.setdefault("APP_PASSPHRASE", "demo-passphrase")
    tmp = tempfile.mkdtemp()
    state_path = os.path.join(tmp, "state.enc")
    from engine.sources import merge_activities
    crypto.write(state_path, dict(activities=merge_activities([], acts), wellness=well,
                                  sync_status=dict(garmin=dict(ok=True, at=f"{today}T05:02"),
                                                   strava=dict(ok=True, at=f"{today}T05:02"))),
                 os.environ["APP_PASSPHRASE"])
    plain = out or os.path.join(tmp, "bundle.json")
    runner.main(["--today", today.isoformat(), "--offline", "--state", state_path,
                 "--out", os.path.join(tmp, "data.enc"), "--plain", plain])
    b = json.load(open(plain))
    print("\nReadiness:", b["readiness"]["status"], b["readiness"]["score"], "| form", b["form"])
    print("Race-day projection:", b["projection"][-1])
    print("\nAdjustments:")
    for a in b["adjustments"]:
        print(" -", a["session_date"], a["change"], "|", a["reason"][:140])
    print("\nThis week:")
    w = next(w for w in b["weeks"] if w["start"] <= today.isoformat() <= w["end"])
    for s in b["sessions"]:
        if w["start"] <= s["date"] <= w["end"]:
            print(f"  {s['date']} {s['sport']:8} {s['status']:8} {s['title']:32} {s['duration_min']:4}m"
                  + (f"  <- {s['adjusted'][:60]}" if s.get("adjusted") else ""))
    print("extras:", len(b["extras"]), "| activities stored:", len(acts))


if __name__ == "__main__":
    main()
