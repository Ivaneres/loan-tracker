"""Gunicorn hooks. One worker owns APScheduler so jobs cannot fire twice."""

import fcntl
import os

_scheduler_lock = None


def post_fork(server, worker):
    global _scheduler_lock
    os.makedirs("data", exist_ok=True)
    handle = open(os.path.join("data", "scheduler.lock"), "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return
    _scheduler_lock = handle
    from app import bootstrap_schedules

    bootstrap_schedules()
