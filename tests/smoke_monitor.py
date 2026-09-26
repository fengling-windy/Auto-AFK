import logging
import sys
import traceback

import afk_hook


def main():
    afk_hook.install(project="SmokeTest")
    afk_hook.mark_stage("stage-1")
    print("smoke stdout line", flush=True)
    logging.getLogger("smoke").warning("smoke warning line")
    try:
        1 / 0
    except ZeroDivisionError:
        afk_hook.mark_error("handled division error", detail=traceback.format_exc()[-200:])
    afk_hook.mark_done("smoke done")
    raise RuntimeError("smoke unhandled exception")


if __name__ == "__main__":
    sys.exit(main())
