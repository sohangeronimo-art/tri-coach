"""Data sources: Garmin Connect (activities + recovery) and Strava (activities, backup).

Everything is normalised to:
  activity: {id, source, name, sport, start, date, duration_s, distance_m, avg_hr, max_hr,
             avg_power, np, tss_device}
  wellness: {date: {hrv, hrv_low, hrv_high, hrv_status, sleep_score, sleep_h, rhr,
                    readiness, readiness_level, body_battery}}
"""
import datetime as dt
import os

GARMIN_SPORT = {
    "running": "run", "trail_running": "run", "treadmill_running": "run", "track_running": "run",
    "indoor_running": "run", "virtual_run": "run",
    "cycling": "bike", "road_biking": "bike", "indoor_cycling": "bike", "virtual_ride": "bike",
    "gravel_cycling": "bike", "mountain_biking": "bike", "cyclocross": "bike", "bmx": "bike",
    "lap_swimming": "swim", "open_water_swimming": "swim", "swimming": "swim",
    "strength_training": "strength", "indoor_cardio": "strength", "hiit": "strength",
}

STRAVA_SPORT = {
    "Run": "run", "TrailRun": "run", "VirtualRun": "run",
    "Ride": "bike", "VirtualRide": "bike", "GravelRide": "bike", "MountainBikeRide": "bike", "EBikeRide": "bike",
    "Swim": "swim", "WeightTraining": "strength", "Workout": "strength", "Crossfit": "strength",
}


def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Garmin
# ---------------------------------------------------------------------------

def garmin_client(state, log):
    from garminconnect import Garmin  # installed in the GitHub Action

    email, pw = os.environ.get("GARMIN_EMAIL"), os.environ.get("GARMIN_PASSWORD")
    tokens = state.setdefault("tokens", {}).get("garmin") or os.environ.get("GARMIN_TOKENS")
    g = None
    if tokens:
        try:
            g = Garmin()
            g.login(tokens)
            log("Garmin: signed in with saved session")
        except Exception as e:  # stale tokens
            log(f"Garmin: saved session failed ({type(e).__name__}), using password")
            g = None
    if g is None:
        if not (email and pw):
            raise RuntimeError("GARMIN_EMAIL / GARMIN_PASSWORD secrets are not set")
        g = Garmin(email, pw)
        g.login()
        log("Garmin: signed in with password")
    saved = False
    for attr in ("garth", "client"):
        obj = getattr(g, attr, None)
        if obj is not None and hasattr(obj, "dumps"):
            try:
                state["tokens"]["garmin"] = obj.dumps()
                saved = True
                break
            except Exception as e:
                log(f"Garmin: couldn't save session via {attr} ({type(e).__name__})")
    if not saved:
        log("Garmin: session not saved; will sign in with password next run")
    return g


def garmin_activities(g, since, until):
    raw = g.get_activities_by_date(since.isoformat(), until.isoformat()) or []
    out = []
    for a in raw:
        key = (a.get("activityType") or {}).get("typeKey", "")
        start = (a.get("startTimeLocal") or "").replace("T", " ")[:19]
        if not start:
            continue
        out.append(dict(
            id=f"g{a.get('activityId')}", source="garmin", name=a.get("activityName") or key,
            sport=GARMIN_SPORT.get(key, "other"), start=start, date=start[:10],
            duration_s=_num(a.get("movingDuration")) or _num(a.get("duration")),
            distance_m=_num(a.get("distance")), avg_hr=_num(a.get("averageHR")), max_hr=_num(a.get("maxHR")),
            avg_power=_num(a.get("avgPower")), np=_num(a.get("normPower")),
            tss_device=_num(a.get("trainingStressScore")),
            te_aero=_num(a.get("aerobicTrainingEffect")), te_anaer=_num(a.get("anaerobicTrainingEffect")),
            te_label=a.get("trainingEffectLabel"), load=_num(a.get("activityTrainingLoad")),
            elev_m=_num(a.get("elevationGain")), calories=_num(a.get("calories")),
            cadence=_num(a.get("averageRunningCadenceInStepsPerMinute") or a.get("averageBikingCadenceInRevPerMinute")),
            swolf=_num(a.get("averageSwolf")), max_power=_num(a.get("maxPower")),
            vo2max=_num(a.get("vO2MaxValue")),
            zones=_zones_from_summary(a),
        ))
    return out


def _zones_from_summary(a):
    z = [_num(a.get(f"hrTimeInZone_{i}")) for i in range(1, 6)]
    return [round(v or 0) for v in z] if any(z) else None


def garmin_zones(g, acts, log, limit=8):
    """Fill HR time-in-zone for the most recent activities that don't have it."""
    n = 0
    for a in sorted(acts, key=lambda a: a["start"], reverse=True)[:limit]:
        if a.get("zones") or a.get("source") != "garmin" or not a.get("avg_hr"):
            continue
        try:
            rows = g.get_activity_hr_in_timezones(a["id"][1:]) or []
            rows = sorted(rows, key=lambda r: r.get("zoneNumber", 0))
            secs = [round(_num(r.get("secsInZone")) or 0) for r in rows][:5]
            if any(secs):
                a["zones"] = secs + [0] * (5 - len(secs))
                a["zone_floor_bpm"] = [_num(r.get("zoneLowBoundary")) for r in rows][:5]
                n += 1
        except Exception as e:
            log(f"Garmin zones {a['id']}: {type(e).__name__}")
    if n:
        log(f"Garmin: HR zones for {n} activities")


def garmin_wellness_day(g, day, log):
    d = day.isoformat()
    w = {}
    try:
        h = g.get_hrv_data(d) or {}
        s = h.get("hrvSummary") or {}
        w["hrv"] = _num(s.get("lastNightAvg"))
        w["hrv_status"] = s.get("status")
        base = s.get("baseline") or {}
        w["hrv_low"] = _num(base.get("balancedLow"))
        w["hrv_high"] = _num(base.get("balancedUpper"))
    except Exception as e:
        log(f"Garmin HRV {d}: {type(e).__name__}")
    try:
        sl = (g.get_sleep_data(d) or {}).get("dailySleepDTO") or {}
        w["sleep_h"] = round((_num(sl.get("sleepTimeSeconds")) or 0) / 3600, 2) or None
        w["sleep_score"] = _num(((sl.get("sleepScores") or {}).get("overall") or {}).get("value"))
    except Exception as e:
        log(f"Garmin sleep {d}: {type(e).__name__}")
    try:
        st = g.get_stats(d) or {}
        w["rhr"] = _num(st.get("restingHeartRate"))
        w["body_battery"] = _num(st.get("bodyBatteryHighestValue"))
    except Exception as e:
        log(f"Garmin stats {d}: {type(e).__name__}")
    try:
        tr = g.get_training_readiness(d)
        tr = tr[0] if isinstance(tr, list) and tr else (tr or {})
        w["readiness"] = _num(tr.get("score"))
        w["readiness_level"] = tr.get("level")
    except Exception:
        pass  # not every watch has Training Readiness
    return {k: v for k, v in w.items() if v is not None}


def garmin_physiology(g, log):
    """Best-effort pull of FTP / threshold values Garmin has estimated."""
    found = {}
    try:
        lt = g.get_lactate_threshold(latest=True) or {}
        shr = lt.get("speed_and_heart_rate") or {}
        if shr.get("heartRate"):
            found["run_lthr_bpm"] = int(shr["heartRate"])
        sp = _num(shr.get("speed"))
        if sp:
            mps = sp * 10 if sp < 1.5 else sp
            pace = 1000 / mps
            if 180 < pace < 480:
                found["run_threshold_pace_s_per_km"] = round(pace)
        # NB: lt["power"] is RUNNING power at threshold, not cycling FTP, so it's ignored.
    except Exception as e:
        log(f"Garmin threshold lookup unavailable ({type(e).__name__})")
    try:
        ud = (g.get_user_profile() or {}).get("userData") or {}
        if ud.get("lactateThresholdHeartRate") and "run_lthr_bpm" not in found:
            found["run_lthr_bpm"] = int(ud["lactateThresholdHeartRate"])
    except Exception:
        pass
    return found


def fetch_garmin(state, today, log, history_days=60):
    g = garmin_client(state, log)
    last = state.get("last_garmin_sync")
    if state.get("schema", 1) < 2:      # new activity fields: refetch history once
        last = None
        state["schema"] = 2
    since = today - dt.timedelta(days=history_days if not last else 10)
    acts = garmin_activities(g, since, today)
    log(f"Garmin: {len(acts)} activities since {since}")
    garmin_zones(g, acts, log)

    well = state.setdefault("wellness", {})
    days = history_days if not last else 4
    for k in range(days, -1, -1):
        d = today - dt.timedelta(days=k)
        got = garmin_wellness_day(g, d, log)
        if got:
            well.setdefault(d.isoformat(), {}).update(got)
    try:
        bc = g.get_body_composition(since.isoformat(), today.isoformat()) or {}
        n = 0
        for e in bc.get("dateWeightList") or []:
            day = e.get("calendarDate")
            if not day and e.get("date"):
                day = dt.datetime.fromtimestamp(e["date"] / 1000).date().isoformat()
            w = _num(e.get("weight"))
            if day and w:
                well.setdefault(day[:10], {})["weight_kg"] = round(w / 1000 if w > 1000 else w, 2)
                n += 1
        log(f"Garmin: {n} weigh-ins")
    except Exception as e:
        log(f"Garmin weight unavailable ({type(e).__name__})")
    phys = garmin_physiology(g, log)
    state["last_garmin_sync"] = dt.datetime.now().isoformat(timespec="minutes")
    return acts, phys


# ---------------------------------------------------------------------------
# Strava
# ---------------------------------------------------------------------------

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"


def strava_token(state, log, code=None):
    import requests
    cid, secret = os.environ.get("STRAVA_CLIENT_ID"), os.environ.get("STRAVA_CLIENT_SECRET")
    if not (cid and secret):
        raise RuntimeError("STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET secrets are not set")
    tok = state.setdefault("tokens", {}).get("strava") or {}
    if code:
        data = dict(client_id=cid, client_secret=secret, code=code, grant_type="authorization_code")
    else:
        rt = tok.get("refresh_token") or os.environ.get("STRAVA_REFRESH_TOKEN")
        if not rt:
            raise RuntimeError("Strava not connected yet (run the 'Connect Strava' workflow)")
        if tok.get("access_token") and tok.get("expires_at", 0) > dt.datetime.now().timestamp() + 300:
            return tok["access_token"]
        data = dict(client_id=cid, client_secret=secret, refresh_token=rt, grant_type="refresh_token")
    r = requests.post(STRAVA_TOKEN_URL, data=data, timeout=30)
    r.raise_for_status()
    j = r.json()
    state["tokens"]["strava"] = dict(access_token=j["access_token"], refresh_token=j["refresh_token"],
                                     expires_at=j["expires_at"])
    log("Strava: token refreshed" if not code else "Strava: connected")
    return j["access_token"]


def fetch_strava(state, today, log, history_days=60):
    import requests
    token = strava_token(state, log)
    last = state.get("last_strava_sync")
    since = today - dt.timedelta(days=history_days if not last else 10)
    after = int(dt.datetime.combine(since, dt.time()).timestamp())
    out, page = [], 1
    while page < 10:
        r = requests.get("https://www.strava.com/api/v3/athlete/activities",
                         headers={"Authorization": f"Bearer {token}"},
                         params={"after": after, "per_page": 100, "page": page}, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for a in batch:
            start = (a.get("start_date_local") or "").replace("T", " ").replace("Z", "")[:19]
            out.append(dict(
                id=f"s{a['id']}", source="strava", name=a.get("name"),
                sport=STRAVA_SPORT.get(a.get("sport_type") or a.get("type"), "other"),
                start=start, date=start[:10], duration_s=_num(a.get("moving_time")),
                distance_m=_num(a.get("distance")), avg_hr=_num(a.get("average_heartrate")),
                max_hr=_num(a.get("max_heartrate")), avg_power=_num(a.get("average_watts")),
                np=_num(a.get("weighted_average_watts")), tss_device=None,
                elev_m=_num(a.get("total_elevation_gain")),
                cadence=(_num(a.get("average_cadence")) or 0) * (2 if a.get("sport_type") in ("Run", "TrailRun") else 1) or None,
                max_power=_num(a.get("max_watts")),
            ))
        page += 1
    state["last_strava_sync"] = dt.datetime.now().isoformat(timespec="minutes")
    log(f"Strava: {len(out)} activities since {since}")
    return out


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge_activities(existing, new):
    """Merge new activities into the stored list, de-duplicating Garmin vs Strava copies
    (same sport, start within 20 minutes). Garmin wins because it carries more detail."""
    by_id = {a["id"]: a for a in existing}
    for a in new:
        prev = by_id.get(a["id"], {})
        by_id[a["id"]] = {**prev, **{k: v for k, v in a.items() if v is not None or k not in prev}}
    acts = sorted(by_id.values(), key=lambda a: a["start"])

    def ts(a):
        return dt.datetime.fromisoformat(a["start"]).timestamp()

    keep = []
    for a in acts:
        dup = None
        for k in keep[-12:]:
            if k["sport"] == a["sport"] and abs(ts(k) - ts(a)) <= 1200:
                dup = k
                break
        if dup is None:
            keep.append(a)
            continue
        if dup["source"] == "strava" and a["source"] == "garmin":
            a["alt_id"] = dup["id"]
            keep[keep.index(dup)] = a
        else:
            dup["alt_id"] = a["id"]
    return keep
