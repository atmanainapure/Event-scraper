"""Decide whether a *scheduled* run should go ahead (stdlib only, runs before installs).

Prints "run" or "skip". Skips when today's email (New York date) was already sent,
or when it's still well before 8 AM New York time (the 7:50 run of the other
daylight-saving offset)."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

now = datetime.now(ZoneInfo("America/New_York"))
try:
    last = json.loads(Path("data/state.json").read_text()).get("last_sent")
except Exception:  # noqa: BLE001
    last = None
if last == now.date().isoformat():
    print("skip")  # already sent today
elif (now.hour, now.minute) < (7, 40):
    print("skip")  # too early; a later scheduled run will send
else:
    print("run")
