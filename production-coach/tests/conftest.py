"""Put the project root on sys.path so `import coach_engine` works in tests
without installing the package. Offline by design — no network, no LLM."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
