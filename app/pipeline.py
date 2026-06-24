"""The orchestrator. Drives a post through the durable state machine:

  generate_daily()  -> picks feature+angle, builds assets, writes a DRAFTED row,
                       runs verification (stub in Sprint 1), moves to PENDING_APPROVAL,
                       notifies the human.
  approve(id)       -> PENDING_APPROVAL -> APPROVED (+publish_at). Instant DB flip.
  reject(id, why)   -> -> REJECTED (planner moves to the next feature/angle).
  publish_due()     -> the poller: APPROVED & due -> PUBLISHING -> PUBLISHED,
                       idempotent on creation_id, respecting the daily publish cap.

Captions are placeholder text in Sprint 1; Sprint 2 replaces _draft_caption() with
the Gemini multi-variant + judge engine. Everything else here is production-shaped.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import func, select

from app import caption as caption_engine
from app import content_planner, creative_strategy, imaging, instagram, learning, links, notify, verify
from app import generation_state
from app.config import get_settings
from app.database import Post, PostFormat, PostStatus, SessionLocal
from app.retries import PermanentError

log = logging.getLogger("autopost")
settings = get_settings()


def _today_key() -> str:
    return date.today().isoformat()


def _now():
    return datetime.now(timezone.utc)


def _choose_format(angle: dict) -> PostFormat:
    """Pick the first enabled format the angle prefers; default CAROUSEL."""
    for f in angle.get("best_formats", []):
        if f in settings.formats_enabled:
            return PostFormat(f)
    return PostFormat.SINGLE


MAX_REGEN = 3


def _generate_verified_caption(feature: dict, angle: dict) -> tuple[dict, dict]:
    """Generate a caption and verify it, regenerating up to MAX_REGEN times on a
    BLOCK. Returns (caption_result, verification_report). The last attempt is kept
    even if still blocking — the human gate is the final backstop."""
    winners = learning.winning_patterns()
    result = report = None
    for attempt in range(MAX_REGEN + 1):
        result = caption_engine.generate(feature, angle, regen_seed=attempt, winners=winners)
        report = verify.run(result["caption"], result["hashtags"])
        if report["ok"]:
            report["attempts"] = attempt + 1
            return result, report
        log.warning("verify blocked (attempt %d): %s", attempt + 1, report["blocking"])
    report["attempts"] = MAX_REGEN + 1
    return result, report


def _visual_variant_for(feature_id: str, angle_id: str, post_id: int | None = None, regen_count: int = 0) -> int:
    """Choose one of the renderer's design families without relying on timestamp luck."""
    basis = f"{feature_id}:{angle_id}:{post_id or 0}"
    base = sum(ord(ch) for ch in basis)
    return (base + regen_count) % 5


def _draft_from_brief(today: str, brief: dict) -> dict:
    feature, angle, pillar = brief["feature"], brief["angle"], brief["pillar"]
    fmt = _choose_format(angle)
    strategy = creative_strategy.select(feature, angle, pillar)
    strategy["visual_variant"] = _visual_variant_for(feature["id"], angle["id"])

    safe_feature_id = feature["id"].replace(":", "-")
    post_key = f"{today}_{safe_feature_id}_{angle['id']}"
    result, report = _generate_verified_caption(feature, angle)
    image_urls = imaging.generate_assets(feature, angle, fmt, post_key, content=result, strategy=strategy)

    with SessionLocal() as s:
        post = Post(
            post_date=today,
            pillar=pillar,
            feature_id=feature["id"],
            angle_id=angle["id"],
            format=fmt,
            caption=result["caption"],
            hashtags=result["hashtags"],
            alt_text=result["alt_text"],
            image_urls=image_urls,
            caption_hash=verify.caption_hash(result["caption"]),
            verification=report,
            traits={
                "pillar": pillar,
                "hook_style": angle.get("hook_style"),
                "cta_style": angle.get("cta_style"),
                "format": fmt.value,
                "keywords": result.get("keywords", []),
                "variant_index": result.get("variant_index"),
                "provider": result.get("provider"),
                "creative_strategy": strategy,
                # Snapshots so the dashboard can regenerate any post (any pillar).
                "_feature": feature,
                "_angle": angle,
            },
            status=PostStatus.APPROVED if settings.auto_approve else PostStatus.PENDING_APPROVAL,
            publish_at=_now() if settings.auto_approve else None,
        )
        s.add(post)
        s.commit()
        post_id = post.id
        if image_urls:
            strategy = creative_strategy.select(feature, angle, pillar)
            strategy["visual_variant"] = _visual_variant_for(feature["id"], angle["id"], post_id)
            image_urls = imaging.generate_assets(
                feature,
                angle,
                fmt,
                f"{post_key}_{post_id}",
                content=result,
                strategy=strategy,
            )
            post.image_urls = image_urls
        # utm_content + UTM-tagged install link now that we have the id (C8 attribution).
        post.utm_content = f"{feature['id']}-{angle['id']}-{post_id}"
        t = dict(post.traits or {})
        t["link"] = links.short_link(post.utm_content)
        t["creative_strategy"] = strategy
        t["visual_variant"] = strategy["visual_variant"]
        post.traits = t
        s.commit()

    flag = "" if report["ok"] else " ⚠️ verification flagged issues"
    if settings.auto_approve:
        notify.send(
            "📸 New post auto-approved",
            f"{feature['name']} · {angle['name']} · {fmt.value}{flag}\nPublishing on the next poller run.",
        )
    else:
        notify.send(
            "📸 New post ready to review",
            f"{feature['name']} · {angle['name']} · {fmt.value}{flag}\nApprove in the dashboard.",
        )
    return {
        "status": "drafted",
        "post_id": post_id,
        "format": fmt.value,
        "verified": report["ok"],
        "auto_approved": settings.auto_approve,
    }


def generate_daily() -> dict:
    """Idempotent: at most one drafted post per calendar day (UNIQUE(post_date))."""
    today = _today_key()
    with SessionLocal() as s:
        existing = s.scalar(select(Post).where(Post.post_date == today))
        if existing:
            if existing.status != PostStatus.REJECTED:
                return {"status": "exists", "post_id": existing.id, "state": existing.status.value}
            existing.post_date = f"R{existing.id:09d}"
            s.commit()

    brief = content_planner.plan_today()
    if not brief:
        return {"status": "noop", "reason": "no content available"}
    return _draft_from_brief(today, brief)


def create_review_post() -> dict:
    """Manual dashboard action: create a fresh review post when none is pending."""
    today = _today_key()
    with SessionLocal() as s:
        pending = s.scalar(
            select(Post)
            .where(Post.status == PostStatus.PENDING_APPROVAL)
            .order_by(Post.id.desc())
        )
        if pending:
            return {"status": "exists", "post_id": pending.id, "state": pending.status.value}

        existing_today = s.scalar(select(Post).where(Post.post_date == today))
        if existing_today:
            existing_today.post_date = f"{existing_today.status.value[0]}{existing_today.id:09d}"
            s.commit()

    brief = content_planner.plan_feature_post()
    if not brief:
        return {"status": "noop", "reason": "no feature content available"}
    return _draft_from_brief(today, brief)


def reset_generation_logic(reason: str = "Reset generation logic") -> dict:
    """Start a fresh feature-first generation cycle without deleting history."""
    with SessionLocal() as s:
        pending = s.scalars(select(Post).where(Post.status == PostStatus.PENDING_APPROVAL)).all()
        for post in pending:
            post.status = PostStatus.REJECTED
            post.reject_reason = reason
            if post.post_date == _today_key():
                post.post_date = f"R{post.id:09d}"
        s.commit()
        max_id = s.scalar(select(func.max(Post.id))) or 0
    state = generation_state.set_reset_after(max_id)
    return {"status": "reset", "reset_after_post_id": state["reset_after_post_id"], "rejected_pending": [p.id for p in pending]}


def approve(post_id: int, publish_at: datetime | None = None) -> dict:
    with SessionLocal() as s:
        post = s.get(Post, post_id)
        if not post or post.status != PostStatus.PENDING_APPROVAL:
            return {"status": "error", "reason": "not pending"}
        post.status = PostStatus.APPROVED
        post.publish_at = publish_at or _now()
        s.commit()
        return {"status": "approved", "post_id": post_id, "publish_at": post.publish_at.isoformat()}


def reject(post_id: int, reason: str = "") -> dict:
    with SessionLocal() as s:
        post = s.get(Post, post_id)
        if not post:
            return {"status": "error", "reason": "not found"}
        post.status = PostStatus.REJECTED
        post.reject_reason = reason
        s.commit()
    return {"status": "rejected", "post_id": post_id}


def edit(post_id: int, caption: str, hashtags: list[str]) -> dict:
    """Human inline-edits caption/hashtags from the dashboard, then re-verify."""
    report = verify.run(caption, hashtags)
    with SessionLocal() as s:
        post = s.get(Post, post_id)
        if not post:
            return {"status": "error", "reason": "not found"}
        post.caption = caption
        post.hashtags = hashtags
        post.caption_hash = verify.caption_hash(caption)
        post.verification = report
        s.commit()
    return {"status": "edited", "post_id": post_id, "verified": report["ok"]}


def regenerate(post_id: int) -> dict:
    """Re-run the caption engine for a post (any pillar) using its stored snapshots."""
    with SessionLocal() as s:
        post = s.get(Post, post_id)
        if not post:
            return {"status": "error", "reason": "not found"}
        traits = post.traits or {}
        feature, angle = traits.get("_feature"), traits.get("_angle")
    if not feature or not angle:
        return {"status": "error", "reason": "no snapshot to regenerate from"}
    result, report = _generate_verified_caption(feature, angle)
    strategy = creative_strategy.select(feature, angle, traits.get("pillar", "feature"))
    updated = edit(post_id, result["caption"], result["hashtags"])
    with SessionLocal() as s:
        post = s.get(Post, post_id)
        if post:
            t = dict(post.traits or {})
            t["creative_strategy"] = strategy
            post.traits = t
            s.commit()
    return updated


def regenerate_image(post_id: int) -> dict:
    """Rebuild only the visual assets for a pending post, keeping caption/hashtags."""
    with SessionLocal() as s:
        post = s.get(Post, post_id)
        if not post:
            return {"status": "error", "reason": "not found"}
        if post.status != PostStatus.PENDING_APPROVAL:
            return {"status": "error", "reason": "not pending"}
        traits = post.traits or {}
        feature, angle = traits.get("_feature"), traits.get("_angle")
        if not feature or not angle:
            return {"status": "error", "reason": "no snapshot to regenerate from"}
        content = {
            "caption": post.caption or "",
            "hashtags": post.hashtags or [],
            "keywords": traits.get("keywords", []),
            "cta": traits.get("cta_style"),
        }
        post_format = post.format
        pillar = post.pillar
        t = dict(post.traits or {})
        regen_count = int(t.get("image_regenerate_count", 0)) + 1
        visual_variant = _visual_variant_for(feature["id"], angle["id"], post_id, regen_count)
        t["image_regeneration"] = {
            "status": "running",
            "started_at": _now().isoformat(),
            "message": "Generating image...",
        }
        t["image_regenerate_count"] = regen_count
        t["visual_variant"] = visual_variant
        post.traits = t
        s.commit()

    strategy = creative_strategy.select(feature, angle, pillar)
    strategy["visual_variant"] = visual_variant
    post_key = f"{_today_key()}_{feature['id']}_{angle['id']}_regen_image_{post_id}_{int(_now().timestamp())}"
    try:
        image_urls = imaging.generate_assets(
            feature,
            angle,
            post_format,
            post_key,
            content=content,
            strategy=strategy,
            use_realistic_asset=True,
        )
    except Exception as e:
        with SessionLocal() as s:
            post = s.get(Post, post_id)
            if post:
                t = dict(post.traits or {})
                t["image_regeneration"] = {
                    "status": "failed",
                    "finished_at": _now().isoformat(),
                    "message": f"Image generation failed: {e}",
                }
                post.traits = t
                s.commit()
        raise

    with SessionLocal() as s:
        post = s.get(Post, post_id)
        if not post:
            return {"status": "error", "reason": "not found"}
        post.image_urls = image_urls
        t = dict(post.traits or {})
        t["creative_strategy"] = strategy
        t["visual_variant"] = visual_variant
        t["image_regenerate_count"] = regen_count
        t["image_regenerated_at"] = _now().isoformat()
        t["image_regeneration"] = {
            "status": "completed",
            "finished_at": t["image_regenerated_at"],
            "message": f"Image regenerated successfully ({len(image_urls)} file{'s' if len(image_urls) != 1 else ''}).",
        }
        post.traits = t
        s.commit()
    return {"status": "image_regenerated", "post_id": post_id, "images": image_urls}


def _published_today(s) -> int:
    return s.scalar(
        select(func.count(Post.id)).where(
            Post.status == PostStatus.PUBLISHED,
            Post.post_date == _today_key(),
        )
    ) or 0


def publish_due() -> dict:
    """Poller. Publishes APPROVED posts whose publish_at <= now, idempotently."""
    published, failed = [], []
    with SessionLocal() as s:
        if _published_today(s) >= settings.ig_max_posts_per_day:
            return {"status": "capped", "published": 0}
        due = s.scalars(
            select(Post)
            .where(Post.status == PostStatus.APPROVED, Post.publish_at <= _now())
            .order_by(Post.publish_at)
        ).all()
        due_ids = [p.id for p in due]

    for pid in due_ids:
        # Lock: flip to PUBLISHING in its own txn so a second poller can't grab it.
        with SessionLocal() as s:
            post = s.get(Post, pid)
            if not post or post.status != PostStatus.APPROVED:
                continue
            post.status = PostStatus.PUBLISHING
            s.commit()

        try:
            with SessionLocal() as s:
                post = s.get(Post, pid)
                # Idempotency: reuse an existing container from a crashed attempt.
                if not post.creation_id:
                    post.creation_id = instagram.create_container(
                        post.image_urls, _full_caption(post), post.format
                    )
                    s.commit()
                media_id = instagram.publish(post.creation_id)
                post.ig_media_id = media_id
                post.permalink = instagram.get_permalink(media_id)
                post.status = PostStatus.PUBLISHED
                post.posted_at = _now()
                s.commit()
                published.append(pid)
            notify.send("✅ Posted to Instagram", f"Post #{pid} is live.")
        except PermanentError as e:
            _fail(pid, str(e))
            failed.append(pid)
        except Exception as e:  # noqa: BLE001
            _fail(pid, f"unexpected: {e}")
            failed.append(pid)

    return {"status": "ok", "published": published, "failed": failed}


def _full_caption(post: Post) -> str:
    tags = " ".join(post.hashtags or [])
    return f"{post.caption}\n\n{tags}".strip()


def _fail(post_id: int, reason: str) -> None:
    with SessionLocal() as s:
        post = s.get(Post, post_id)
        if post:
            post.status = PostStatus.FAILED
            post.reject_reason = reason
            s.commit()
    log.error("publish failed post=%s: %s", post_id, reason)
    notify.send("❌ Publish failed", f"Post #{post_id}: {reason}")
