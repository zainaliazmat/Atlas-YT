# studio/tests/test_archetype_token_parity.py
from studio.compose import archetypes as A
from studio.gate import parse as P


def test_registry_keys_are_in_the_closed_vocab():
    for k in A.REGISTRY:
        assert k in A.ARCHETYPES, f"{k} not in the closed archetype vocab"


def test_every_registered_archetype_token_is_known_to_motion_variety():
    # THE INVARIANT: a new archetype ships with its motion_variety token in the same commit.
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for a in A.REGISTRY:
        tok = A.token_for(a)
        assert tok in token_names, (
            f"archetype {a!r} emits token {tok!r} but it is not in gate.parse._BEAT_TOKENS")


def test_vocab_matches_iris_layouts():
    from studio import engines
    layouts = set(engines.iris_layouts())
    assert set(A.ARCHETYPES) <= layouts, "archetype vocab drifted from Iris LAYOUTS"
