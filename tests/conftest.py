import sys
from pathlib import Path

# Make the project root importable so `import sensors`, `import app`, etc. work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
