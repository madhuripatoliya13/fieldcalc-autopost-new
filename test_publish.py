"""One-shot smoke test: publish a single image to Instagram.

Validates the whole publish path (token -> Cloudinary upload -> create container
-> publish) without the caption engine / dashboard. Run from the project root:

    source .venv/bin/activate
    python3 test_publish.py

Requires DRY_RUN=false and a valid IG_ACCESS_TOKEN / IG_USER_ID / CLOUDINARY_URL.
"""
from app.config import get_settings
from app.database import PostFormat
from app import instagram

settings = get_settings()

# Use an existing generated image (single 1080x1080 is simplest for a first post).
IMAGE = "generated/test_voice_single.png"
CAPTION = "FieldCalc — GPS Area Measure & Voice Navigation. Test post ✅"

print("dry_run:", settings.dry_run, "| graph:", instagram.GRAPH)
print("ig_user_id:", settings.ig_user_id)

print("\n1) creating media container (uploads image to Cloudinary first)...")
creation_id = instagram.create_container([IMAGE], CAPTION, PostFormat.SINGLE)
print("   creation_id:", creation_id)

print("2) publishing...")
media_id = instagram.publish(creation_id)
print("   media_id:", media_id)

print("3) fetching permalink...")
print("   LIVE AT:", instagram.get_permalink(media_id))
print("\n✅ Done — check @vasundhara_test on Instagram.")
