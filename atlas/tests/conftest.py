"""Put atlas/ on sys.path so the package's bare imports resolve under pytest.

Atlas runs with its own directory as the working dir (like the sibling agents), so
its modules import each other by bare name (`import registry`, `import llm`). This
makes that work no matter where pytest is invoked from.
"""
import pathlib
import sys

ATLAS_DIR = str(pathlib.Path(__file__).resolve().parent.parent)
if ATLAS_DIR not in sys.path:
    sys.path.insert(0, ATLAS_DIR)
