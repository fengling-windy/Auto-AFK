import sys
import time
from pathlib import Path

log_path = Path(sys.argv[1])
log_path.parent.mkdir(parents=True, exist_ok=True)
with open(log_path, "w", encoding="utf-8") as f:
    f.write("[17:00:00.000] [INF] [test] 一条龙任务开始\n")
    f.flush()
time.sleep(3)
with open(log_path, "a", encoding="utf-8") as f:
    f.write("[17:00:03.000] [INF] [test] 一条龙和配置组任务结束\n")
    f.flush()
time.sleep(300)
sys.exit(0)
