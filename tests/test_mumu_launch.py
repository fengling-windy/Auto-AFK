import json
import os
import subprocess
import time

CMD = "E:/MuMuPlayer/nx_device/15.0/shell/MuMuNxDevice.exe -v 0"
MGR = r"E:\MuMuPlayer\nx_main\MuMuManager.exe"


def main():
    print("cwd:", os.getcwd())
    proc = subprocess.Popen(
        CMD,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        encoding="utf-8",
        shell=False,
    )
    print("launcher pid:", proc.pid)
    time.sleep(12)
    exited = proc.poll() is not None
    print("launcher exited:", exited)
    if exited:
        out, err = proc.communicate()
        print("stdout:", (out or "")[:1500])
        print("stderr:", (err or "")[:1500])
    info = subprocess.run([MGR, "info", "-v", "0"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    try:
        data = json.loads(info.stdout)
        print("is_android_started:", data.get("is_android_started"), "is_process_started:", data.get("is_process_started"))
    except Exception:
        print("info output:", info.stdout[:400], info.stderr[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
