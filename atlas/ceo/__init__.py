"""The CEO surface: Atlas's business state, work cycle, and scheduler.

This is where Atlas stops being a showrunner-for-hire and becomes the CEO who owns
a revenue goal. The package holds the durable business state (state.py), the
single-action work cycle (cycle.py), and a capped scheduler (run via cycle.py).
The CEO comms it relies on — the journal, the request queue, and the STOP
kill-switch — live in boundary.py alongside state.json under boundary.CEO_DIR.
"""
