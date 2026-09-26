import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config

rules = config.repair_rules()["rules"]
rule = [r for r in rules if r["id"] == "missing-module"][0]
print("rule match:", repr(rule["match"]))
text = "ModuleNotFoundError: No module named 'afk_nonexistent_package'"
m = re.search(rule["match"], text, re.IGNORECASE | re.MULTILINE)
print("groups:", m.groups() if m else None)
print("expand:", m.expand(rule["actions"][0]["package"]) if m else None)
