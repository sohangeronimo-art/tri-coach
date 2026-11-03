"""Daily job: sync Garmin + Strava, adapt the plan, write the encrypted app bundle.

  python -m engine.run                 # normal daily run (GitHub Action)
  python -m engine.run --strava-code X # finish connecting Strava
  python -m engine.run --offline       # no syncing, just re-plan
  python -m engine.run --plain out.json  # also write an unencrypted copy (local debugging only)
"""
import argparse
import datetime as dt
import json
import os
import sys
import traceback
from zoneinfo import ZoneInfo

from . import crypto
from .adapt import Engine
from .plan_builder import build_plan
from .sources import fetch_garmin, fetch_strava, merge_activities, strava_token

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def summarise_weeks(weeks, sessions, extras):
    out = []
    for w in weeks:
        ss = [s for s in sessions if w["start"] <= s["date"] <= w["end"]]
        ex = [a for a in extras if w["start"] <= a["date"] <= w["end"]]
        by = {}
        for sport in ("swim", "bike", "run", "strength"):
            ps = [s for s in ss if s["sport"] == sport]
            done_min = sum((s.get("completed") or {}).get("minutes") or 0 for s in ps) + \
                sum(round((a.get("duration_s") or 0) / 60) for a in ex if a["sport"] == sport)
            done_km = sum(((s.get("completed") or {}).get("distance_m") or 0) for s in ps) / 1000 + \
                sum((a.get("distance_m") or 0) for a in ex if a["sport"] == sport) / 1000
            plan_km = sum((s.get("distance_m") or 0) for s in ps) / 1000
            by[sport] = dict(planned_min=sum(s["duration_min"] for s in ps), done_min=done_min,
                             planned_km=round(plan_km, 1), done_km=round(done_km, 1),
                             sessions=len(ps), done=sum(1 for s in ps if s.get("status") in ("done", "partial")))
        planned_tss = sum(s["tss"] for s in ss) or 0
        done_tss = sum((s.get("completed") or {}).get("tss") or 0 for s in ss) + sum(a.get("tss") or 0 for a in ex)
        out.append(dict(w, by_sport=by, sessions_total=len([s for s in ss if s["sport"] != "strength"]) + len([s for s in ss if s["sport"] == "strength"]),
                        sessions_done=sum(1 for s in ss if s.get("status") in ("done", "partial")),
                        done_tss=round(done_tss), planned_tss=planned_tss,
                        done_hours=round(sum(v["done_min"] for v in by.values()) / 60, 1)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--today")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--strava-code")
    ap.add_argument("--plain")
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "athlete.json"))
    ap.add_argument("--state", default=os.path.join(ROOT, "state", "state.enc"))
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "data.enc"))
    args = ap.parse_args(argv)

    pw = os.environ.get("APP_PASSPHRASE")
    if not pw:
        sys.exit("APP_PASSPHRASE secret is not set")

    cfg = json.load(open(args.config))
    tz = ZoneInfo(cfg.get("timezone", "Australia/Sydney"))
    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(tz).date()
    state = crypto.read(args.state, pw, default={}) or {}
    logs = []
    log = lambda m: (logs.append(m), print(m))
    status = state.setdefault("sync_status", {})

    if args.strava_code:
        strava_token(state, log, code=args.strava_code.strip())

    new_acts, phys = [], {}
    if not args.offline:
        if os.environ.get("GARMIN_EMAIL") or state.get("tokens", {}).get("garmin"):
            try:
                acts, phys = fetch_garmin(state, today, log)
                new_acts += acts
                status["garmin"] = dict(ok=True, at=dt.datetime.now(tz).isoformat(timespec="minutes"))
            except Exception as e:
                traceback.print_exc()
                status["garmin"] = dict(ok=False, at=dt.datetime.now(tz).isoformat(timespec="minutes"),
                                        error=f"{type(e).__name__}: {str(e)[:160]}")
        else:
            status["garmin"] = dict(ok=False, error="Not set up yet")
        if os.environ.get("STRAVA_CLIENT_ID"):
            try:
                new_acts += fetch_strava(state, today, log)
                status["strava"] = dict(ok=True, at=dt.datetime.now(tz).isoformat(timespec="minutes"))
            except Exception as e:
                traceback.print_exc()
                status["strava"] = dict(ok=False, at=dt.datetime.now(tz).isoformat(timespec="minutes"),
                                        error=f"{type(e).__name__}: {str(e)[:160]}")
        else:
            status["strava"] = dict(ok=False, error="Not set up yet")

    cutoff = (dt.date.fromisoformat(cfg["plan_start"]) - dt.timedelta(days=120)).isoformat()
    state["activities"] = [a for a in merge_activities(state.get("activities", []), new_acts) if a["date"] >= cutoff]

    # Garmin's own threshold estimates replace the starting guesses once they exist.
    if phys:
        prev = state.get("physiology", {})
        changed = {k: v for k, v in phys.items() if prev.get(k) != v}
        if changed:
            log(f"Updated thresholds from Garmin: {changed}")
            state.setdefault("adjustments", []).append(dict(
                made=today.isoformat(), session_id=None, session_date=today.isoformat(), sport="all", before="",
                after="", change="Zones updated",
                reason="Garmin has new threshold numbers: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in changed.items())
                       + ". All future targets now use them."))
        state["physiology"] = {**prev, **phys}
    baselines = {**{k: v for k, v in state.get("physiology", {}).items()},
                 **{k: v for k, v in state.get("manual_baselines", {}).items()}}

    base = build_plan(cfg, baselines)
    res = Engine(base, state, cfg, today).run()

    race_day = dt.date.fromisoformat(cfg["race"]["date"])
    wellness = {k: v for k, v in sorted(state.get("wellness", {}).items()) if k >= (today - dt.timedelta(days=42)).isoformat()}
    bundle = dict(
        generated_at=dt.datetime.now(tz).isoformat(timespec="minutes"),
        today=today.isoformat(),
        athlete=dict(name=cfg["name"]),
        race=dict(cfg["race"], days_to_go=(race_day - today).days),
        plan_start=cfg["plan_start"],
        schedule_confirmed=cfg.get("schedule_confirmed", False),
        rest_day=cfg.get("rest_day"),
        zones=base["zones"],
        baselines=base["baselines"],
        weeks=summarise_weeks(base["weeks"], res["sessions"], res["extras"]),
        sessions=res["sessions"],
        extras=res["extras"],
        history=res["history"][-180:],
        projection=res["projection"],
        readiness=res["readiness"],
        form=res["form"],
        wellness=wellness,
        adjustments=list(reversed(state.get("adjustments", [])))[:60],
        sync=status,
        log=logs[-30:],
    )
    crypto.write(args.out, bundle, pw)
    crypto.write(args.state, state, pw)
    if args.plain:
        json.dump(bundle, open(args.plain, "w"), indent=1)
    print(f"OK — {len(res['sessions'])} sessions, readiness {res['readiness']['status']}, form {res['form']}")


if __name__ == "__main__":
    main()
