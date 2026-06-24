# FieldCalc Instagram Autopost — Developer Guide

How a new developer gets this project running, end to end. Read this first.

---

## 1. What this project is

An automated Instagram content engine for the **FieldCalc – GPS Area Measure** app
(10M+ downloads, live on Play Store since 2018). Each day it:

1. Picks an app feature + content angle (never repeating — 15 features × 7 angles).
2. Generates a caption + hashtags (Gemini LLM, with a local fallback).
3. Renders a branded poster image.
4. Routes the post to a **human-approval dashboard**.
5. On approval, **publishes to Instagram** via the Meta Graph API.
6. Later collects performance metrics and learns what works.

**Tech stack:** FastAPI · SQLAlchemy · Pillow/Playwright (images) · Cloudinary (image
hosting) · Meta Graph API (Instagram Business Login) · Gemini/Groq (captions) · Render (hosting).

---

## 2. Architecture in one picture

```
cron-job.org ──POST /run-daily──▶ generate post ──▶ PENDING_APPROVAL
                                                          │
                                              human opens dashboard
                                                          │  (reviews image + caption)
                                                   clicks Approve
                                                          │
                                      upload image → Cloudinary (public URL)
                                                          │
                                   Meta Graph API: create container → wait FINISHED → publish
                                                          │
                                                      PUBLISHED ✅
```

State machine: `DRAFTED → PENDING_APPROVAL → APPROVED → PUBLISHING → PUBLISHED`
(or `REJECTED` / `FAILED`).

---

## 3. Run it locally

> ⚠️ **Instagram is blocked on some office/corporate networks** (Sophos and similar
> firewalls categorize it as "Social Networking"). If `graph.instagram.com` returns a
> "Blocked site" page or an SSL `CERTIFICATE_VERIFY_FAILED`, you are behind such a
> firewall — use a different network (mobile hotspot) or just run it on Render (below).

```bash
cd instagram-autopost
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium   # optional; falls back to Pillow if missing
cp .env.example .env                     # then fill in values (see CREDENTIALS.md)
uvicorn app.main:app --reload
```

Then:
- Health check: open http://localhost:8000/health
- Dashboard: open http://localhost:8000/ (login: any username + `DASHBOARD_PASSWORD`)

### Trigger the pipeline manually (local)
```bash
# 1. generate today's post
curl -X POST localhost:8000/run-daily -H "X-Run-Token: $RUN_TOKEN"
# 2. review + Approve in the dashboard (clicking Approve now publishes immediately)
# 3. (optional) publish any approved-but-unpublished posts
curl -X POST localhost:8000/run-poller -H "X-Run-Token: $RUN_TOKEN"
```

### Run the tests (no accounts needed)
```bash
source .venv/bin/activate
python3 -m pytest        # runs in DRY_RUN against a temp SQLite DB
```

---

## 4. Environment variables

All config comes from environment / `.env`. See `CREDENTIALS.md` for the real values.

| Key | What it is | Required to post? |
|---|---|---|
| `DRY_RUN` | `true` = simulate (no real posting); `false` = live | — |
| `IG_USER_ID` | Instagram Business account numeric ID | ✅ |
| `IG_ACCESS_TOKEN` | Instagram Business Login token (starts `IGAA…`, 60-day) | ✅ |
| `META_APP_ID` / `META_APP_SECRET` | Meta app credentials (Instagram product) | ✅ |
| `GRAPH_BASE_URL` | `https://graph.instagram.com/v21.0` for IGAA tokens | ✅ |
| `CLOUDINARY_URL` | `cloudinary://key:secret@cloud` — hosts images publicly for Meta | ✅ |
| `DATABASE_URL` | Postgres (Neon) connection string; falls back to local SQLite | recommended |
| `GEMINI_API_KEY` | Caption LLM; without it, a local template fallback is used | optional |
| `GROQ_API_KEY` | Secondary caption LLM fallback | optional |
| `RUN_TOKEN` | Secret protecting `/run-daily`, `/run-poller`, etc. (sent as `X-Run-Token` header) | ✅ |
| `DASHBOARD_PASSWORD` | Password for the approval dashboard | ✅ |
| `SSL_VERIFY` | Keep `true`. `false` only to bypass a TLS-intercepting proxy when testing | — |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Optional alerts | optional |
| `SENTRY_DSN`, `HEALTHCHECK_PING_URL` | Optional ops monitoring | optional |

---

## 5. Key API endpoints

Protected endpoints need header `X-Run-Token: <RUN_TOKEN>`.

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness + DB/coverage info |
| `POST /run-daily` | Generate today's post (→ PENDING_APPROVAL) |
| `POST /run-poller` | Publish approved-and-due posts |
| `POST /approve/{id}` | Approve a post (API) |
| `POST /reject/{id}` | Reject a post |
| `POST /requeue/{id}` | Reset a FAILED post back to APPROVED to retry |
| `GET /` | Approval dashboard (Basic auth; **Approve now publishes immediately**) |
| `GET /history` | Published / rejected / failed history |
| `GET /insights/performance` | Ranked posts + winning patterns |

---

## 6. Deploying to Render (production)

The bot must run on an always-on server that can reach Instagram (your laptop can't —
it's off when you leave, and office networks block Instagram). Render is the configured host.

1. Push the repo to GitHub.
2. Render → **New → Web Service** → connect the repo.
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Instance: Free
3. Add all env vars from `CREDENTIALS.md` (use **Add from .env** to paste them at once).
4. **Pin Python:** the repo includes `.python-version` (3.12.8). Do not remove it —
   Python 3.14 breaks prebuilt wheels (pydantic-core tries to compile Rust and fails).
5. Deploy. Health check path is `/health`.

### Two known free-tier limitations
- **Spins down when idle** → first request after a while takes ~50s and may 502. A cron
  ping keeps it warm; just retry.
- **Ephemeral disk** → SQLite resets on every redeploy. **Add Neon Postgres**
  (`DATABASE_URL`) so posts/history survive. Free, no card: neon.tech.

### Daily automation (cron-job.org)
- Job 1: `POST https://<app>.onrender.com/run-daily` with header `X-Run-Token` — once/day.
- Job 2: `POST https://<app>.onrender.com/run-poller` with header `X-Run-Token` — every ~5 min.

---

## 7. Project layout

```
instagram-autopost/
├── app/
│   ├── main.py            # FastAPI app + endpoints
│   ├── config.py          # env-driven settings
│   ├── database.py        # SQLAlchemy models + state machine
│   ├── pipeline.py        # orchestrator: generate → approve → publish
│   ├── content_planner.py # what to post today (pillars/features/angles)
│   ├── feature_picker.py  # zero-repeat 15×7 rotation
│   ├── caption.py + llm.py# caption generation (Gemini→Groq→local)
│   ├── verify.py          # compliance gates (claims, dedup, hashtags)
│   ├── imaging.py         # poster rendering (HTML/Playwright → Pillow fallback)
│   ├── render_html.py     # Chromium HTML→PNG renderer
│   ├── instagram.py       # Meta Graph API client (create→wait→publish)
│   ├── dashboard.py       # approval UI (Approve = publish immediately)
│   ├── insights.py + learning.py  # metrics + learning loop
│   └── ...
├── data/                  # features.json, angles.json, pillars.json, visual_assets.json
├── requirements.txt
├── render.yaml            # Render blueprint
├── .python-version        # 3.12.8 (do not remove)
├── .env                   # secrets (git-ignored)
├── CREDENTIALS.md         # the actual secret values
├── SETUP-JOURNEY.md       # how this was built, step by step
└── DEVELOPER-GUIDE.md     # this file
```

---

## 8. Gotchas we already hit (so you don't)

- **`code 9007 "Media not ready"`** on publish → you must poll the container's
  `status_code` until `FINISHED` before calling `media_publish`. Handled in
  `instagram._wait_until_ready()`.
- **Instagram Business Login tokens (`IGAA…`)** use `graph.instagram.com`, NOT
  `graph.facebook.com`. Set `GRAPH_BASE_URL` accordingly.
- **Meta needs a public image URL** — local file paths fail. Images are uploaded to
  Cloudinary first (`instagram._public_url()`).
- **`requests` not `httpx`** for Graph calls — old macOS LibreSSL trips httpx; requests
  + certifi verifies cleanly.
- **"Insufficient developer role"** when generating a token → assign the Instagram
  account the **Instagram Tester** role in the Meta app and accept the invite in the IG app.
