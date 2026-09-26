import sys
import time

import afk_hook


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "X"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    afk_hook.install(project="Smoke" + name)
    afk_hook.mark_stage("start-" + name)
    print("smoke {0} start".format(name), flush=True)
    time.sleep(seconds)
    afk_hook.mark_done("smoke {0} done".format(name))
    print("smoke {0} done".format(name), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
