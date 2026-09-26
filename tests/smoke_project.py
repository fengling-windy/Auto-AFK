import logging
import sys

import afk_hook


def main():
    afk_hook.install(project="SmokeProject")
    logging.getLogger("smoke").info("starting smoke project")
    afk_hook.mark_stage("running")
    print("smoke project output", flush=True)
    raise ModuleNotFoundError("No module named 'afk_nonexistent_package'")


if __name__ == "__main__":
    sys.exit(main())
