# Setup Journey — How We Took This Live

The complete, ordered record of every step that took this project from "testing
phase" to a **real post published on Instagram** from a cloud server. Use this to
reproduce the setup for another account/app, or to understand why each piece exists.

Date completed: **24 June 2026** · First live post: **@vasundhara_test**

---

## Phase 1 — Instagram & Meta setup (accounts)

1. **Converted Instagram to a Business account**
   IG app → Settings → Account type and tools → Switch to professional → **Business**.
   *(Creator won't work — the Content Publishing API requires Business.)*

2. **Created a Facebook Page** (`FieldCalc GPS` / `FieldCalc App`) at facebook.com/pages/create.
   - Hit "too many attempts" rate-limit once → waited and retried.

3. **Linked Instagram to the Facebook Page**
   Done via the Instagram authorization popup → "Adding this Instagram profile to your
   business portfolio" → **Add**. Result: `@vasundhara_test` connected under the
   **Vasundhara Qa** business portfolio.

4. **Created a Meta Developer account** at developers.facebook.com
   - Verified by phone (had to switch numbers via Accounts Center) and email.

5. **Created a Meta App** (type: Business) → **App ID `794449540420476`**.
   - Added the **Instagram** product → "API setup with Instagram business login".
   - This generated an **Instagram app** (ID `1796898418348694`) with its own secret.

6. **Assigned the Instagram Tester role**
   App roles → Roles → **Instagram Testers** → added `vasundhara_test` →
   **accepted the invite** inside the IG app. *(Without this, token generation fails
   with "Insufficient developer role".)*

7. **Generated the access token**
   Instagram → API setup → **Generate access tokens → Add account** → logged in as
   `vasundhara_test` → **Allow** (all scopes incl. publish content).
   - Got: **IG user ID `17841441331701486`** and a long-lived **`IGAA…` token**.
   - Note: a CSRF error appeared from mixed logins; solved by using a clean
     incognito window with only the Facebook owner account signed in.

---

## Phase 2 — Image hosting (Cloudinary)

8. **Why:** Meta's Graph API fetches the post image from a **public URL** — it can't
   read local files. So generated PNGs must be uploaded somewhere public first.

9. **Created a free Cloudinary account** → Dashboard → API Keys.
   - Built `CLOUDINARY_URL = cloudinary://<api_key>:<api_secret>@<cloud_name>`.
   - Cloud name: `dfmfqqwfs`.

10. **Wired Cloudinary into the code** (`instagram._public_url()`): any local image
    path is uploaded to Cloudinary and the returned `secure_url` is what we send to Meta.

---

## Phase 3 — Code changes to support the Instagram Login token

11. **Graph host switch:** Instagram Business Login tokens (`IGAA…`) use
    `graph.instagram.com`, not `graph.facebook.com`. Added a `GRAPH_BASE_URL` setting
    defaulting to `https://graph.instagram.com/v21.0`.

12. **HTTP client switch:** the office Mac's old LibreSSL made `httpx` fail TLS. Switched
    the Graph API calls to **`requests`** + `certifi` bundle (same library Cloudinary
    uses successfully). Added `requests` and `certifi` to requirements.

13. **`SSL_VERIFY` toggle** added (kept `true`) — only for bypassing a TLS-intercepting
    proxy during testing.

---

## Phase 4 — The networking reality (important)

14. **Discovered the office network blocks Instagram.** A **Sophos firewall** classifies
    `graph.instagram.com` as "Social Networking" and returns a "Blocked site" page.
    This means the bot **cannot run from the office network at all** — it must run in
    the cloud. (Cloudinary and GitHub were not blocked, which is why those worked.)

    → Conclusion: deploy to **Render** (cloud), where Instagram is reachable.

---

## Phase 5 — Get the code onto GitHub

15. The project's existing git remote was an **internal company server**
    (`20.0.0.3:5000`) that Render can't reach → needed the code on **GitHub**.

16. **Created a GitHub repo** `madhuripatoliya13/fieldcalc-autopost-new` (private).

17. **Pushed the `instagram-autopost` folder** as a standalone repo.
    - Used a **Personal Access Token (classic)** with `repo` scope for auth
      (GitHub no longer accepts passwords).
    - Removed `.github/workflows/ci.yml` (token lacked `workflow` scope; CI not needed).
    - `.env`, `.venv/`, `generated/`, and the DB are git-ignored → no secrets pushed.

---

## Phase 6 — Deploy on Render

18. **Created a Render Web Service** from the GitHub repo (Free instance).
    - Build: `pip install -r requirements.txt`
    - Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

19. **First build failed:** Render defaulted to **Python 3.14**, and `pydantic-core`
    had no prebuilt wheel → tried to compile Rust → failed on read-only FS.
    **Fix:** added `.python-version` = `3.12.8`. Rebuild succeeded.

20. **Added all environment variables** in Render (via "Add from .env"):
    `DRY_RUN=false`, `GRAPH_BASE_URL`, `IG_USER_ID`, `IG_ACCESS_TOKEN`, `META_APP_ID`,
    `META_APP_SECRET`, `CLOUDINARY_URL`, `RUN_TOKEN`, `DASHBOARD_PASSWORD`.

21. Service went **Live** at `https://fieldcalc-autopost-new.onrender.com`.

---

## Phase 7 — First publish (and the bug we fixed)

22. **Generated a post:** `POST /run-daily` → `{"status":"drafted","post_id":1}`.

23. **First publish attempt FAILED** with Meta error:
    ```
    code 9007 / "The media is not ready for publishing, please wait for a moment"
    ```
    **Cause:** we called `media_publish` immediately after creating the container, but
    Instagram processes containers asynchronously.

24. **Fix:** added `instagram._wait_until_ready()` — polls the container's `status_code`
    until `FINISHED` (or errors/timeout) before publishing. Also added a `/requeue/{id}`
    endpoint to retry FAILED posts.

25. **Re-ran the pipeline** → `{"status":"ok","published":[1],"failed":[]}` →
    **post went LIVE on @vasundhara_test.** 🎉

    *(Note: the free-tier SQLite DB reset on redeploy, so post #1 was regenerated fresh —
    a reminder to add Neon Postgres for durable state.)*

---

## Phase 8 — Approval UX change

26. Changed the dashboard so **clicking "Approve" publishes to Instagram immediately**
    (review-then-post in one click), instead of only marking it approved for a later poller.

---

## What's still recommended (not yet done)

- [ ] **Revoke the GitHub Personal Access Token** that was used (it was pasted in chat).
- [ ] **Add Neon Postgres** (`DATABASE_URL`) so posts/history survive Render redeploys.
- [ ] **Set up cron-job.org** to hit `/run-daily` daily and `/run-poller` periodically.
- [ ] **Fix image quality** — the phone-mockup screen renders blank on Render (no Chromium);
      either embed the real app screenshot or use a clean no-phone design.
- [ ] **Meta App Review** for `instagram_content_publish` to post to non-tester accounts
      (currently works for the tester account `@vasundhara_test` only).
- [ ] **Rotate the 60-day Instagram token** before it expires (or wire token refresh for
      the Instagram Login flow via `graph.instagram.com/refresh_access_token`).
