import sys
import time


def main():
    # simulates a launcher: exits quickly (non-zero) while the real game task
    # (tracked via window keywords in the smoke test) keeps running
    print("fake launcher starting", flush=True)
    time.sleep(2)
    print("fake launcher exiting with code 3", flush=True)
    return 3


if __name__ == "__main__":
    sys.exit(main())
