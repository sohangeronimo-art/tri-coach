"""Training load: per-activity TSS and the fitness / fatigue / form model."""
import datetime as dt


def activity_tss(a, base):
    """Estimate TSS for a normalised activity. Prefers power, then pace, then HR."""
    hours = (a.get("duration_s") or 0) / 3600
    if hours <= 0:
        return 0, "none"
    sport = a.get("sport")
    ftp = base.get("bike_ftp_w")

    if a.get("tss_device"):
        return round(a["tss_device"]), "device"

    if sport == "bike" and ftp and (a.get("np") or a.get("avg_power")):
        p = a.get("np") or a.get("avg_power") * 1.05
        i = p / ftp
        return round(hours * i * i * 100), "power"

    if sport == "run" and a.get("distance_m") and a.get("duration_s"):
        pace = a["duration_s"] / (a["distance_m"] / 1000)  # s/km
        lt = base.get("run_threshold_pace_s_per_km") or 320
        if 150 < pace < 900:
            i = min(1.15, lt / pace)
            return round(hours * i * i * 100), "pace"

    if sport == "swim" and a.get("distance_m") and a.get("duration_s"):
        pace = a["duration_s"] / (a["distance_m"] / 100)  # s/100m incl. rest
        css = base.get("swim_css_s_per_100m") or 105
        if 60 < pace < 400:
            i = min(1.15, css / pace)
            return round(hours * i ** 3 * 100), "swim pace"

    hr = a.get("avg_hr")
    if hr:
        rest = base.get("resting_hr_bpm") or 55
        lthr = (base.get("bike_lthr_bpm") if sport == "bike" else base.get("run_lthr_bpm")) or \
               (base.get("max_hr_bpm") or 190) * 0.89
        i = max(0.4, min(1.15, (hr - rest) / max(1, lthr - rest)))
        return round(hours * i * i * 100), "heart rate"

    if sport == "strength":
        return round(hours * 30), "duration"
    return round(hours * 50), "duration"


def pmc(daily_tss, start, end, ctl0=0.0, atl0=0.0):
    """Performance-management chart. daily_tss: {iso_date: tss}.
    Returns list of {date, tss, ctl, atl, tsb} where tsb is form going INTO the day."""
    out = []
    ctl, atl = ctl0, atl0
    d = start
    while d <= end:
        t = daily_tss.get(d.isoformat(), 0)
        tsb = ctl - atl
        ctl = ctl + (t - ctl) / 42
        atl = atl + (t - atl) / 7
        out.append(dict(date=d.isoformat(), tss=round(t), ctl=round(ctl, 1), atl=round(atl, 1), tsb=round(tsb, 1)))
        d += dt.timedelta(days=1)
    return out
