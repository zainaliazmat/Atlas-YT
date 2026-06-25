"""Pure-unit tests for Mason's signature shader-transition layer (no browser/render).

Covers the closed vocabulary, the self-contained + determinism-wall-clean HTML, byte
stability, and the GLSL<->vocab parity that keeps the token list honest.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import shader_transition as st  # noqa: E402

_FROM = "data:image/png;base64,FROMAAA="
_TO = "data:image/png;base64,TOAAA=="


# ---- closed vocabulary -------------------------------------------------
def test_validate_shader_closed_vocab():
    assert all(st.validate_shader(s) for s in st.SHADER_TRANSITIONS)
    assert not st.validate_shader("wormhole")
    assert not st.validate_shader("")


def test_glsl_and_vocab_are_in_lockstep():
    # every token has a GLSL body and every GLSL body has a token (no orphans)
    assert set(st.SHADER_TRANSITIONS) == set(st._GLSL)


def test_build_rejects_unknown_shader():
    import pytest
    with pytest.raises(ValueError):
        st.build_transition_html(_FROM, _TO, "kaboom", 1920, 1080)


# ---- the emitted page --------------------------------------------------
def test_html_embeds_both_textures_and_is_self_contained():
    html = st.build_transition_html(_FROM, _TO, "whip-pan", 1920, 1080)
    assert _FROM in html and _TO in html            # both frames inlined
    assert "http://" not in html and "https://" not in html  # no remote refs
    assert "transition(" in html and "gl_FragColor" in html


def test_html_honors_the_determinism_wall():
    for shader in st.SHADER_TRANSITIONS:
        html = st.build_transition_html(_FROM, _TO, shader, 1280, 720)
        for tok in st.BANNED_TOKENS:
            assert tok not in html, f"banned token {tok!r} leaked into {shader}"


def test_html_is_byte_stable_for_same_inputs():
    a = st.build_transition_html(_FROM, _TO, "sdf-iris", 1920, 1080)
    b = st.build_transition_html(_FROM, _TO, "sdf-iris", 1920, 1080)
    assert a == b


def test_html_dimensions_are_baked_in():
    html = st.build_transition_html(_FROM, _TO, "glitch", 1234, 567)
    assert 'width="1234"' in html and 'height="567"' in html


# ---- progress stepping -------------------------------------------------
def test_progress_endpoints_and_monotonicity():
    assert st.progress_for_frame(0, 14) == 0.0
    assert st.progress_for_frame(13, 14) == 1.0
    seq = [st.progress_for_frame(i, 14) for i in range(14)]
    assert seq == sorted(seq) and len(set(seq)) == 14
    assert st.progress_for_frame(0, 1) == 1.0     # degenerate single frame


def test_chrome_flags_force_software_gl():
    flags = st.chrome_flags()
    assert "--enable-unsafe-swiftshader" in flags and "--headless=new" in flags
