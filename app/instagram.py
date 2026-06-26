"""Meta Graph API client for Instagram content publishing.

Publishing is the documented two-step dance:
  1. create a media *container* (returns a creation_id)
  2. publish that container (returns the final media id)

Carousels add a layer: create one child container per slide, then a parent
container referencing the children, then publish the parent.

Idempotency (C3): the caller persists creation_id BEFORE publishing. If a publish
attempt crashes and retries, we publish the SAME container instead of creating a
new one — so a timeout-then-retry can never double-post.

DRY_RUN (C7): when settings.dry_run is True, no HTTP is sent; we return fake ids and
log what *would* have happened. This is also the manual-post degraded mode — the
dashboard still shows the finished asset so a human can post by hand.
"""
from __future__ import annotations

import logging
import time

import certifi
import requests

from app.config import get_settings
from app.database import PostFormat
from app.retries import PermanentError, TransientError, resilient

log = logging.getLogger("autopost")
settings = get_settings()

# Pin the CA bundle: some macOS Python builds ship an old LibreSSL whose default
# trust store fails to verify Meta's certs. certifi gives a consistent bundle.
# We use `requests` (urllib3) rather than httpx — httpx trips on old LibreSSL
# while requests verifies cleanly with the same bundle (Cloudinary proves it).
_VERIFY = certifi.where() if settings.ssl_verify else False

if not settings.ssl_verify:
    import urllib3

    urllib3.disable_warnings()  # silence the insecure-request noise when verify is off
    log.warning("SSL verification DISABLED — testing mode only, do not use in production")

GRAPH = settings.graph_base_url


def _token() -> str:
    # Sprint 1 reads from settings; once stored, token_refresh keeps it fresh in DB.
    return settings.ig_access_token


def _public_url(image: str) -> str:
    """Meta fetches images by public HTTPS URL — it can't read local files. Upload
    local PNG paths to Cloudinary and return the hosted URL. Already-public URLs
    (http/https) pass through unchanged."""
    if image.startswith("http://") or image.startswith("https://"):
        return image
    if not settings.cloudinary_url:
        raise PermanentError(
            f"image '{image}' is local and CLOUDINARY_URL is not set — "
            "Meta needs a public image URL to publish."
        )
    import os

    import cloudinary
    import cloudinary.uploader

    # The SDK parses the cloudinary:// URL from this env var natively; the
    # cloudinary_url= kwarg is unreliable across SDK versions.
    os.environ["CLOUDINARY_URL"] = settings.cloudinary_url
    cloudinary.reset_config()
    cloudinary.config(secure=True)
    try:
        res = cloudinary.uploader.upload(image, folder="fieldcalc_ig")
    except Exception as e:  # noqa: BLE001
        raise TransientError(f"cloudinary upload failed: {e}") from e
    return res["secure_url"]


def _post(path: str, data: dict) -> dict:
    """One resilient POST to the Graph API with sane error classification."""

    @resilient()
    def _do() -> dict:
        try:
            r = requests.post(f"{GRAPH}/{path}", data={**data, "access_token": _token()}, timeout=30, verify=_VERIFY)
        except requests.RequestException as e:
            raise TransientError(f"network: {e}") from e
        if r.status_code >= 500 or r.status_code == 429:
            raise TransientError(f"{r.status_code}: {r.text}")
        if r.status_code >= 400:
            _log_meta_error(r)
            raise PermanentError(f"{r.status_code}: {r.text}")
        return r.json()

    return _do()


def _log_meta_error(r: requests.Response) -> None:
    """Log the full Meta error payload so fbtrace_id / error_subcode appear in Render logs."""
    try:
        err = r.json().get("error", {})
        log.error(
            "Meta API error: code=%s subcode=%s type=%s fbtrace=%s msg=%s",
            err.get("code"),
            err.get("error_subcode"),
            err.get("type"),
            err.get("fbtrace_id"),
            err.get("message"),
        )
    except Exception:  # noqa: BLE001
        log.error("Meta API error (raw): %s", r.text[:500])


def _get(path: str, params: dict) -> dict:
    @resilient()
    def _do() -> dict:
        try:
            r = requests.get(f"{GRAPH}/{path}", params={**params, "access_token": _token()}, timeout=30, verify=_VERIFY)
        except requests.RequestException as e:
            raise TransientError(f"network: {e}") from e
        if r.status_code >= 500 or r.status_code == 429:
            raise TransientError(f"{r.status_code}: {r.text}")
        if r.status_code >= 400:
            _log_meta_error(r)
            raise PermanentError(f"{r.status_code}: {r.text}")
        return r.json()

    return _do()


def create_container(image_urls: list[str], caption: str, post_format: PostFormat) -> str:
    """Create the media container(s) and return the publishable creation_id."""
    if settings.dry_run:
        log.info("[DRY_RUN] create_container %s slides=%d", post_format.value, len(image_urls))
        return f"dryrun-container-{int(time.time())}"

    # Local PNG paths -> public Cloudinary URLs Meta can fetch.
    image_urls = [_public_url(u) for u in image_urls]
    uid = settings.ig_user_id
    if post_format == PostFormat.CAROUSEL:
        child_ids = []
        for url in image_urls:
            child = _post(f"{uid}/media", {"image_url": url, "is_carousel_item": "true"})
            child_ids.append(child["id"])
        parent = _post(
            f"{uid}/media",
            {"media_type": "CAROUSEL", "children": ",".join(child_ids), "caption": caption},
        )
        return parent["id"]

    if post_format == PostFormat.STORY:
        res = _post(f"{uid}/media", {"image_url": image_urls[0], "media_type": "STORIES"})
        return res["id"]

    # SINGLE
    res = _post(f"{uid}/media", {"image_url": image_urls[0], "caption": caption})
    return res["id"]


def _wait_until_ready(creation_id: str, timeout: int = 60, interval: int = 4) -> None:
    """Instagram processes a freshly-created container asynchronously. Publishing
    before it's FINISHED returns code 9007 ('media not ready'). Poll status_code
    until FINISHED, raising on ERROR/EXPIRED or timeout."""
    waited = 0
    while waited < timeout:
        status = _get(creation_id, {"fields": "status_code"}).get("status_code", "")
        if status == "FINISHED":
            return
        if status in {"ERROR", "EXPIRED"}:
            raise PermanentError(f"container {creation_id} status={status}")
        time.sleep(interval)
        waited += interval
    # Still not ready — transient so the poller retries (container persisted).
    raise TransientError(f"container {creation_id} not ready after {timeout}s")


def publish(creation_id: str) -> str:
    """Publish a previously created container; returns the final media id."""
    if settings.dry_run:
        log.info("[DRY_RUN] publish container=%s", creation_id)
        return f"dryrun-media-{creation_id.split('-')[-1]}"
    _wait_until_ready(creation_id)
    res = _post(f"{settings.ig_user_id}/media_publish", {"creation_id": creation_id})
    return res["id"]


def get_permalink(media_id: str) -> str:
    if settings.dry_run:
        return f"https://instagram.com/p/DRYRUN_{media_id[-8:]}"
    return _get(media_id, {"fields": "permalink"}).get("permalink", "")
