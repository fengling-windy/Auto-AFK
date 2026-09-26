import subprocess
import sys
import time

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
print("child pid {0}".format(child.pid), flush=True)
time.sleep(1.0)
sys.exit(0)
