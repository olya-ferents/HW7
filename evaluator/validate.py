import json
import os
from pathlib import Path

from loader import load, set_hash, summarise

sets_dir = Path(os.environ.get(
    "EVAL_SETS_DIR", Path(__file__).resolve().parent.parent))
path = sets_dir / f"{os.environ.get('SET', 'l05_dialogs')}.jsonl"
print("set", path.name, "set_hash", set_hash(path))
print(json.dumps(summarise(load(path)), indent=2))
