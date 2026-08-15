"""Continuous, realistic traffic against checkout-service (design doc §1.2).

Runs a fixed number of concurrent workers, each looping
request -> sleep -> request, logging every outcome as structured JSON so a
human (or, eventually, Loki) can see the real error rate rise when
payment-service's pool starts exhausting.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import sys
import time

import httpx
from aic_common.logging import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_CONCURRENCY = 10
DEFAULT_REQUEST_INTERVAL_SECONDS = 0.2
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0


async def _worker(
    worker_id: int,
    client: httpx.AsyncClient,
    order_ids: itertools.count[int],
    interval_seconds: float,
    stop_at: float | None,
) -> None:
    while stop_at is None or time.monotonic() < stop_at:
        order_id = f"w{worker_id}-{next(order_ids)}"
        start = time.perf_counter()
        try:
            response = await client.post(
                "/checkout", json={"order_id": order_id, "amount_cents": 500}
            )
            latency_seconds = time.perf_counter() - start
            logger.info(
                "load_generator.request_completed",
                order_id=order_id,
                status_code=response.status_code,
                latency_seconds=latency_seconds,
            )
        except httpx.RequestError as exc:
            latency_seconds = time.perf_counter() - start
            logger.warning(
                "load_generator.request_failed",
                order_id=order_id,
                error=str(exc),
                latency_seconds=latency_seconds,
            )
        await asyncio.sleep(interval_seconds)


async def run(
    *,
    base_url: str,
    concurrency: int,
    interval_seconds: float,
    duration_seconds: float | None,
    request_timeout_seconds: float,
) -> None:
    stop_at = None if duration_seconds is None else time.monotonic() + duration_seconds
    order_ids = itertools.count()
    async with httpx.AsyncClient(base_url=base_url, timeout=request_timeout_seconds) as client:
        logger.info(
            "load_generator.started",
            base_url=base_url,
            concurrency=concurrency,
            interval_seconds=interval_seconds,
        )
        workers = [
            _worker(worker_id, client, order_ids, interval_seconds, stop_at)
            for worker_id in range(concurrency)
        ]
        await asyncio.gather(*workers)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send continuous traffic to checkout-service.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=DEFAULT_REQUEST_INTERVAL_SECONDS,
        help="Per-worker pause between requests.",
    )
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=None,
        help="Stop after this many seconds; omit to run until interrupted.",
    )
    parser.add_argument(
        "--request-timeout-seconds", type=float, default=DEFAULT_REQUEST_TIMEOUT_SECONDS
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        asyncio.run(
            run(
                base_url=args.base_url,
                concurrency=args.concurrency,
                interval_seconds=args.interval_seconds,
                duration_seconds=args.duration_seconds,
                request_timeout_seconds=args.request_timeout_seconds,
            )
        )
    except KeyboardInterrupt:
        logger.info("load_generator.stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
