"""Gate scheduled checks using configurable local hours; manual runs always pass."""

import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo


def minutes(value):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("Monitoring times must use HH:MM (00:00 to 23:59)")
    hour, minute = map(int, value.split(":"))
    if minute % 15:
        raise ValueError("Monitoring times must align to a 15-minute boundary")
    return hour * 60 + minute


def should_run(now, start="08:00", end="20:00", interval="15"):
    start_minute, end_minute = minutes(start), minutes(end)
    interval = int(interval)
    if interval < 15 or interval > 1440 or interval % 15:
        raise ValueError("MONITOR_INTERVAL_MINUTES must be a multiple of 15 from 15 to 1440")
    # Use the current quarter-hour slot so small runner delays do not skip checks.
    slot = (now.hour * 60 + now.minute) // 15 * 15
    elapsed = (slot - start_minute) % 1440
    window = (end_minute - start_minute) % 1440
    return now.weekday() != 6 and elapsed <= window and elapsed % interval == 0


def main():
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        allowed = True
        print("Manual run: bypassing monitoring hours")
    else:
        now = datetime.now(ZoneInfo(os.environ.get("MONITOR_TIMEZONE", "Europe/Luxembourg")))
        allowed = should_run(
            now, os.environ.get("MONITOR_START", "08:00"),
            os.environ.get("MONITOR_END", "20:00"),
            os.environ.get("MONITOR_INTERVAL_MINUTES", "15"),
        )
        print(f"Local time: {now.isoformat()}; run monitor: {allowed}")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"run_monitor={str(allowed).lower()}\n")


if __name__ == "__main__":
    main()
