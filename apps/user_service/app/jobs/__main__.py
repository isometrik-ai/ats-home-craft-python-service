"""CLI entrypoint for notice board background jobs."""

from __future__ import annotations

import argparse
import asyncio

from apps.user_service.app.jobs.publish_scheduled_notices import (
    expire_notice_pins,
    publish_scheduled_notices,
)
from libs.shared_db.drivers.asyncpg_client import get_pool


async def _run_publish() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            published = await publish_scheduled_notices(conn)
            print(f"Published {len(published)} scheduled notice(s)")


async def _run_expire_pins() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            count = await expire_notice_pins(conn)
            print(f"Expired {count} banner pin(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Notice board background jobs")
    parser.add_argument(
        "job",
        choices=["publish-scheduled", "expire-pins"],
        help="Job to run",
    )
    args = parser.parse_args()
    if args.job == "publish-scheduled":
        asyncio.run(_run_publish())
    else:
        asyncio.run(_run_expire_pins())


if __name__ == "__main__":
    main()
