"""Independently verify an exported demo audit: python verify_audit.py export.json."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from domain import verify

if __name__ == "__main__":
    document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result = verify(document["events"])
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["valid"] else 1)
