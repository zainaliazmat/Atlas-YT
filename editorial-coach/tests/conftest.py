"""Put the project dir on sys.path so `import coach_engine` / `import llm` resolve
when tests run from anywhere — mirrors how the scriptwriter tests resolve imports
(they sys.path.insert the parent dir). Doing it in conftest keeps the test files
clean and means no network/LLM import side-effects from the package root.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
