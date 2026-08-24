"""Process entry point for Rocky's durable scheduled-work runner."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket

from app.db.session import get_engine, get_sessionmaker
from app.reminders.handler import ReminderDueHandler
from app.reminders.service import RemindersService
from app.notifications.service import NotificationsService
from app.scheduling.runner import ScheduledJobRunner
from app.scheduling.service import SchedulingService

logger = logging.getLogger(__name__)


async def run_once(worker_id: str, *, limit: int = 20) -> int:
    async with get_sessionmaker()() as session:
        scheduling = SchedulingService(session)
        reminders = RemindersService(session)
        notifications = NotificationsService(session)
        runner = ScheduledJobRunner(
            scheduling,
            {"reminder.due": ReminderDueHandler(reminders, notifications)},
        )
        jobs = await runner.run_once(worker_id, limit=limit)
        return len(jobs)


async def run_forever(
    worker_id: str, *, poll_seconds: float = 2.0, limit: int = 20
) -> None:
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive.")
    try:
        while True:
            processed = await run_once(worker_id, limit=limit)
            if processed == 0:
                await asyncio.sleep(poll_seconds)
    finally:
        await get_engine().dispose()


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"[:128]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Rocky scheduled work.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id", default=_default_worker_id())
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.once:
        processed = asyncio.run(run_once(args.worker_id, limit=args.limit))
        logger.info("Processed %s scheduled job(s)", processed)
    else:
        asyncio.run(
            run_forever(
                args.worker_id,
                poll_seconds=args.poll_seconds,
                limit=args.limit,
            )
        )


if __name__ == "__main__":  # pragma: no cover - process entry point
    main()
