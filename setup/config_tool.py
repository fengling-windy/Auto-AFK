import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afk_core import config, configsync


def main():
    parser = argparse.ArgumentParser(description="Auto-AFK config helper")
    parser.add_argument("action", choices=["import", "link", "sync-in", "sync-out", "restore"])
    parser.add_argument("--project", action="append", dest="projects")
    args = parser.parse_args()

    projects = config.registry().get("projects", {})
    keys = args.projects or list(projects.keys())
    result = {}
    for key in keys:
        spec = projects.get(key)
        if spec is None:
            result[key] = ["unknown project"]
            continue
        if args.action == "import":
            result[key] = configsync.import_release_config(key, spec)
        elif args.action == "link":
            result[key] = configsync.ensure_links(key, spec)
        elif args.action == "sync-in":
            result[key] = configsync.sync_in(key, spec)
        elif args.action == "sync-out":
            result[key] = configsync.sync_out(key, spec)
        else:
            result[key] = configsync.restore(key, spec)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
