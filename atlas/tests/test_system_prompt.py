"""The orchestration prompt carries the delegation contract; the persona/chat
prompt deliberately EXCLUDES it (so casual conversation stays on-voice)."""
from orchestrator import build_chat_system, build_orchestrator_system


def test_orchestration_prompt_has_playbook_and_tool_names():
    s = build_orchestrator_system()
    assert "DEFAULT PLAYBOOK" in s
    assert "scout_find_topics" in s and "sage_research" in s
    assert "operating contract" in s.lower()


def test_chat_persona_prompt_excludes_the_orchestration_contract():
    s = build_chat_system()
    assert "DEFAULT PLAYBOOK" not in s
    assert "operating contract" not in s.lower()
    # but it IS still Atlas (identity preserved)
    assert "Atlas" in s
