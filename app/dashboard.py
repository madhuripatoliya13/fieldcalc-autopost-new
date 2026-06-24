"""Human-approval web dashboard (the 'simple web dashboard' approval surface).

Password-protected via HTTP Basic. Shows the pending review queue with image
preview, editable caption + hashtags, verification badges, and Approve / Reject /
Regenerate actions. Plus history and an insights page.
"""
from __future__ import annotations

import secrets
import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app import attribution, creative_strategy, generation_state, learning, pipeline
from app.config import get_settings
from app.database import Post, PostStatus, SessionLocal
from app.feature_picker import load_angles, load_features

settings = get_settings()
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "dashboard_templates"))
_basic = HTTPBasic()


def require_dashboard(creds: HTTPBasicCredentials = Depends(_basic)) -> None:
    ok = secrets.compare_digest(creds.password, settings.dashboard_password)
    if not ok:
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})


def _img_urls(post: Post) -> list[str]:
    urls = []
    for u in post.image_urls or []:
        urls.append(u if u.startswith("http") else f"/generated/{Path(u).name}")
    return urls


def _strategy_for(post: Post) -> dict:
    traits = post.traits or {}
    feature, angle = traits.get("_feature"), traits.get("_angle")
    latest = creative_strategy.select(feature, angle, post.pillar) if feature and angle else {}
    if traits.get("creative_strategy"):
        strategy = {**latest, **dict(traits["creative_strategy"])}
        if "visual_variant" not in strategy and traits.get("visual_variant") is not None:
            strategy["visual_variant"] = traits["visual_variant"]
        labels = strategy.get("variant_labels") or []
        variant = strategy.get("visual_variant")
        if variant is not None and labels:
            try:
                strategy["visual_variant_label"] = labels[int(variant) % len(labels)]
            except (TypeError, ValueError):
                strategy["visual_variant_label"] = "Auto"
        return strategy
    if latest:
        strategy = latest
        if traits.get("visual_variant") is not None:
            strategy["visual_variant"] = traits["visual_variant"]
        labels = strategy.get("variant_labels") or []
        variant = strategy.get("visual_variant")
        if variant is not None and labels:
            try:
                strategy["visual_variant_label"] = labels[int(variant) % len(labels)]
            except (TypeError, ValueError):
                strategy["visual_variant_label"] = "Auto"
        return strategy
    return {}


def _notice_url(message: str, kind: str = "info") -> str:
    return f"/?notice={quote(message)}&notice_type={quote(kind)}"


def _plan_notice_url(message: str, kind: str = "info") -> str:
    return f"/plan?notice={quote(message)}&notice_type={quote(kind)}"


def _visual_manifest() -> dict:
    path = Path(__file__).resolve().parent.parent / "data" / "visual_assets.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _image_note(post: Post) -> dict:
    # AI-generated posts are written as "{post_key}_ai.png". Detect from the saved path.
    if any(str(u).endswith("_ai.png") for u in (post.image_urls or [])):
        return {
            "kind": "ai",
            "label": "AI generated",
            "detail": "Photorealistic scenario generated with FLUX (Pollinations.ai) plus a clean text overlay.",
        }
    visuals = _visual_manifest().get("features", {}).get(post.feature_id, {})
    if visuals.get("realistic_asset"):
        return {
            "kind": "premium",
            "label": "Curated realistic asset",
            "detail": "Uses a live-ready realistic base and creates a fresh branded variant for the post.",
        }
    return {
        "kind": "enhanced",
        "label": "Enhanced screenshot poster",
        "detail": "Built from the real app screenshot, feature scenario, caption angle, and 3D-style vector elements.",
    }


@router.get("/", response_class=HTMLResponse, dependencies=[Depends(require_dashboard)])
def index(request: Request):
    with SessionLocal() as s:
        pending = s.query(Post).filter(Post.status == PostStatus.PENDING_APPROVAL).order_by(Post.id).all()
        cards = [{
            "p": p, "images": _img_urls(p),
            "v": p.verification or {}, "link": (p.traits or {}).get("link", ""),
            "strategy": _strategy_for(p),
            "image_status": (p.traits or {}).get("image_regeneration", {}),
            "image_note": _image_note(p),
        } for p in pending]
        recent_posts = s.query(Post).filter(Post.status != PostStatus.PENDING_APPROVAL).order_by(Post.id.desc()).all()
        recent = [{"p": p, "images": _img_urls(p), "link": (p.traits or {}).get("link", "")} for p in recent_posts]
    return templates.TemplateResponse(request, "index.html", {
        "cards": cards,
        "recent": recent,
        "notice": request.query_params.get("notice", ""),
        "notice_type": request.query_params.get("notice_type", "info"),
        "app_name": settings.app_name,
    })


@router.get("/plan", response_class=HTMLResponse, dependencies=[Depends(require_dashboard)])
def plan_page(request: Request):
    manifest = _visual_manifest()
    visual_features = manifest.get("features", {})
    features = load_features()
    angles = load_angles()
    state = generation_state.load()
    reset_after = int(state.get("reset_after_post_id") or 0)
    with SessionLocal() as s:
        posts = s.query(Post).filter(Post.pillar == "feature", Post.id > reset_after).order_by(Post.id).all()

    post_map: dict[tuple[str, str], list[Post]] = {}
    for post in posts:
        post_map.setdefault((post.feature_id, post.angle_id), []).append(post)

    rows = []
    for feature in features:
        visuals = visual_features.get(feature["id"], {})
        angle_cells = []
        used_count = 0
        for angle in angles:
            used_posts = post_map.get((feature["id"], angle["id"]), [])
            if used_posts:
                used_count += 1
            angle_cells.append({
                "angle": angle,
                "posts": used_posts,
                "latest": used_posts[-1] if used_posts else None,
            })
        rows.append({
            "feature": feature,
            "has_realistic": bool(visuals.get("realistic_asset")),
            "has_screenshot": bool(visuals.get("screenshot")),
            "realistic_asset": visuals.get("realistic_asset", ""),
            "used_count": used_count,
            "angle_cells": angle_cells,
        })

    return templates.TemplateResponse(request, "plan.html", {
        "rows": rows,
        "angles": angles,
        "app_name": settings.app_name,
        "realistic_count": sum(1 for row in rows if row["has_realistic"]),
        "total_features": len(rows),
        "reset_after_post_id": reset_after,
        "reset_at": state.get("reset_at", ""),
        "notice": request.query_params.get("notice", ""),
        "notice_type": request.query_params.get("notice_type", "info"),
    })


@router.post("/ui/reset-generation", dependencies=[Depends(require_dashboard)])
def ui_reset_generation():
    result = pipeline.reset_generation_logic("Reset from dashboard: start feature-first rotation again.")
    return RedirectResponse(
        _plan_notice_url(f"Generation plan reset after post #{result['reset_after_post_id']}. Next post starts at Area Measurement / Tutorial.", "success"),
        status_code=303,
    )


@router.post("/ui/approve/{post_id}", dependencies=[Depends(require_dashboard)])
def ui_approve(post_id: int):
    result = pipeline.approve(post_id)
    if result.get("status") == "approved":
        return RedirectResponse(
            _notice_url(f"Post #{post_id} approved and scheduled. It moved to Recent activity.", "success"),
            status_code=303,
        )
    return RedirectResponse(_notice_url(f"Post #{post_id} was not approved: {result.get('reason', 'unknown')}", "error"), status_code=303)


@router.post("/ui/reject/{post_id}", dependencies=[Depends(require_dashboard)])
def ui_reject(post_id: int, reason: str = Form("")):
    result = pipeline.reject(post_id, reason)
    if result.get("status") == "rejected":
        return RedirectResponse(_notice_url(f"Post #{post_id} rejected. You can create a new post now.", "success"), status_code=303)
    return RedirectResponse(_notice_url(f"Post #{post_id} was not rejected: {result.get('reason', 'unknown')}", "error"), status_code=303)


@router.post("/ui/edit/{post_id}", dependencies=[Depends(require_dashboard)])
def ui_edit(post_id: int, caption: str = Form(...), hashtags: str = Form("")):
    tags = [t.strip() if t.strip().startswith("#") else f"#{t.strip()}"
            for t in hashtags.replace(",", " ").split() if t.strip()]
    result = pipeline.edit(post_id, caption, tags)
    kind = "success" if result.get("status") == "edited" else "error"
    return RedirectResponse(_notice_url(f"Post #{post_id} edits saved.", kind), status_code=303)


@router.post("/ui/regenerate/{post_id}", dependencies=[Depends(require_dashboard)])
def ui_regenerate(post_id: int):
    result = pipeline.regenerate(post_id)
    kind = "success" if result.get("status") == "edited" else "error"
    return RedirectResponse(_notice_url(f"Post #{post_id} text regenerated.", kind), status_code=303)


@router.post("/ui/regenerate-image/{post_id}", dependencies=[Depends(require_dashboard)])
def ui_regenerate_image(post_id: int):
    result = pipeline.regenerate_image(post_id)
    kind = "success" if result.get("status") == "image_regenerated" else "error"
    return RedirectResponse(_notice_url(f"Post #{post_id} image regenerated.", kind), status_code=303)


@router.post("/ui/create-post", dependencies=[Depends(require_dashboard)])
def ui_create_post():
    result = pipeline.create_review_post()
    if result.get("status") == "drafted":
        return RedirectResponse(_notice_url(f"New post #{result['post_id']} created and ready for review.", "success"), status_code=303)
    if result.get("status") == "exists":
        return RedirectResponse(_notice_url(f"Post #{result['post_id']} is already waiting for review.", "info"), status_code=303)
    return RedirectResponse(_notice_url(f"Could not create post: {result.get('reason', result.get('status', 'unknown'))}", "error"), status_code=303)


@router.get("/history", response_class=HTMLResponse, dependencies=[Depends(require_dashboard)])
def history(request: Request):
    with SessionLocal() as s:
        posts = s.query(Post).filter(
            Post.status.in_([PostStatus.PUBLISHED, PostStatus.REJECTED, PostStatus.FAILED])
        ).order_by(Post.id.desc()).limit(100).all()
        rows = [{"p": p} for p in posts]
    return templates.TemplateResponse(request, "history.html", {"rows": rows, "app_name": settings.app_name})


@router.get("/insights", response_class=HTMLResponse, dependencies=[Depends(require_dashboard)])
def insights_page(request: Request):
    ranked = learning.scored_posts()
    patterns = learning.winning_patterns()
    installs = attribution.installs_by_post()
    total = sum(r["installs"] for r in installs)
    return templates.TemplateResponse(request, "insights.html", {
        "ranked": ranked, "patterns": patterns,
        "total_installs": total, "app_name": settings.app_name,
    })
