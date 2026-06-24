/**
 * Free first-party link shortener + click counter on Cloudflare Workers + D1.
 * Replaces Bitly (now paywalled). Redirects /go/{utm_content} to the Play Store
 * referrer URL and logs one click row to D1 so you get mid-funnel click data.
 *
 * Deploy (all free tier):
 *   1. npm create cloudflare@latest ig-shortener
 *   2. Create a D1 database:  wrangler d1 create ig_clicks
 *   3. In wrangler.toml bind it as DB, and set PLAY_URL var to your Play Store URL.
 *   4. Run the schema in schema.sql, then `wrangler deploy`.
 *   5. Point PUBLIC_ASSET_BASE_URL in the Python app at this Worker's domain.
 */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const match = url.pathname.match(/^\/go\/(.+)$/);
    if (!match) return new Response("not found", { status: 404 });

    const utmContent = decodeURIComponent(match[1]);
    // Log the click (best-effort; never block the redirect).
    try {
      await env.DB.prepare(
        "INSERT INTO clicks (utm_content, ts, ua) VALUES (?, ?, ?)"
      ).bind(utmContent, Date.now(), request.headers.get("user-agent") || "").run();
    } catch (_) { /* ignore */ }

    const referrer =
      `utm_source=instagram&utm_medium=social&utm_content=${utmContent}`;
    const target = `${env.PLAY_URL}&referrer=${encodeURIComponent(referrer)}`;
    return Response.redirect(target, 302);
  },
};
