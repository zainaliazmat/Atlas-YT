"""atomic_write_json must be thread-safe: concurrent writers to the SAME path within one
process must not collide on the temp file (regression — the supervisor logs a decision from
the worker thread while an operator action writes the same project.json from the main thread).
"""
import json
import threading

import chat_state


def test_concurrent_writes_to_same_path_do_not_crash(tmp_path):
    path = tmp_path / "project.json"
    errors = []
    barrier = threading.Barrier(8)

    def writer(n):
        try:
            barrier.wait()                     # maximize overlap on the temp-file step
            for i in range(40):
                chat_state.atomic_write_json(path, {"writer": n, "i": i})
        except Exception as exc:               # noqa: BLE001 — collecting the race crash
            errors.append(repr(exc))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent writes raised: {errors[:3]}"
    # the file is intact, valid JSON written by some writer (last-writer-wins is acceptable)
    obj = json.loads(path.read_text())
    assert "writer" in obj and "i" in obj
    # no orphaned temp files left behind
    assert not list(tmp_path.glob("project.json.tmp*"))
