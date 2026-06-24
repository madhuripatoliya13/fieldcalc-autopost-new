# Human Setup Checklist — accounts & approvals only you can do

These steps need a human with access to your business identity, payment-free
accounts, and the Instagram account. Do them **in parallel with development**.
Item #1 (Meta App Review) is the long pole — **start it today.**

> Verify free-tier numbers when you sign up — providers change limits. Where a
> value is quoted below it was accurate at planning time (June 2026), not guaranteed.

---

## 🔴 START TODAY — the 2–4 week long pole

### 1. Meta / Instagram publishing access
This is what lets code post to Instagram. It gates everything.

- [ ] Convert your Instagram account to **Business** (not Creator — the Content
      Publishing API needs Business, and it's required for Reels later + full Insights).
- [ ] Create a **Facebook Page** and link the Instagram account to it.
- [ ] Go to **developers.facebook.com** → create an App (type: Business).
- [ ] Add the **Instagram Graph API** / **Instagram** product to the app.
- [ ] Complete **Business Verification** (Meta asks for business documents — this
      takes days; start immediately).
- [ ] Submit **App Review** for `instagram_content_publish` (and `instagram_basic`,
      `pages_show_list`). You record a screencast showing how the permission is used.
      Approval is typically **2–4+ weeks**.
- [ ] While waiting: add **yourself as a tester** so you can develop against your
      own account at Standard Access.

Capture these into `.env` when issued:
`META_APP_ID`, `META_APP_SECRET`, `IG_USER_ID`, `IG_ACCESS_TOKEN` (long-lived, 60-day).

### 2. Privacy policy URL (required by App Review)
- [ ] Publish a privacy policy page (free on **GitHub Pages**). App Review will not
      pass without a reachable privacy-policy URL.

---

## 🟠 This week

### 3. Database — Neon (durable state, fixes the data-loss bug)
- [ ] Sign up at **neon.tech** (free tier, no card, non-expiring).
- [ ] Create a project + database. Copy the connection string.
- [ ] Put it in `.env` as `DATABASE_URL=postgresql+psycopg://...?sslmode=require`.
- [ ] ❌ Do **not** use Render's own free Postgres — it is deleted ~30 days after creation.

### 4. Caption AI — Google Gemini
- [ ] Go to **aistudio.google.com** → "Get API key".
- [ ] Confirm the current free model + limits, set `GEMINI_MODEL` accordingly
      (planned default: `gemini-2.5-flash-lite`).
- [ ] Put the key in `.env` as `GEMINI_API_KEY`.
- [ ] (Fallback) Create a free **Groq** key at console.groq.com → `GROQ_API_KEY`.

### 5. Daily trigger — external cron (fixes the "never fires while asleep" bug)
- [ ] Sign up at **cron-job.org** (free, unlimited jobs, failure alerts).
- [ ] Create a daily job (your post time) that POSTs to
      `https://<your-app>/run-daily` with header `X-Run-Token: <RUN_TOKEN>`.
- [ ] Create a second job every ~5 min POSTing to `/run-poller` (publishes due,
      approved posts).
- [ ] Generate a long random `RUN_TOKEN` and put it in `.env`.

### 6. Hosting — Render
- [ ] Sign up at **render.com**, connect your GitHub repo.
- [ ] Create a **Web Service** (free), root directory `instagram-autopost`.
- [ ] Add all `.env` values as Render environment variables (not committed).

---

## 🟢 Before go-live

### 7. Image hosting (Graph API needs a public image URL)
- [ ] Decide host: **GitHub Pages** (free, primary) and/or **Cloudinary** free tier
      as fallback. Set `PUBLIC_ASSET_BASE_URL` and/or `CLOUDINARY_URL`.

### 8. Notifications & monitoring
- [ ] **Telegram bot** (primary alerts): create via @BotFather → `TELEGRAM_BOT_TOKEN`,
      and get your `TELEGRAM_CHAT_ID`.
- [ ] **Gmail** (secondary): enable 2FA, create an **App Password** → `SMTP_*`.
- [ ] **Healthchecks.io** dead-man's-switch (free) → `HEALTHCHECK_PING_URL`.
- [ ] **Sentry** (free 5k errors/mo) → `SENTRY_DSN`.

### 9. Account warming (anti-ban, fixes the "day-one bot" signal)
- [ ] For **1–2 weeks before** automation turns on: manually post ~1/day, follow a
      few relevant accounts, engage. A brand-new account that suddenly auto-posts
      from a datacenter IP looks like spam to Meta.

### 10. Attribution (Sprint 5 — DONE in code, needs your build + verify)
The Android app now captures the Play Install Referrer and reports `utm_content`
to Firebase. Files changed: `MainApplication.kt`, `utils/preferences/SharedPrefs.kt`,
new `attribution/InstallReferrerHelper.kt`.
- [ ] Build the app in **Android Studio** and confirm it compiles + runs.
- [ ] Test referrer capture with a Play referrer test URL (Play Console → "Create
      install referrer URL"), or `adb` broadcast of `INSTALL_REFERRER`.
- [ ] (Optional, free) Deploy the **Cloudflare Workers shortener** in
      `infra/cloudflare_shortener/` for first-party click counts, then set
      `PUBLIC_ASSET_BASE_URL` to its domain.
- [ ] Feed installs back via `POST /ingest/installs` (RevenueCat webhook) and/or
      `attribution.import_play_console_csv(path)` from a Play Console export.

---

## Where each value lands

| `.env` key | From step |
|---|---|
| `DATABASE_URL` | 3 (Neon) |
| `GEMINI_API_KEY`, `GROQ_API_KEY` | 4 |
| `RUN_TOKEN` | 5 |
| `META_APP_ID`, `META_APP_SECRET`, `IG_USER_ID`, `IG_ACCESS_TOKEN` | 1 |
| `PUBLIC_ASSET_BASE_URL`, `CLOUDINARY_URL` | 7 |
| `TELEGRAM_*`, `SMTP_*`, `HEALTHCHECK_PING_URL`, `SENTRY_DSN` | 8 |

Keep `DRY_RUN=true` until step 1 is approved and you've watched a few posts go
through the dashboard correctly. Flip to `false` to publish for real.
