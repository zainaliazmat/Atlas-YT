"""Deterministic objective analyzers (no LLM).

Each module exposes ``analyze(ctx: EvalContext) -> list[Measurement]``:
  audio.py   master.wav / audio_manifest -> loudness, peak, ducking, SNR, SFX
  video.py   video.mp4 / composition_manifest -> motion, cut rhythm, av-sync
  text.py    script / storyboard / asset_manifest / transcript -> structure
"""
