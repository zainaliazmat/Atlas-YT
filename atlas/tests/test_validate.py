"""Validation rejects garbage before Atlas spends API calls on it."""
import validate


def test_accepts_real_niches_and_topics():
    for good in ("home espresso", "Roman history", "chess", "AI productivity tools"):
        assert validate.validate_niche(good)[0] is True
        assert validate.validate_topic(good)[0] is True


def test_rejects_empty_and_too_short():
    for bad in ("", "  ", "x", "ab"):
        assert validate.validate_niche(bad)[0] is False
        assert validate.validate_topic(bad)[0] is False


def test_rejects_symbols_only():
    ok, reason = validate.validate_niche("!@#$ 123")
    assert ok is False and "actual" in reason


def test_rejects_keyboard_smash_single_word():
    assert validate.validate_niche("asdfghjk")[0] is False
    assert validate.validate_topic("qwrtzxcv")[0] is False


def test_multiword_is_not_smash():
    # A space almost always means a real phrase, even with consonant clusters.
    assert validate.validate_niche("crypto strength training")[0] is True
