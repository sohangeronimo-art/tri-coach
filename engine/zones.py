"""Training zones and target strings for swim / bike / run."""


def fmt_pace(sec):
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


# Run pace zones as multipliers of threshold pace (slower = bigger number).
RUN_ZONES = {
    "Z1": (1.29, 1.45),   # recovery
    "Z2": (1.14, 1.29),   # aerobic
    "Z3": (1.06, 1.13),   # tempo
    "Z4": (1.01, 1.05),   # sub-threshold
    "Z5": (0.97, 1.00),   # threshold
    "Z6": (0.90, 0.96),   # VO2
    "RACE": (1.03, 1.06), # Olympic 10k off the bike
}

SWIM_OFFSETS = {          # seconds per 100m relative to CSS
    "EASY": (12, 18),
    "STEADY": (6, 10),
    "CSS": (-1, 2),
    "FAST": (-6, -3),
    "RACE": (2, 5),
}

BIKE_ZONES = {            # % FTP
    "Z1": (0, 55),
    "Z2": (56, 75),
    "Z3": (76, 87),
    "SS": (88, 93),
    "Z4": (94, 105),
    "Z5": (106, 120),
    "RACE": (85, 92),
}

BIKE_RPE = {"Z1": "RPE 2", "Z2": "RPE 3–4", "Z3": "RPE 5–6", "SS": "RPE 6–7",
            "Z4": "RPE 7–8", "Z5": "RPE 9", "RACE": "RPE 6–7"}

# Friel-style HR zones as % of lactate-threshold HR
HR_ZONES = {"Z1": (0, 84), "Z2": (85, 89), "Z3": (90, 94), "Z4": (95, 99), "Z5": (100, 106)}


class Zones:
    def __init__(self, css, run_lt, ftp=None, run_lthr=None, bike_lthr=None):
        self.css = css
        self.run_lt = run_lt
        self.ftp = ftp
        self.run_lthr = run_lthr
        self.bike_lthr = bike_lthr

    # ---- targets -------------------------------------------------------
    def run(self, z):
        lo, hi = RUN_ZONES[z]
        s = f"{fmt_pace(self.run_lt * lo)}–{fmt_pace(self.run_lt * hi)}/km"
        hr = self._hr(self.run_lthr, z)
        label = "Race pace" if z == "RACE" else z
        return f"{label} · {s}" + (f" · {hr}" if hr else "")

    def swim(self, z):
        lo, hi = SWIM_OFFSETS[z]
        label = {"EASY": "Easy", "STEADY": "Steady", "CSS": "CSS", "FAST": "Fast", "RACE": "Race pace"}[z]
        return f"{label} · {fmt_pace(self.css + lo)}–{fmt_pace(self.css + hi)}/100m"

    def bike(self, z):
        lo, hi = BIKE_ZONES[z]
        label = "Race power" if z == "RACE" else z
        pct = f"{lo}–{hi}% FTP" if lo else f"<{hi}% FTP"
        if self.ftp:
            w = f"{round(self.ftp * lo / 100)}–{round(self.ftp * hi / 100)}W" if lo else f"<{round(self.ftp * hi / 100)}W"
            return f"{label} · {w} ({pct})"
        hr = self._hr(self.bike_lthr, z if z in HR_ZONES else ("Z3" if z in ("SS", "RACE") else "Z2"))
        return f"{label} · {pct} · {BIKE_RPE[z]}" + (f" · {hr}" if hr else "")

    def _hr(self, lthr, z):
        if not lthr or z not in HR_ZONES:
            return ""
        lo, hi = HR_ZONES[z]
        return f"{round(lthr * lo / 100)}–{round(lthr * hi / 100)}bpm" if lo else f"<{round(lthr * hi / 100)}bpm"

    def table(self):
        return {
            "swim": [{"zone": z, "target": self.swim(z)} for z in SWIM_OFFSETS],
            "bike": [{"zone": z, "target": self.bike(z)} for z in BIKE_ZONES],
            "run": [{"zone": z, "target": self.run(z)} for z in RUN_ZONES],
            "css": fmt_pace(self.css) + "/100m",
            "run_threshold": fmt_pace(self.run_lt) + "/km",
            "ftp": self.ftp,
            "run_lthr": self.run_lthr,
            "bike_lthr": self.bike_lthr,
        }
