# Cron Setup

Your cron-job.org screenshot shows:

- `run-poller` succeeded quickly.
- `run-daily` failed with `timeout (30s)`.
- Both jobs show `Inactive`, so cron-job.org will not run them again until you enable them.

## Why `run-daily` timed out

`/run-daily` waits for the full post generation flow. On Render free hosting, the app may first wake from sleep, then generate caption/images. That can take more than cron-job.org's 30 second timeout.

Use the fast cron endpoint instead:

```text
POST https://fieldcalc-autopost-new.onrender.com/cron/run-daily
Header: X-Run-Token: <your RUN_TOKEN>
```

This endpoint returns quickly with `status: accepted`, then creates the post in the background.

## Recommended cron-job.org jobs

### 1. Daily post generation

- Title: `FieldCalc run-daily`
- URL: `https://fieldcalc-autopost-new.onrender.com/cron/run-daily`
- Method: `POST`
- Header name: `X-Run-Token`
- Header value: your Render `RUN_TOKEN`
- Schedule: every day at `08:00`
- Timezone: set cron-job.org timezone to your desired timezone, e.g. `Asia/Kolkata`

After it runs, open the dashboard and check for a new post in approval.

If you want the post to go live automatically without review, set this on Render:

```text
AUTO_APPROVE=true
DRY_RUN=false
```

Then keep the `run-poller` job active. `run-daily` creates/approves the post, and
`run-poller` publishes it when it is due.

Consequence: with `AUTO_APPROVE=true`, a bad caption/image can publish without
human review. Keep `AUTO_APPROVE=false` if you want the safer approve-and-post flow.

### 2. Publish approved posts

- Title: `run-poller`
- URL: `https://fieldcalc-autopost-new.onrender.com/run-poller`
- Method: `POST`
- Header name: `X-Run-Token`
- Header value: your Render `RUN_TOKEN`
- Schedule: every 5 minutes

This job publishes posts only after they are approved and due.

## Manual test

From this folder:

```bash
APP_BASE_URL=https://fieldcalc-autopost-new.onrender.com RUN_TOKEN=your-token ./.venv/bin/python scripts/run_cron_task.py daily
```

Expected response:

```json
{
  "status": "accepted",
  "message": "Daily post generation started in background. Check the dashboard in a few minutes."
}
```

Then wait a few minutes and open the dashboard.

## Important

If cron-job.org shows `Inactive`, open each job, fix the URL/header if needed, and enable/save it again.
