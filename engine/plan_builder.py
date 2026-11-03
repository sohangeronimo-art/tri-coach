"""Builds the base periodised plan from config/athlete.json.

The base plan is the 'ideal' plan. The adaptive engine (adapt.py) layers
day-to-day changes on top of it from Garmin / Strava data.
"""
import datetime as dt
import math

from .zones import Zones, fmt_pace

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Relative minutes per role at the start of the plan and at peak. The week is
# scaled so the total hits the target hours; these just set the proportions.
ROLE_WEIGHTS = {
    #                  start  peak
    "swim_endurance": (45, 55),
    "swim_technique": (40, 40),
    "swim_threshold": (45, 60),
    "bike_quality":   (60, 75),
    "bike_endurance": (60, 70),
    "bike_long":      (105, 165),
    "run_long":       (55, 80),
    "run_quality":    (45, 55),
    "run_easy":       (25, 30),
    "run_brick":      (0, 25),
    "strength":       (35, 30),
}

ROLE_SPORT = {r: r.split("_")[0] for r in ROLE_WEIGHTS}
ROLE_SPORT["strength"] = "strength"

KEY_ROLES = {"bike_quality", "run_quality", "swim_threshold", "bike_long", "run_long"}

IF_BY_KIND = {
    "recovery": 0.55, "easy": 0.65, "technique": 0.62, "endurance": 0.70, "long": 0.72,
    "tempo": 0.80, "sweetspot": 0.85, "threshold": 0.90, "vo2": 0.92, "race_pace": 0.87,
    "brick": 0.84, "test": 0.90, "race": 0.90, "strength": 0.55, "openers": 0.70,
}

PHASE_INFO = {
    "base":   ("Base", "Aerobic engine, technique, strength foundation"),
    "build1": ("Build 1", "Threshold work, longer long sessions"),
    "build2": ("Build 2", "Race-specific: race pace, bricks, open water"),
    "peak":   ("Peak", "Race rehearsals at full race load"),
    "taper":  ("Taper", "Volume down, sharpness kept"),
    "race":   ("Race week", "Fresh legs. Trust the work."),
}


def r5(x):
    return max(5, int(5 * round(x / 5)))


def r50(x):
    return max(50, int(50 * round(x / 50)))


def tss(minutes, kind):
    i = IF_BY_KIND.get(kind, 0.7)
    return round(minutes / 60 * i * i * 100)


# ---------------------------------------------------------------------------
# Phase layout
# ---------------------------------------------------------------------------

def layout_weeks(n):
    """Return list of (phase, is_recovery, is_test) for n weeks ending in race week."""
    out = []
    phase_of = []
    for i in range(n):
        e = n - 1 - i
        if e == 0:
            phase_of.append("race")
        elif e == 1:
            phase_of.append("taper")
        elif e <= 3:
            phase_of.append("peak")
        elif e <= 9:
            phase_of.append("build2")
        elif e <= 17:
            phase_of.append("build1")
        else:
            phase_of.append("base")
    # recovery pattern inside each block
    pos = {}
    for i, ph in enumerate(phase_of):
        pos[ph] = pos.get(ph, 0) + 1
        k = pos[ph]
        if ph in ("base", "build1"):
            rec = k % 4 == 0
        elif ph == "build2":
            rec = k % 3 == 0
        else:
            rec = False
        out.append([ph, rec, False])
    # tests: week 1, first recovery of build1, first recovery of build2
    out[0][2] = True
    for ph in ("build1", "build2"):
        for i, w in enumerate(out):
            if w[0] == ph and w[1]:
                w[2] = True
                break
    return [tuple(w) for w in out]


# ---------------------------------------------------------------------------
# Session library
# ---------------------------------------------------------------------------

class Lib:
    def __init__(self, zones: Zones):
        self.z = zones

    def swim_dist(self, minutes):
        return r50(minutes * 60 / (self.z.css + 20) * 100)

    # ----- swim ------------------------------------------------------------
    def swim_technique(self, ph, m, w):
        d = self.swim_dist(m)
        wu, cd = 300, 100
        drills = 400 if d >= 1500 else 300
        main = max(200, d - wu - cd - drills)
        steps = [
            ("Warm up", f"{wu}m easy, mixed strokes · {self.z.swim('EASY')}"),
            ("Drills", f"{drills // 50}×50 rotating catch-up / fingertip drag / 6-kick switch / single arm, 15s rest"),
            ("Main", f"{main}m as 100s with a pull buoy, long stroke, count strokes · {self.z.swim('STEADY')}"),
            ("Cool down", f"{cd}m easy"),
        ]
        return dict(title=f"{d / 1000:.1f}km Technique Swim", kind="technique", distance_m=d, steps=steps,
                    summary="Drills and smooth, long strokes")

    def swim_endurance(self, ph, m, w):
        d = self.swim_dist(m)
        wu, cd = 400, 200
        main = max(400, d - wu - cd)
        if ph in ("base",):
            rep, rest, z, note = 200, 20, "STEADY", ""
        elif ph == "build1":
            rep, rest, z, note = 300, 20, "STEADY", ""
        elif ph in ("build2", "peak"):
            rep, rest, z, note = 400, 20, "RACE", " · sight every 6 strokes"
        else:
            rep, rest, z, note = 200, 20, "STEADY", ""
        n = max(2, main // rep)
        steps = [
            ("Warm up", f"{wu}m easy + 4×50 build"),
            ("Main", f"{n}×{rep}m · {self.z.swim(z)} · {rest}s rest{note}"),
            ("Cool down", f"{cd}m easy"),
        ]
        return dict(title=f"{wu + n * rep + cd:,}m Endurance Swim".replace(",", ","), kind="endurance",
                    distance_m=wu + n * rep + cd, steps=steps, summary="Aerobic swim volume")

    def swim_threshold(self, ph, m, w, test=False):
        if test:
            steps = [
                ("Warm up", "400m easy + 4×50 build + 100 easy"),
                ("Test 1", "400m time trial, all out but even. Note the time."),
                ("Recover", "10 min easy swimming"),
                ("Test 2", "200m time trial, all out. Note the time."),
                ("Cool down", "200m easy"),
            ]
            return dict(title="CSS Test Swim", kind="test", distance_m=1700, steps=steps,
                        summary="400 + 200 time trials. Sets your swim zones.")
        d = self.swim_dist(m)
        wu, cd = 400, 200
        main = max(500, d - wu - cd)
        if ph == "base":
            rep, rest, z = 100, 15, "CSS"
        elif ph == "build1":
            rep, rest, z = 150, 15, "CSS"
        elif ph == "build2":
            rep, rest, z = 200, 20, "CSS"
        elif ph == "peak":
            rep, rest, z = 500, 30, "RACE"
        else:
            rep, rest, z = 100, 20, "RACE"
        n = max(3, main // rep)
        steps = [
            ("Warm up", f"{wu}m easy + 4×50 build"),
            ("Main", f"{n}×{rep}m · {self.z.swim(z)} · {rest}s rest"),
            ("Cool down", f"{cd}m easy"),
        ]
        return dict(title=f"{wu + n * rep + cd:,}m CSS Swim", kind="threshold", distance_m=wu + n * rep + cd,
                    steps=steps, summary=f"{n}×{rep} at threshold")

    # ----- bike ------------------------------------------------------------
    def bike_quality(self, ph, m, w, test=False):
        if test:
            steps = [
                ("Warm up", f"15 min easy, 3×1 min fast pedalling, 5 min easy · {self.z.bike('Z2')}"),
                ("Opener", "5 min hard (RPE 8), then 10 min easy"),
                ("Test", "20 min all-out, steady effort. Power meter: FTP ≈ 95% of avg power. "
                         "No power: record avg HR (≈ bike LTHR)."),
                ("Cool down", "10 min easy"),
            ]
            return dict(title="FTP Test Ride", kind="test", steps=steps, minutes=60,
                        summary="20-min test. Sets your bike zones.")
        work = m - 25
        if ph == "base":
            n = max(2, min(4, work // 15))
            main, kind, title = f"{n}×10 min · {self.z.bike('SS')} · 5 min easy between", "sweetspot", "Sweet Spot Ride"
        elif ph == "build1":
            n = max(3, min(5, work // 12))
            main, kind, title = f"{n}×8 min · {self.z.bike('Z4')} · 4 min easy between", "threshold", "Threshold Ride"
        elif ph == "build2" and w % 2 == 0:
            n = max(4, min(6, work // 8))
            main, kind, title = f"{n}×4 min · {self.z.bike('Z5')} · 4 min easy between", "vo2", "VO2 Ride"
        elif ph in ("build2", "peak"):
            main, kind, title = f"2×20 min · {self.z.bike('RACE')} · 5 min easy between, aero position", "race_pace", "Race Pace Ride"
        else:
            main, kind, title = f"3×3 min · {self.z.bike('RACE')} · 3 min easy", "openers", "Openers Ride"
        steps = [
            ("Warm up", f"15 min building · {self.z.bike('Z2')}"),
            ("Main", main),
            ("Cool down", "10 min easy"),
        ]
        return dict(title=title, kind=kind, steps=steps, summary=main.split(" · ")[0])

    def bike_endurance(self, ph, m, w):
        if ph == "base":
            main, kind = f"{m - 15} min steady · {self.z.bike('Z2')} · include 6×1 min high cadence (100+ rpm)", "endurance"
        elif ph == "build1":
            main, kind = f"Z2 with 2×15 min tempo · {self.z.bike('Z3')}", "tempo"
        elif ph in ("build2", "peak"):
            main, kind = f"Z2 with 3×10 min · {self.z.bike('RACE')}", "race_pace"
        else:
            main, kind = f"{m - 10} min easy spin · {self.z.bike('Z2')}", "easy"
        steps = [("Warm up", "10 min easy"), ("Main", main), ("Cool down", "5 min easy")]
        return dict(title="Endurance Ride" if kind == "endurance" else ("Tempo Ride" if kind == "tempo" else
                    ("Race Pace Ride" if kind == "race_pace" else "Easy Spin")), kind=kind, steps=steps,
                    summary=main.split(" · ")[0])

    def bike_long(self, ph, m, w):
        if ph == "base":
            main, kind = f"{m} min steady · {self.z.bike('Z2')} · fuel every 20 min", "long"
        elif ph == "build1":
            main, kind = f"Z2, last 30 min · {self.z.bike('Z3')} · fuel every 20 min", "long"
        elif ph == "build2":
            main, kind = f"Z2 with 3×15 min · {self.z.bike('RACE')} · practise race nutrition", "long"
        elif ph == "peak":
            main, kind = f"Race rehearsal: 40km · {self.z.bike('RACE')} inside a Z2 ride · race kit + nutrition", "race_pace"
        else:
            main, kind = f"{m} min · {self.z.bike('Z2')}", "endurance"
        hrs = m / 60
        title = f"{int(hrs)}hr {int(round((hrs % 1) * 60))}min Long Ride".replace(" 0min", "")
        if hrs < 1:
            title = f"{m} min Long Ride"
        return dict(title=title, kind=kind, steps=[("Ride", main)], summary=main.split(" · ")[0])

    # ----- run -------------------------------------------------------------
    def run_quality(self, ph, m, w, test=False):
        if test:
            steps = [
                ("Warm up", f"15 min easy + 4×20s strides · {self.z.run('Z2')}"),
                ("Test", "30 min solo, best even effort. Lap at 10 min. Avg pace of the last 20 min ≈ threshold "
                         "pace, avg HR of the last 20 min ≈ run LTHR."),
                ("Cool down", "10 min easy"),
            ]
            return dict(title="Run Threshold Test", kind="test", steps=steps, minutes=55,
                        summary="30-min test. Sets your run zones.")
        lt = self.z.run_lt
        if ph == "base":
            if w < 3:
                main, kind, title = f"{m - 15} min · {self.z.run('Z2')} with 6×20s strides", "endurance", "Strides Run"
            elif w % 2 == 0:
                main, kind, title = "8×45s hill reps, hard but controlled, jog down recovery", "vo2", "Hill Reps Run"
            else:
                main, kind, title = f"2×10 min · {self.z.run('Z3')} · 3 min jog", "tempo", "Tempo Run"
        elif ph == "build1":
            n = max(3, min(6, (m - 25) // 6))
            main, kind, title = f"{n}×1km · {self.z.run('Z5')} · 90s jog", "threshold", "Threshold Run"
        elif ph == "build2":
            main, kind, title = f"3×2km · {self.z.run('RACE')} · 2 min jog", "race_pace", "Race Pace Run"
        elif ph == "peak":
            main, kind, title = f"5×1km at {fmt_pace(lt * 1.0)}/km (a touch quicker than race pace) · 90s jog", "threshold", "Sharpening Run"
        else:
            main, kind, title = f"4×3 min · {self.z.run('RACE')} · 2 min jog", "openers", "Openers Run"
        steps = [("Warm up", f"15 min easy · {self.z.run('Z2')}"), ("Main", main), ("Cool down", "10 min easy")]
        return dict(title=title, kind=kind, steps=steps, summary=main.split(" · ")[0])

    def run_long(self, ph, m, w):
        if ph in ("base",):
            main = f"{m} min · {self.z.run('Z2')}"
        elif ph == "build1":
            main = f"{m - 15} min Z2, last 15 min · {self.z.run('Z3')}"
        elif ph in ("build2", "peak"):
            main = f"{m - 20} min Z2, last 20 min · {self.z.run('RACE')}"
        else:
            main = f"{m} min · {self.z.run('Z2')}"
        return dict(title=f"{m} min Endurance Run", kind="long", steps=[("Run", main)], summary=main.split(" · ")[0])

    def run_easy(self, ph, m, w):
        off_bike = ph in ("build2", "peak")
        main = f"{m} min · {self.z.run('Z2')}" + (" · straight off the ride" if off_bike else "")
        return dict(title=f"{m} min Easy Run", kind="easy", steps=[("Run", main)], summary="Easy aerobic")

    def run_brick(self, ph, m, w):
        main = f"Straight off the bike: first 5 min controlled, then {m - 5} min · {self.z.run('RACE')}"
        return dict(title=f"{m} min Brick Run", kind="brick", steps=[("Transition", "Rack bike, shoes on, go. Practise a fast T2."),
                                                                         ("Run", main)], summary="Run off the bike")

    def strength(self, ph, m, w):
        if ph == "base":
            body = ("3×10: goblet squat, Romanian deadlift, split squat, push-ups, bent-over row. "
                    "Core: 3×40s plank, side plank, dead bugs. Calf raises 3×15.")
            title = "Foundation Strength"
        elif ph == "build1":
            body = ("3×6 heavier: back squat or trap-bar deadlift, step-ups, pull-ups / rows. "
                    "Plyo: 3×5 box jumps. Core circuit 10 min.")
            title = "Strength & Power"
        else:
            body = "2×8 moderate: squats, single-leg RDL, rows. Core 8 min. Stay fresh."
            title = "Maintenance Strength"
        return dict(title=f"{m} min {title}", kind="strength", steps=[("Session", body)], summary=title)


# ---------------------------------------------------------------------------
# Race week (fixed)
# ---------------------------------------------------------------------------

def race_week(lib: Lib, start: dt.date, race_date: dt.date, cfg):
    z = lib.z
    race = cfg["race"]
    plan = {
        -5: [("swim", "Race Week Swim", "easy", 40, [("Main", f"1500m easy with 5×100 · {z.swim('RACE')}")]),
             ("run", "Easy Run", "easy", 30, [("Main", f"30 min · {z.run('Z2')}")])],
        -4: [("bike", "Openers Ride", "openers", 60, [("Main", f"Z2 with 4×4 min · {z.bike('RACE')}")])],
        -3: [("run", "Openers Run", "openers", 35, [("Main", f"Easy with 5×1 min · {z.run('RACE')}")]),
             ("swim", "Easy Swim", "easy", 30, [("Main", "1200m easy + 4×50 fast")])],
        -2: [("bike", "Easy Ride", "easy", 40, [("Main", f"Z2 with 3×1 min race effort · {z.bike('Z2')}")]),
             ("swim", "Open Water Check", "easy", 20, [("Main", "Swim the course if you can. Test wetsuit and goggles.")])],
        -1: [("bike", "Shakeout Ride", "easy", 20, [("Main", "Easy spin + 3×30s race effort. Check the bike.")]),
             ("run", "Shakeout Run", "easy", 10, [("Main", "Easy jog + 3 strides")])],
        0: [("race", f"{race['name']} — RACE DAY", "race", 150,
             [("Swim", f"{race['swim_m']}m · settle in, then {z.swim('RACE')}"),
              ("Bike", f"{race['bike_km']}km · {z.bike('RACE')} · don't burn the run"),
              ("Run", f"{race['run_km']}km · first 2km controlled, then {z.run('RACE')}. Empty the tank in the last 2km.")])],
        1: [("swim", "Recovery Swim", "recovery", 20, [("Main", "Optional easy swim or walk")])],
    }
    out = []
    for off, items in plan.items():
        day = race_date + dt.timedelta(days=off)
        if day < start or day > start + dt.timedelta(days=6):
            continue
        for k, (sport, title, kind, mins, steps) in enumerate(items):
            out.append(dict(date=day.isoformat(), sport=sport, role=f"raceweek_{sport}_{k}", title=title, kind=kind,
                            duration_min=mins, steps=[{"label": a, "detail": b} for a, b in steps],
                            key=kind == "race", summary=steps[0][1][:60]))
    return out


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def _blocked(cfg, day):
    for b in cfg.get("blocked_dates", []):
        if b["from"] <= day.isoformat() <= b["to"]:
            return b
    return None


def build_plan(cfg, baselines=None):
    b = dict(cfg["baselines"])
    if baselines:
        b.update({k: v for k, v in baselines.items() if v})
    zones = Zones(b["swim_css_s_per_100m"], b["run_threshold_pace_s_per_km"], b.get("bike_ftp_w"),
                  b.get("run_lthr_bpm"), b.get("bike_lthr_bpm"))
    lib = Lib(zones)

    start = dt.date.fromisoformat(cfg["plan_start"])
    race_date = dt.date.fromisoformat(cfg["race"]["date"])
    race_week_start = race_date - dt.timedelta(days=race_date.weekday())
    n = (race_week_start - start).days // 7 + 1
    layout = layout_weeks(n)

    train_idx = [i for i, (ph, rec, _) in enumerate(layout) if not rec and ph not in ("taper", "race")]
    progress = {i: (k / max(1, len(train_idx) - 1)) for k, i in enumerate(train_idx)}

    v0, v1 = cfg["volume"]["start_hours"], cfg["volume"]["peak_hours"]
    weeks, sessions = [], []
    last_p = 0.0
    for i, (ph, rec, test) in enumerate(layout):
        wstart = start + dt.timedelta(days=7 * i)
        p = progress.get(i, last_p)
        if i in progress:
            last_p = p
        shaped = math.sqrt(p)  # rise quicker early, flatten toward peak
        hours = v0 + (v1 - v0) * shaped
        if rec:
            hours *= 0.65
        if ph == "taper":
            hours = v1 * 0.7
        if i == 0:
            hours *= 0.85

        name, focus = PHASE_INFO[ph]
        week = dict(index=i + 1, start=wstart.isoformat(), end=(wstart + dt.timedelta(days=6)).isoformat(),
                    phase=ph, phase_name=name, focus=("Recovery week — absorb the work" if rec else focus),
                    recovery=rec, test_week=test, planned_hours=round(hours, 1))

        if ph == "race":
            wk_sessions = race_week(lib, wstart, race_date, cfg)
        else:
            roles = []
            for di, day in enumerate(DAYS):
                for role in cfg["week_template"].get(day, []):
                    if role == "run_brick" and ph == "base":
                        continue
                    if role == "strength" and ph in ("peak", "taper"):
                        continue
                    roles.append((di, role))
            weights = {r: ROLE_WEIGHTS[r][0] + (ROLE_WEIGHTS[r][1] - ROLE_WEIGHTS[r][0]) * shaped for _, r in roles}
            total = sum(weights[r] for _, r in roles) or 1
            scale = hours * 60 / total
            wk_sessions = []
            for di, role in roles:
                day = wstart + dt.timedelta(days=di)
                mins = r5(weights[role] * scale)
                is_test = test and role in ("bike_quality", "run_quality", "swim_threshold")
                fn = getattr(lib, role)
                spec = fn(ph, mins, i, test=True) if is_test else fn(ph, mins, i)
                mins = spec.pop("minutes", mins)
                steps = [{"label": a, "detail": d} for a, d in spec.pop("steps")]
                wk_sessions.append(dict(date=day.isoformat(), sport=ROLE_SPORT[role], role=role, duration_min=mins,
                                        steps=steps, key=(role in KEY_ROLES and not rec) or is_test, test=is_test, **spec))

        # blocked dates
        kept = []
        for s in wk_sessions:
            blk = _blocked(cfg, dt.date.fromisoformat(s["date"]))
            if blk:
                mode = blk.get("mode", "off")
                if mode == "off" or (mode == "run_only" and s["sport"] not in ("run", "strength")) or \
                        (mode == "no_pool" and s["sport"] == "swim"):
                    continue
            kept.append(s)
        for s in kept:
            s["id"] = f"{s['date']}-{s['role']}"
            s["tss"] = tss(s["duration_min"], s["kind"])
            s.setdefault("distance_m", None)
            s.setdefault("test", False)
        week["planned_tss"] = sum(s["tss"] for s in kept)
        week["planned_hours"] = round(sum(s["duration_min"] for s in kept) / 60, 1)
        weeks.append(week)
        sessions.extend(kept)

    return dict(zones=zones.table(), weeks=weeks, sessions=sessions, baselines=b)


if __name__ == "__main__":
    import json, sys
    cfg = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "config/athlete.json"))
    plan = build_plan(cfg)
    for w in plan["weeks"]:
        print(w["index"], w["start"], w["phase_name"], "REC" if w["recovery"] else "   ",
              "TEST" if w["test_week"] else "    ", w["planned_hours"], "h", w["planned_tss"], "TSS")
    print(len(plan["sessions"]), "sessions")
