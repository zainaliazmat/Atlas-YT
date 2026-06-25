"""The Claude seam retries TRANSIENT API hiccups (server_error/overloaded/5xx/connection)
with backoff so a single blip doesn't fail a whole pipeline stage — but a non-transient
error raises immediately (no masking real failures)."""
import llm


def _install(monkeypatch, behaviors):
    """behaviors: list of either an Exception to raise or a str to return, per attempt."""
    calls = {"n": 0}

    def fake_async(system, user):
        i = calls["n"]
        calls["n"] += 1
        b = behaviors[i]
        if isinstance(b, Exception):
            raise b

        async def ok():
            return b
        return ok()

    monkeypatch.setattr(llm, "_claude_chat_async", lambda s, u, model=None: fake_async(s, u))
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    return calls


def test_transient_server_error_is_retried_then_succeeds(monkeypatch):
    err = RuntimeError("Claude returned an error: server_error")
    calls = _install(monkeypatch, [err, err, "RECOVERED"])
    assert llm._chat_claude("sys", "hi") == "RECOVERED"
    assert calls["n"] == 3


def test_non_transient_error_raises_immediately(monkeypatch):
    calls = _install(monkeypatch, [RuntimeError("some other fatal error")])
    try:
        llm._chat_claude("sys", "hi")
        assert False, "should have raised"
    except RuntimeError as e:
        assert "fatal" in str(e)
    assert calls["n"] == 1  # no wasted retries on a real failure


def test_gives_up_after_max_attempts(monkeypatch):
    err = RuntimeError("overloaded: server_error")
    calls = _install(monkeypatch, [err, err, err, err])
    try:
        llm._chat_claude("sys", "hi")
        assert False, "should have raised after exhausting retries"
    except RuntimeError:
        pass
    assert calls["n"] == 4  # 1 initial + 3 retries
