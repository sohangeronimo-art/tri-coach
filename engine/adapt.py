"""The adaptive engine.

Takes the base plan + what actually happened (activities, recovery data) and
produces the live plan. Every change it makes is logged with a plain-English
reason so you can see why the plan moved.

Changes are stored as 'overrides' keyed by session id, so they persist between
daily runs and each rule only fires once per trigger.
"""
import copy
import datetime as dt
import statistics as stats

from .load import activity_tss, pmc
from .plan_builder import tss as est_tss

HARD = {"threshold", "vo2", "race_pace", "sweetspot", "tempo", "test", "brick", "long"}


def d(s):
    return dt.date.fromisoformat(s)


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

def readiness(wellness, day):
    today = wellness.get(day.isoformat()) or {}
    hist = [wellness.get((day - dt.timedelta(days=k)).isoformat()) or {} for k in range(1, 31)]
    factors = []

    if not today:
        return dict(score=None, status="unknown", source=None, factors=[], date=day.isoformat())

    score, source = 75.0, "composite"

    hrv = today.get("hrv")
    if hrv:
        lo, hi = today.get("hrv_low"), today.get("hrv_high")
        past = [h["hrv"] for h in hist if h.get("hrv")]
        if not (lo and hi) and len(past) >= 7:
            m, sd = stats.mean(past), stats.pstdev(past) or 1
            lo, hi = m - sd, m + sd
        note = ""
        if lo and hi:
            if hrv < lo:
                score -= 25
                note = f"below your normal {lo:.0f}–{hi:.0f}"
            elif hrv > hi * 1.15:
                score -= 5
                note = f"unusually high vs {lo:.0f}–{hi:.0f}"
            else:
                note = f"in your normal {lo:.0f}–{hi:.0f}"
        factors.append(dict(label="HRV", value=f"{hrv:.0f}ms", note=note,
                            flag="bad" if note.startswith("below") else "ok"))

    rhr = today.get("rhr")
    past_rhr = [h["rhr"] for h in hist if h.get("rhr")]
    if rhr and len(past_rhr) >= 5:
        delta = rhr - stats.mean(past_rhr)
        if delta > 5:
            score -= 15
        elif delta > 3:
            score -= 8
        factors.append(dict(label="Resting HR", value=f"{rhr:.0f}bpm",
                            note=f"{delta:+.0f} vs 30-day avg", flag="bad" if delta > 3 else "ok"))

    sl = today.get("sleep_score")
    if sl:
        if sl < 50:
            score -= 20
        elif sl < 65:
            score -= 10
        elif sl >= 80:
            score += 5
        hrs = today.get("sleep_h")
        factors.append(dict(label="Sleep", value=f"{sl:.0f}/100", note=f"{hrs:.1f}h" if hrs else "",
                            flag="bad" if sl < 65 else "ok"))

    if today.get("readiness") is not None:
        score, source = today["readiness"], "garmin"
        factors.insert(0, dict(label="Garmin readiness", value=f"{score:.0f}",
                               note=(today.get("readiness_level") or "").replace("_", " ").lower(), flag="ok"))
    if today.get("body_battery"):
        factors.append(dict(label="Body Battery", value=f"{today['body_battery']:.0f}", note="peak today", flag="ok"))

    score = max(0, min(100, score))
    status = "red" if score < 35 else ("amber" if score < 55 else "green")
    return dict(score=round(score), status=status, source=source, factors=factors, date=day.isoformat())


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def easy_version(s, factor=0.6, why="Recovery is low"):
    mins = max(20, int(5 * round(s["duration_min"] * factor / 5)))
    sport = s["sport"]
    detail = {
        "run": "Easy jog, Z1–Z2, conversational. Stop early if you feel flat.",
        "bike": "Easy spin, Z1–Z2, high cadence. Nothing hard.",
        "swim": "Easy aerobic swim + a few drills. No hard sets.",
        "strength": "Mobility and light core only.",
    }.get(sport, "Easy, Z1–Z2.")
    return dict(title=f"{mins} min Easy {sport.capitalize()}", kind="easy", duration_min=mins, key=False,
                summary=f"Swapped to easy — {why.lower()}", steps=[{"label": "Easy", "detail": detail}],
                tss=est_tss(mins, "easy"), distance_m=None)


def content_of(s):
    keys = ("title", "kind", "duration_min", "key", "summary", "steps", "tss", "distance_m", "test")
    return {k: copy.deepcopy(s.get(k)) for k in keys}


def scaled(s, factor, why):
    mins = max(15, int(5 * round(s["duration_min"] * factor / 5)))
    steps = [{"label": "Note", "detail": why}] + copy.deepcopy(s["steps"])
    title = s["title"]
    return dict(duration_min=mins, tss=est_tss(mins, s["kind"]), steps=steps,
                title=title, summary=(s.get("summary") or "") + f" · cut to {mins} min")


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class Engine:
    def __init__(self, base, state, cfg, today):
        self.base = base
        self.state = state
        self.cfg = cfg
        self.today = today
        self.ov = state.setdefault("overrides", {})
        self.fired = set(state.setdefault("fired", []))
        self.log = state.setdefault("adjustments", [])
        self.baselines = base["baselines"]

    # ----- core ------------------------------------------------------------
    def live_sessions(self):
        out = []
        for s in self.base["sessions"]:
            s = copy.deepcopy(s)
            o = self.ov.get(s["id"])
            if o:
                s["original_title"] = s["title"]
                s.update(copy.deepcopy(o["set"]))
                s["adjusted"] = o.get("reason")
            out.append(s)
        return out

    def note(self, s, change, reason, after=None):
        self.log.append(dict(made=self.today.isoformat(), session_id=s["id"], session_date=s["date"],
                             sport=s["sport"], before=s["title"], after=after or s["title"], change=change,
                             reason=reason))

    def set_override(self, s, fields, reason, rule="other"):
        self.ov[s["id"]] = {"set": fields, "reason": reason, "rule": rule}

    def locked(self, s):
        """Weekly volume tweaks can be overridden by day-to-day rules; anything else can't."""
        o = self.ov.get(s["id"])
        return bool(o) and o.get("rule") != "weekly"

    def match(self, sessions, acts):
        """Attach completed activities to planned sessions (same day, same sport)."""
        by_day = {}
        for a in acts:
            by_day.setdefault((a["date"], a["sport"]), []).append(a)
        used = set()
        for s in sorted(sessions, key=lambda s: (s["date"], -s["duration_min"])):
            sport = "bike" if s["sport"] == "race" else s["sport"]
            cands = [a for a in by_day.get((s["date"], sport), []) if a["id"] not in used]
            if s["sport"] == "race":
                cands = [a for a in acts if a["date"] == s["date"] and a["id"] not in used]
            if cands:
                best = min(cands, key=lambda a: abs((a["duration_s"] or 0) / 60 - s["duration_min"]))
                used.add(best["id"])
                mins = (best["duration_s"] or 0) / 60
                s["completed"] = dict(activity_id=best["id"], minutes=round(mins), distance_m=best.get("distance_m"),
                                      tss=best.get("tss"), avg_hr=best.get("avg_hr"), name=best.get("name"))
                s["status"] = "done" if mins >= 0.6 * s["duration_min"] else "partial"
            elif d(s["date"]) < self.today:
                s["status"] = "missed"
            else:
                s["status"] = "planned"
        extras = [a for a in acts if a["id"] not in used and d(a["date"]) >= d(self.cfg["plan_start"])]
        return extras

    def week_of(self, day):
        for w in self.base["weeks"]:
            if d(w["start"]) <= day <= d(w["end"]):
                return w
        return None

    # ----- rules -----------------------------------------------------------
    def rule_readiness(self, live, ready):
        key = f"ready:{self.today}"
        if ready["status"] not in ("red", "amber") or key in self.fired:
            return
        self.fired.add(key)
        todays = [s for s in live if s["date"] == self.today.isoformat() and s["status"] == "planned"
                  and s["kind"] in HARD and not self.locked(s)]
        why = ", ".join(f"{f['label']} {f['value']} ({f['note']})" for f in ready["factors"] if f["flag"] == "bad") \
              or f"readiness {ready['score']}"
        for s in todays:
            if ready["status"] == "red":
                moved = self.reschedule(live, s, f"moved from {d(s['date']).strftime('%a')} — low recovery")
                ev = easy_version(s, 0.6, "Recovery is low")
                self.set_override(s, ev, f"Low recovery: {why}", "readiness")
                self.note(s, "Swapped to easy" + (f", moved to {moved}" if moved else ""),
                          f"Recovery is red today ({why}). " +
                          (f"The {s['title']} moves to {moved}." if moved else "No good slot to move it to this week, so it's skipped."),
                          ev["title"])
            else:
                f = 0.8
                sc = scaled(s, f, "Recovery is a bit down — drop one rep from the main set and keep it controlled.")
                self.set_override(s, sc, f"Recovery amber: {why}", "readiness")
                self.note(s, "Trimmed 20%", f"Recovery is amber today ({why}). Same session, a bit less of it.")

    def reschedule(self, live, s, reason):
        """Move a key session's content into a later easy session of the same sport this week."""
        week = self.week_of(d(s["date"]))
        if not week:
            return None
        rest = self.cfg.get("rest_day", "Sat")
        key_days = {x["date"] for x in live if x.get("key") and x["status"] == "planned" and x["id"] != s["id"]}
        for c in sorted(live, key=lambda x: x["date"]):
            cd = d(c["date"])
            if c["sport"] != s["sport"] or c.get("key") or c["status"] != "planned" or c["id"] == s["id"]:
                continue
            if not (max(self.today + dt.timedelta(days=1), d(week["start"])) <= cd <= d(week["end"])):
                continue
            if cd.strftime("%a") == rest or self.locked(c):
                continue
            nb = {(cd - dt.timedelta(days=1)).isoformat(), (cd + dt.timedelta(days=1)).isoformat()}
            if nb & key_days and len([x for x in live if x["date"] in nb and x.get("key") and x["sport"] == s["sport"]]):
                continue
            content = content_of(s)
            content["summary"] = (content.get("summary") or "") + f" · {reason}"
            self.set_override(c, content, reason, "moved")
            c.update(content)
            c["key"] = True
            return cd.strftime("%a %-d %b")
        return None

    def rule_missed(self, live):
        week = self.week_of(self.today)
        if not week:
            return
        for s in live:
            sd = d(s["date"])
            if not (d(week["start"]) <= sd < self.today) or s["status"] != "missed" or not s.get("key"):
                continue
            key = f"missed:{s['id']}"
            if key in self.fired:
                continue
            self.fired.add(key)
            moved = self.reschedule(live, s, f"moved from {sd.strftime('%a')} (missed)")
            if moved:
                self.note(s, f"Moved to {moved}", f"You missed {s['title']}. It's a key session, so it replaces an easy "
                          f"{s['sport']} later this week.")
            else:
                self.note(s, "Dropped", f"You missed {s['title']} and there's no safe slot left this week. "
                          "Let it go — don't cram it.")

    def rule_fatigue(self, live, form):
        if form is None or form > -30:
            return
        recent = [f for f in self.fired if f.startswith("fatigue:")]
        if any(d(f.split(":", 1)[1]) > self.today - dt.timedelta(days=3) for f in recent):
            return
        self.fired.add(f"fatigue:{self.today}")
        window = {(self.today + dt.timedelta(days=k)).isoformat() for k in range(3)}
        n = 0
        for s in live:
            if s["date"] in window and s["status"] == "planned" and not self.locked(s) and s["sport"] != "race":
                f = 0.85 if s.get("key") else 0.7
                self.set_override(s, scaled(s, f, f"Fatigue is high (form {form:.0f}) — shorter session."),
                                  f"Form {form:.0f}", "fatigue")
                n += 1
        if n:
            self.log.append(dict(made=self.today.isoformat(), session_id=None, session_date=self.today.isoformat(),
                                 sport="all", before="", after="", change=f"Next 3 days trimmed ({n} sessions)",
                                 reason=f"Form is {form:.0f} — you're carrying a lot of fatigue. Easing the next 3 days "
                                        "so it turns into fitness instead of a hole."))

    def rule_weekly(self, live, series):
        week = self.week_of(self.today)
        if not week or week["index"] == 1 or week["phase"] in ("taper", "race"):
            return
        key = f"week:{week['index']}"
        if key in self.fired:
            return
        self.fired.add(key)
        prev = [w for w in self.base["weeks"] if w["index"] in (week["index"] - 1, week["index"] - 2)]
        comps = []
        for w in prev:
            ss = [s for s in live if w["start"] <= s["date"] <= w["end"]]
            planned = sum(s["tss"] for s in ss) or 1
            done = sum((s.get("completed") or {}).get("tss") or 0 for s in ss)
            comps.append((w["index"], done / planned))
        comps.sort()
        last = comps[-1][1] if comps else 1

        ctl_now = series.get(self.today.isoformat(), {}).get("ctl")
        ctl_wk = series.get((self.today - dt.timedelta(days=7)).isoformat(), {}).get("ctl")
        ramp = (ctl_now - ctl_wk) if (ctl_now is not None and ctl_wk is not None) else 0

        well = self.state.get("wellness", {})
        low_hrv = sum(1 for k in range(1, 8)
                      if (well.get((self.today - dt.timedelta(days=k)).isoformat()) or {}).get("hrv_status")
                      in ("LOW", "UNBALANCED", "POOR"))

        factor, reason = 1.0, None
        if last < 0.6:
            factor, reason = 0.85, f"Last week you hit {last:.0%} of planned load. Easing back in rather than jumping straight to this week's full load."
        elif ramp > 8:
            factor, reason = 0.9, f"Fitness ramped {ramp:.1f}/week — too steep to hold safely. Taking 10% off."
        elif low_hrv >= 4:
            factor, reason = 0.85, f"HRV was low or unbalanced {low_hrv} of the last 7 days. Taking 15% off this week."
        elif (not week["recovery"] and week["phase"] != "foundation" and len(comps) == 2 and all(c >= 0.95 for _, c in comps)
              and low_hrv == 0 and ramp < 6):
            factor, reason = 1.05, "Two solid weeks (95%+ done) and recovery is holding. Nudging this week up 5%."
        if factor == 1.0:
            return
        n = 0
        for s in live:
            if week["start"] <= s["date"] <= week["end"] and s["status"] == "planned" and not s.get("test") \
                    and not self.ov.get(s["id"]) and d(s["date"]) >= self.today:
                self.set_override(s, scaled(s, factor, reason) if factor < 1 else
                                  dict(duration_min=int(5 * round(s["duration_min"] * factor / 5)),
                                       tss=est_tss(int(s["duration_min"] * factor), s["kind"])), reason, "weekly")
                n += 1
        self.log.append(dict(made=self.today.isoformat(), session_id=None, session_date=week["start"], sport="all",
                             before="", after="", change=f"Week {week['index']} {'reduced' if factor < 1 else 'increased'} "
                             f"{abs(1 - factor):.0%}", reason=reason))

    # ----- run -------------------------------------------------------------
    def run(self):
        acts = self.state.get("activities", [])
        for a in acts:
            a["tss"], a["tss_method"] = activity_tss(a, self.baselines)

        live = self.live_sessions()
        extras = self.match(live, acts)

        # fitness / fatigue / form up to today
        daily = {}
        for a in acts:
            daily[a["date"]] = daily.get(a["date"], 0) + a["tss"]
        first = min([d(a["date"]) for a in acts], default=self.today - dt.timedelta(days=1))
        hist = pmc(daily, min(first, self.today - dt.timedelta(days=42)), self.today)
        series = {p["date"]: p for p in hist}
        form = series.get(self.today.isoformat(), {}).get("tsb")

        ready = readiness(self.state.get("wellness", {}), self.today)

        # rules (order matters: weekly shape → missed → today's readiness → fatigue)
        self.rule_weekly(live, series)
        live = self.live_sessions(); self.match(live, acts)
        self.rule_missed(live)
        live = self.live_sessions(); self.match(live, acts)
        self.rule_readiness(live, ready)
        live = self.live_sessions(); self.match(live, acts)
        self.rule_fatigue(live, form)
        live = self.live_sessions(); extras = self.match(live, acts)

        # projection to race day using the live plan
        race_day = d(self.cfg["race"]["date"])
        planned = {}
        for s in live:
            if d(s["date"]) > self.today or (s["date"] == self.today.isoformat() and s["status"] == "planned"):
                planned[s["date"]] = planned.get(s["date"], 0) + s["tss"]
        yday = hist[-2] if len(hist) >= 2 else dict(ctl=0, atl=0)
        t = self.today.isoformat()
        planned[t] = planned.get(t, 0) + daily.get(t, 0)
        proj = pmc(planned, self.today, race_day, yday["ctl"], yday["atl"])

        self.state["fired"] = sorted(self.fired)
        self.state["adjustments"] = self.log[-200:]
        return dict(sessions=live, extras=extras, history=hist, projection=proj, readiness=ready, form=form)
