"""Per-activity breakdowns + an overview of recent training.

Every activity gets a rule-based coach read built from its own numbers. If an
ANTHROPIC_API_KEY secret is set, Claude also writes a short read of each new
activity (cached, so each activity is only sent once).
"""
import datetime as dt
import json
import os
import statistics as st

from .zones import fmt_pace

STIMULUS = {
    "recovery":  ("Recovery", "Easy blood flow. Helps you absorb harder days; adds almost no fatigue."),
    "base":      ("Aerobic base", "Builds the engine Olympic racing runs on: more mitochondria and capillaries, better fat burning, "
                                  "a heart that pumps more per beat. Low cost, so you can stack lots of it."),
    "tempo":     ("Tempo", "Muscular endurance. Lifts the pace you can hold for an hour or more. Moderate fatigue: "
                           "good in small doses, a trap if every session drifts here."),
    "threshold": ("Threshold", "Raises your lactate threshold, which is roughly the effort you hold through an Olympic bike and run. "
                               "One of the highest-value sessions for your race, but it needs 24–48h before the next hard one."),
    "vo2":       ("VO2 max", "Raises your ceiling, the top-end aerobic power everything else sits under. High cost; "
                             "1–2 a week at most."),
    "anaerobic": ("Anaerobic", "Short, very hard efforts. Sharpens speed and surges, but small direct value for an Olympic-distance race."),
    "strength":  ("Strength", "Stronger tendons and muscles: fewer injuries, better economy late in the run, and it holds onto muscle "
                              "while you're cutting weight."),
}

TE_LABEL = {"RECOVERY": "recovery", "BASE": "base", "AEROBIC_BASE": "base", "TEMPO": "tempo",
            "LACTATE_THRESHOLD": "threshold", "THRESHOLD": "threshold", "VO2MAX": "vo2",
            "ANAEROBIC_CAPACITY": "anaerobic", "SPRINT": "anaerobic"}

EASY_KINDS = {"easy", "endurance", "long", "technique", "recovery"}
HARD_KINDS = {"threshold", "vo2", "race_pace", "sweetspot", "test"}


def _f(x, n=0):
    return f"{x:.{n}f}" if x is not None else "–"


def hm(sec):
    m = round((sec or 0) / 60)
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"


def stimulus(a):
    if a["sport"] == "strength":
        return "strength", "type"
    if a.get("te_label") and TE_LABEL.get(str(a["te_label"]).upper()):
        return TE_LABEL[str(a["te_label"]).upper()], "Garmin training effect"
    z = a.get("zones")
    if z and sum(z) > 0:
        tot = sum(z)
        p = [v / tot for v in z]
        if p[4] >= 0.08:
            return "vo2", "time in HR zones"
        if p[3] >= 0.15:
            return "threshold", "time in HR zones"
        if p[2] >= 0.2:
            return "tempo", "time in HR zones"
        return ("recovery" if p[0] >= 0.6 and (a.get("duration_s") or 0) < 2700 else "base"), "time in HR zones"
    hrs = (a.get("duration_s") or 0) / 3600
    if hrs and a.get("tss"):
        i = (a["tss"] / hrs / 100) ** 0.5
        if i < 0.6:
            return "recovery", "intensity"
        if i < 0.78:
            return "base", "intensity"
        if i < 0.87:
            return "tempo", "intensity"
        if i < 0.97:
            return "threshold", "intensity"
        return "vo2", "intensity"
    return "base", "duration"


def headline_stats(a, base):
    s, d, t = a["sport"], a.get("distance_m") or 0, a.get("duration_s") or 0
    out = []
    if s == "run" and d > 0:
        out += [("Distance", f"{d / 1000:.2f} km"), ("Pace", f"{fmt_pace(t / (d / 1000))}/km")]
        if a.get("cadence"):
            out.append(("Cadence", f"{a['cadence']:.0f} spm"))
    elif s == "bike" and d > 0:
        out += [("Distance", f"{d / 1000:.1f} km"), ("Speed", f"{d / 1000 / (t / 3600):.1f} km/h")]
        if a.get("np") or a.get("avg_power"):
            out.append(("Power", f"{(a.get('np') or a.get('avg_power')):.0f} W" + (" NP" if a.get("np") else "")))
    elif s == "swim" and d > 0:
        out += [("Distance", f"{d:.0f} m"), ("Pace", f"{fmt_pace(t / (d / 100))}/100m")]
        if a.get("swolf"):
            out.append(("SWOLF", f"{a['swolf']:.0f}"))
    out.append(("Time", hm(t)))
    if a.get("avg_hr"):
        out.append(("Avg HR", f"{a['avg_hr']:.0f} bpm"))
    if a.get("elev_m") and s in ("run", "bike") and a["elev_m"] > 20:
        out.append(("Climbing", f"{a['elev_m']:.0f} m"))
    out.append(("Load", f"{a.get('tss') or 0:.0f} TSS"))
    return out[:6]


def efficiency(a, history):
    """Output per heartbeat vs similar recent sessions. Higher = fitter (or a cooler, flatter day)."""
    def ef(x):
        if not (x.get("avg_hr") and x.get("duration_s") and x["duration_s"] >= 1200):
            return None
        if x["sport"] == "bike" and x.get("np"):
            return x["np"] / x["avg_hr"]
        if x["sport"] == "run" and x.get("distance_m"):
            return (x["distance_m"] / (x["duration_s"] / 60)) / x["avg_hr"]
        return None
    me = ef(a)
    if me is None:
        return None
    start = (dt.date.fromisoformat(a["date"]) - dt.timedelta(days=60)).isoformat()
    past = [ef(x) for x in history if x["sport"] == a["sport"] and x["id"] != a["id"] and start <= x["date"] < a["date"]]
    past = [p for p in past if p]
    if len(past) < 3:
        return None
    med = st.median(past)
    return round((me / med - 1) * 100, 1), len(past)


def recovery_hours(a, stim):
    t = a.get("tss") or 0
    if stim in ("recovery",) or t < 40:
        return 12
    if t < 80 and stim == "base":
        return 18
    if t < 100:
        return 24
    if t < 160:
        return 36
    return 48


def plan_match(a, sessions):
    for s in sessions:
        if (s.get("completed") or {}).get("activity_id") in (a["id"], a.get("alt_id")):
            return s
    return None


def read_activity(a, history, sessions, base, ctl):
    stim, how = stimulus(a)
    name, what = STIMULUS[stim]
    notes = []
    hrs = (a.get("duration_s") or 0) / 3600

    # load in context
    if ctl and a.get("tss"):
        ratio = a["tss"] / max(ctl, 1)
        if ratio >= 1.8:
            notes.append(f"Big day: {a['tss']:.0f} TSS is {ratio:.1f}× your average daily load ({ctl:.0f}). Bank sleep and carbs tonight.")
        elif ratio <= 0.5:
            notes.append(f"Light day for you: {a['tss']:.0f} TSS against an average of {ctl:.0f} a day.")

    # zones
    z = a.get("zones")
    if z and sum(z):
        tot = sum(z)
        easy, mod, hard = (z[0] + z[1]) / tot, z[2] / tot, (z[3] + z[4]) / tot
        notes.append(f"{easy:.0%} easy (Z1–2), {mod:.0%} moderate (Z3), {hard:.0%} hard (Z4–5).")

    # vs plan
    s = plan_match(a, sessions)
    if s:
        pct = (a.get("duration_s") or 0) / 60 / max(1, s["duration_min"])
        line = f"Planned: {s['title']} ({s['duration_min']} min). You did {pct:.0%} of the time."
        if s["kind"] in EASY_KINDS and (stim in ("tempo", "threshold", "vo2") or (z and sum(z) and (z[2] + z[3] + z[4]) / sum(z) > 0.25)):
            line += " This was meant to be easy and it wasn't. Easy days done too hard leave you flat for the sessions that matter."
        elif s["kind"] in HARD_KINDS and stim in ("base", "recovery"):
            line += " It was a hard session on paper but came out easy. Check you actually hit the intervals."
        elif 0.9 <= pct <= 1.15:
            line += " On target."
        notes.append(line)

    # efficiency
    e = efficiency(a, history)
    if e:
        pct, n = e
        unit = "watts" if a["sport"] == "bike" else "pace"
        if pct >= 3:
            notes.append(f"Efficiency up {pct:.0f}%: more {unit} per heartbeat than your median of the last {n} similar sessions. "
                         "Usually a sign the aerobic engine is improving (or a cool, flat day).")
        elif pct <= -4:
            notes.append(f"Efficiency down {abs(pct):.0f}% vs your last {n} similar sessions: heart rate high for the output. "
                         "Heat, hills, fatigue or getting sick all do this. Worth a look if it repeats.")
        else:
            notes.append(f"Efficiency in line with your last {n} similar sessions ({pct:+.0f}%).")

    # sport specifics
    if a["sport"] == "run" and a.get("cadence") and a["cadence"] < 164 and hrs > 0.25:
        notes.append(f"Cadence {a['cadence']:.0f} spm is on the low side. Nudging toward 170+ usually means shorter, "
                     "cheaper strides, especially off the bike.")
    if a["sport"] == "swim" and a.get("distance_m") and a.get("duration_s"):
        pace = a["duration_s"] / (a["distance_m"] / 100)
        css = base.get("swim_css_s_per_100m") or 105
        notes.append(f"Average {fmt_pace(pace)}/100m including rests, vs your CSS of {fmt_pace(css)}.")
    if a.get("te_aero"):
        notes.append(f"Garmin training effect: aerobic {a['te_aero']:.1f}, anaerobic {(a.get('te_anaer') or 0):.1f} "
                     "(0–5; 2–3 maintains, 3–4 improves, 4+ highly improving).")

    rec = recovery_hours(a, stim)
    return dict(
        id=a["id"], name=a.get("name"), sport=a["sport"], date=a["date"], start=a.get("start"),
        stimulus=stim, stimulus_name=name, stimulus_source=how, what_it_did=what,
        stats=[{"label": k, "value": v} for k, v in headline_stats(a, base)],
        zones=z, notes=notes, recovery_hours=rec,
        planned=s["title"] if s else None,
        headline=f"{name} · {hm(a.get('duration_s'))} · {a.get('tss') or 0:.0f} TSS",
    )


def overview(acts, today, ctl):
    last14 = [a for a in acts if a["date"] >= (today - dt.timedelta(days=13)).isoformat()]
    z = [0] * 5
    for a in last14:
        for i, v in enumerate(a.get("zones") or []):
            z[i] += v
    out = []
    if sum(z) > 3600:
        tot = sum(z)
        easy, hard = (z[0] + z[1]) / tot, (z[3] + z[4]) / tot
        mid = z[2] / tot
        if easy < 0.7:
            out.append(f"Last 2 weeks: only {easy:.0%} of your time was easy. Endurance athletes improve fastest around 80% easy. "
                       f"Your {mid:.0%} in Z3 is the grey zone: tiring, but not hard enough to drive big gains.")
        else:
            out.append(f"Last 2 weeks: {easy:.0%} easy / {mid:.0%} moderate / {hard:.0%} hard. That's a healthy spread.")
    days = len({a["date"] for a in last14})
    by = {}
    for a in last14:
        by[a["sport"]] = by.get(a["sport"], 0) + (a.get("duration_s") or 0)
    if days:
        mix = ", ".join(f"{k} {hm(v)}" for k, v in sorted(by.items(), key=lambda kv: -kv[1]))
        out.append(f"{days} active days in the last 2 weeks: {mix}.")
    for sport in ("swim", "bike", "run"):
        lastd = max([a["date"] for a in acts if a["sport"] == sport], default=None)
        if lastd and (today - dt.date.fromisoformat(lastd)).days >= 10:
            out.append(f"No {sport} in {(today - dt.date.fromisoformat(lastd)).days} days. "
                       + ("Swim feel fades fastest; even one easy swim a week keeps it." if sport == "swim" else "Worth one easy session to keep it ticking."))
    return dict(zones14=z if sum(z) else None, notes=out[:3])


def ai_read(a, read, ctx, cache, log):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    if a["id"] in cache:
        return cache[a["id"]]
    try:
        import requests
        prompt = {
            "athlete": ctx, "activity": {k: a.get(k) for k in ("name", "sport", "date", "duration_s", "distance_m", "avg_hr",
                                                                 "max_hr", "np", "avg_power", "cadence", "elev_m", "tss",
                                                                 "te_aero", "te_anaer", "te_label", "zones", "swolf")},
            "computed": {k: read[k] for k in ("stimulus_name", "notes", "planned", "recovery_hours")},
        }
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"), "max_tokens": 350,
                  "system": ("You are a direct, knowledgeable triathlon coach. In 3–4 short sentences, say what this session "
                             "actually did for the athlete given their race goal and current block, one thing they did well or "
                             "should change, and anything in the numbers worth watching. Plain language, no fluff, no "
                             "cheerleading, no headings, no emojis. Only use numbers that appear in the data."),
                  "messages": [{"role": "user", "content": json.dumps(prompt)}]})
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()
        if text:
            cache[a["id"]] = text
        return text or None
    except Exception as e:
        log(f"AI read failed for {a['id']}: {type(e).__name__}")
        return None


def build(state, sessions, base, cfg, today, history, log):
    acts = [a for a in state.get("activities", []) if a["sport"] in ("swim", "bike", "run", "strength", "other")]
    acts.sort(key=lambda a: a["start"], reverse=True)
    ctl = history[-1]["ctl"] if history else None
    cache = state.setdefault("ai_reads", {})
    ctx = {"race": cfg["race"]["name"], "race_date": cfg["race"]["date"], "fitness_ctl": ctl,
           "goal": "be at peak for race day", "body_goal": cfg.get("body")}
    reads = []
    for a in acts[:5]:
        r = read_activity(a, acts, sessions, base, ctl)
        r["ai"] = ai_read(a, r, ctx, cache, log)
        reads.append(r)
    # keep the AI cache small
    keep = {a["id"] for a in acts[:30]}
    state["ai_reads"] = {k: v for k, v in cache.items() if k in keep}
    last5 = acts[:5]
    return dict(
        activities=reads,
        totals=dict(time=hm(sum(a.get("duration_s") or 0 for a in last5)),
                    tss=round(sum(a.get("tss") or 0 for a in last5)),
                    km=round(sum((a.get("distance_m") or 0) for a in last5 if a["sport"] in ("run", "bike")) / 1000, 1),
                    swim_m=round(sum((a.get("distance_m") or 0) for a in last5 if a["sport"] == "swim")),
                    since=last5[-1]["date"] if last5 else None),
        overview=overview(acts, today, ctl),
        ai_enabled=bool(os.environ.get("ANTHROPIC_API_KEY")),
    )
