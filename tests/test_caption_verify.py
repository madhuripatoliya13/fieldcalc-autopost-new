"""Caption engine + verification gates (local provider, no API keys)."""
from app import caption as caption_engine
from app import llm, verify
from app.feature_picker import load_angles, load_features
from app.database import Post, PostStatus, SessionLocal


def _ff():
    return load_features()[0], load_angles()[0]


def test_local_provider_when_no_keys():
    assert llm.provider() == "local"


def test_caption_has_required_shape():
    f, a = _ff()
    r = caption_engine.generate(f, a)
    assert r["caption"].strip()
    assert 1 <= len(r["hashtags"]) <= 5          # C5
    assert all(h.startswith("#") for h in r["hashtags"])
    assert r["alt_text"]
    assert r["variant_count"] == caption_engine.N_VARIANTS  # multi-variant ran


def test_verify_passes_clean_caption():
    rep = verify.run("Measure any field in minutes. Save this for your next job.", ["#FieldCalc"])
    assert rep["ok"] is True
    # AI-label reminder is always present as a warning (C6).
    assert any("AI-content label" in w for w in rep["warnings"])


def test_verify_blocks_false_claims():
    rep = verify.run("This app is 100% accurate and guaranteed to be the #1 best app ever.", ["#x"])
    assert rep["ok"] is False
    assert any("claim violation" in b for b in rep["blocking"])


def test_verify_blocks_too_many_hashtags():
    tags = [f"#{i}" for i in range(8)]
    rep = verify.run("A perfectly fine caption.", tags)
    assert rep["ok"] is False
    assert any("too many hashtags" in b for b in rep["blocking"])


def test_verify_flags_trademark_as_warning_not_block():
    rep = verify.run("Works great with Google Maps for your trips.", ["#FieldCalc"])
    assert rep["ok"] is True  # warn, not block
    assert any("review wording" in w for w in rep["warnings"])


def test_verify_blocks_duplicate_caption():
    text = "Measure your land the easy way. Save this for later."
    with SessionLocal() as s:
        s.add(Post(post_date="dup-day", feature_id="x", angle_id="y",
                   caption=text, caption_hash=verify.caption_hash(text),
                   status=PostStatus.PUBLISHED))
        s.commit()
    rep = verify.run(text, ["#FieldCalc"])
    assert rep["ok"] is False
    assert any("duplicate" in b for b in rep["blocking"])
