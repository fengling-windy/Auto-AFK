import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "configs" / "BAAH" / "国际服挂机.json"


def main():
    data = json.loads(CFG.read_text(encoding="utf-8"))
    backup = CFG.with_suffix(".json.bak")
    if not backup.exists():
        shutil.copy2(CFG, backup)
    old = data.get("TARGET_EMULATOR_PATH", "")
    data["TARGET_EMULATOR_PATH"] = ""
    CFG.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
    print("old TARGET_EMULATOR_PATH:", old)
    print("new TARGET_EMULATOR_PATH:", data["TARGET_EMULATOR_PATH"])
    print("TARGET_PORT:", data.get("TARGET_PORT"), "TARGET_IP_PATH:", data.get("TARGET_IP_PATH"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
