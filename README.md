# Brick — adaptive triathlon coach

A training app for the Kiama Triathlon (Standard / Olympic distance, Sat 8 May 2027).
It pulls your Garmin (and optionally Strava) data three times a day, rewrites the plan
around what you actually did and how you recovered, and shows it in a web app you add to
your iPhone home screen.

App: **https://sohangeronimo-art.github.io/tri-coach/** (after setup step 2)

## Setup (about 10 minutes, phone is fine)

### 1. Add your secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Name | Value |
|---|---|
| `APP_PASSPHRASE` | Make one up. Long (4+ random words). It encrypts all your data and unlocks the app. |
| `GARMIN_EMAIL` | Your Garmin Connect login email |
| `GARMIN_PASSWORD` | Your Garmin Connect password |

Secrets are never shown again, not even to you, and never appear in logs.
If your Garmin account has two-step verification on, tell Claude: it needs a one-off token step instead.

### 2. Turn on the web app
Repo → **Settings → Pages** → Source: **Deploy from a branch** → Branch: `main`, folder: `/docs` → **Save**.

### 3. Run the first sync
Repo → **Actions** → (enable workflows if asked) → **Sync & adapt** → **Run workflow**.
The first run pulls the last 60 days from Garmin so your fitness numbers start accurate. Takes 2–4 minutes.

### 4. Put it on your phone
Open the app link in **Safari** → enter your passphrase → **Share → Add to Home Screen**.
It opens full-screen, works offline, and remembers the passphrase.

### 5. Strava backup (optional)
1. Go to https://www.strava.com/settings/api and create an app. Website: the app link above. Authorization Callback Domain: `localhost`.
2. Add two more secrets: `STRAVA_CLIENT_ID` and `STRAVA_CLIENT_SECRET` (from that page).
3. Open this link with your client ID swapped in:
   `https://www.strava.com/oauth/authorize?client_id=YOUR_ID&response_type=code&redirect_uri=http://localhost&approval_prompt=force&scope=activity:read_all`
4. Tap **Authorize**. The next page fails to load. That's expected. Copy the `code=` value from the address bar.
5. **Actions → Connect Strava → Run workflow**, paste the code. Codes expire in a few minutes, so do this straight away.

Garmin already copies workouts to Strava, so duplicates are merged and Garmin wins (it has more detail).

## Setting up your own copy (for friends)

Everyone gets their own repo, their own secrets and their own app link. Nobody can see anyone else's data.

1. Make a free GitHub account.
2. Open https://github.com/sohangeronimo-art/tri-coach → **Use this template → Create a new repository**.
   Name it `tri-coach`, set it to **Public**, and create it.
3. In your new repo, open `config/athlete.json` → pencil icon → replace it with your own details
   (Claude can write this file for you: give it your race, date, swim pace, run pace, FTP if known,
   which days you can train and roughly how many hours). Commit.
4. Do setup steps 1–4 above in **your** repo. Your app will be at `https://YOUR-USERNAME.github.io/tri-coach/`.

The copy carries the original owner's encrypted files. They can't be read without that owner's passphrase,
and your first sync replaces them with your own.

## How the plan adapts

* **Every morning**: last night's HRV, resting HR, sleep and Garmin Training Readiness give a readiness score.
  Red → today's hard session becomes easy and the hard one moves to an easy day of the same sport later in the week.
  Amber → trimmed 20%.
* **Missed key session**: moved onto a later easy day that week. No safe slot → dropped, never crammed.
* **Fatigue**: form (fitness − fatigue) below −30 → next 3 days trimmed.
* **Every Monday**: last week under 60% done → ease back in. Fitness ramping > 8/week or HRV low 4+ days → trim 10–15%.
  Two 95%+ weeks with good recovery → +5%.
* **New thresholds**: when Garmin updates FTP, threshold pace or threshold HR, every future target is rewritten.

Every change shows in the app under **Plan changes** with the reason.

## What's public and what isn't

The repo is public so GitHub Pages hosts it for free.

* **Encrypted:** everything from Garmin/Strava (activities, HRV, sleep, heart rate) and your login tokens:
  `docs/data.enc` and `state/state.enc`. Unreadable without `APP_PASSPHRASE`.
* **Readable by anyone:** the code and `config/athlete.json` (race, starting paces, weekly layout).

## Changing things

Edit `config/athlete.json`, or just ask Claude to do it:
* `week_template`: which sessions go on which day
* `volume`: start and peak weekly hours
* `blocked_dates`: trips or exam weeks (`off`, `run_only`, `no_pool`)
* `baselines`: starting CSS, threshold pace, FTP

## Layout

```
config/athlete.json        your race, baselines, weekly layout
engine/plan_builder.py     26-week periodised plan (base → build → peak → taper)
engine/sources.py          Garmin + Strava sync, de-duplication
engine/load.py             TSS + fitness / fatigue / form
engine/adapt.py            the adaptation rules
engine/run.py              daily job
app/app.html               the app (source)
tools/build_site.py        builds docs/ from app/
docs/                      what GitHub Pages serves
tests/sim.py               simulated training block for testing
```
