"""A small job runner for Docker deployments, where host cron is not used.

Runs, in the application's time zone:
  deliver_emails   every 5 minutes
  send_reminders   Monday to Friday at 07:30
  apply_retention  every day at 02:15

On a normal Linux server use deploy/crontab instead.
"""

import os
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("DJANGO_TIME_ZONE", "Africa/Harare"))
JOBS = [
    ("deliver_emails", lambda now: now.minute % 5 == 0),
    ("send_reminders", lambda now: now.weekday() < 5 and (now.hour, now.minute) == (7, 30)),
    ("apply_retention", lambda now: (now.hour, now.minute) == (2, 15)),
]


def run(command):
    started = datetime.now(TZ).isoformat(timespec="seconds")
    result = subprocess.run([sys.executable, "manage.py", command], capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip().replace("\n", " | ")
    print(f"{started} {command} exit={result.returncode} {output}", flush=True)


def main():
    last_minute = None
    while True:
        now = datetime.now(TZ).replace(second=0, microsecond=0)
        if now != last_minute:
            last_minute = now
            for command, due in JOBS:
                if due(now):
                    run(command)
        time.sleep(15)


if __name__ == "__main__":
    main()
