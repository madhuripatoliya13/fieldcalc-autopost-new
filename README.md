# FieldCalc — Instagram Auto-Post System

Fully automated, **$0/month** Instagram content engine for the FieldCalc / Voice GPS app.
Daily it picks an app feature + content angle (never repeating), generates a caption +
branded image, verifies grammar/copyright/ASO, routes one post to a human-approval
dashboard, then publishes to Instagram via the Meta Graph API.

> Formats: **Carousel (default) + Story + Single image.** (Reels deferred.)

## Status

| Sprint | Scope | State |
|---|---|---|
| 0 | Foundation & unblock — data defs, durable DB, cron trigger, setup checklist | ✅ done |
| 1 | Durable pipeline core — state machine, idempotent publish, token refresh | ✅ done |
| 2 | Caption & compliance engine — multi-variant + judge, verification gates | ✅ done |
| 3 | Format-aware content — HTML→Chromium image engine, carousel/story/single | ✅ done |
| 4 | Content pillars — weighted+weekday mix, seasonal triggers, review social proof | ✅ done |
| 5 | Install attribution — UTM links, Play Install Referrer (Android), ingestion | ✅ done |
| 6 | Feedback loop & learning — insights, scoring, bandit, prompt injection, digest | ✅ done |
| 7 | Approval dashboard UI, observability, preflight, backups, CI, deploy config | ✅ done |

## Architecture (the foundation fixes)

- **State → Neon Postgres** (not the ephemeral host disk). Survives sleep/redeploy. → `app/database.py`
- **Trigger → external cron** hits token-protected `POST /run-daily` to wake the host and run. → `app/main.py`
- **Approval/publish → durable state machine** `DRAFTED → PENDING_APPROVAL → APPROVED → PUBLISHING → PUBLISHED`. → `Post.status`
- **LLM model id → env var** so deprecations are a one-line change. → `GEMINI_MODEL`

## Local dev

```bash
cd instagram-autopost
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install chromium   # primary image engine (Pillow is the fallback)
cp .env.example .env        # fill in keys; DRY_RUN stays true for now
uvicorn app.main:app --reload
# then: curl localhost:8000/health
```

> Deployment note: the Render build command must include
> `python -m playwright install --with-deps chromium`. If it's missing, the system
> still works — it falls back to the Pillow renderer automatically.

With no `DATABASE_URL` set it falls back to a local SQLite file, so you can run the
picker and dashboard before any cloud account exists.

## Testing (no accounts required)

```bash
source .venv/bin/activate
python3 -m pytest          # runs the suite in DRY_RUN against a temp SQLite DB
```

Four levels of testing:

1. **Automated suite** (`pytest`) — picker no-repeat, full state machine, idempotency,
   no-double-post, hashtag cap. Runs anywhere, no accounts.
2. **Local DRY_RUN** — `uvicorn app.main:app` then POST `/run-daily`, `/approve/{id}`,
   `/run-poller`. Walks a real post through the flow; images saved to `generated/`.
3. **Real post to your OWN account** — once you have a Business account + FB Page +
   dev token, set `DRY_RUN=false`. Instagram's *development mode* lets you publish to
   accounts you own **without** waiting for public App Review. This is the real
   integration test.
4. **Public/live** — after Meta App Review approves Advanced Access.

## Layout

```
instagram-autopost/
├── app/
│   ├── config.py          # env-driven settings (C4 model swap lives here)
│   ├── database.py         # durable models + state machine (C1, C3)
│   ├── feature_picker.py   # zero-repeat 15x7 picker (→ bandit in Sprint 6)
│   └── main.py             # FastAPI: /health, /run-daily, /run-poller
├── data/
│   ├── features.json       # 15 app features (source of truth for captions)
│   └── angles.json         # 7 content angles
├── requirements.txt
├── .env.example
└── HUMAN_SETUP.md          # ← accounts only you can create. START THE META REVIEW NOW.
```

## ⚠️ Before code can post anything

Read **[HUMAN_SETUP.md](./HUMAN_SETUP.md)**. The Meta App Review is a 2–4 week
gate — start it on day one, in parallel with development.
