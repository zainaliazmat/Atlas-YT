"""The event-loop-nesting mitigation is LOAD-BEARING, not incidental.

The sibling engines call `asyncio.run()` inside their `llm.chat()`. Atlas's tools run
on the SDK's already-running loop, so a direct call would raise
`RuntimeError: asyncio.run() cannot be called from a running event loop`. Dispatching
via `asyncio.to_thread` gives the sibling a worker thread with no running loop, so its
`asyncio.run()` creates a fresh loop cleanly. This test proves BOTH halves.
"""
import asyncio


def _sync_job_that_uses_asyncio_run():
    """Stands in for a sibling engine: synchronous, but spins its own loop."""
    async def inner():
        return 42
    return asyncio.run(inner())


def test_direct_call_inside_running_loop_raises_but_to_thread_works():
    async def driver():
        raised = False
        try:
            _sync_job_that_uses_asyncio_run()         # nested loop -> must raise
        except RuntimeError:
            raised = True
        value = await asyncio.to_thread(_sync_job_that_uses_asyncio_run)  # clean loop
        return raised, value

    raised, value = asyncio.run(driver())
    assert raised is True       # the mitigation is necessary (not a no-op)
    assert value == 42          # the mitigation actually works
