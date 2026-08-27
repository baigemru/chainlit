"""HTTP throughput baseline against a running chainlit server.

Usage: uv run python bench_http.py [base_url]
Reports p50/p95/p99 and requests/sec per endpoint at a fixed concurrency.
"""

import asyncio
import sys
import time
from statistics import quantiles

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
CONCURRENCY = 32
REQUESTS = 2000

ENDPOINTS = [
    ("GET /health", "GET", "/health", None),
    ("GET /project/translations", "GET", "/project/translations?language=en-US", None),
    ("GET /auth/config", "GET", "/auth/config", None),
    ("GET / (SPA shell)", "GET", "/", None),
]


async def worker(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    body,
    queue: asyncio.Queue,
    latencies: list[float],
    errors: list[int],
) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        t0 = time.perf_counter()
        try:
            resp = await client.request(method, path, json=body)
            if resp.status_code >= 500:
                errors.append(resp.status_code)
        except Exception:
            errors.append(-1)
        latencies.append((time.perf_counter() - t0) * 1000)


async def run_one(label: str, method: str, path: str, body) -> None:
    queue: asyncio.Queue = asyncio.Queue()
    for _ in range(REQUESTS):
        queue.put_nowait(1)

    latencies: list[float] = []
    errors: list[int] = []

    limits = httpx.Limits(
        max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY
    )
    async with httpx.AsyncClient(base_url=BASE, limits=limits, timeout=30.0) as client:
        await client.request(method, path, json=body)  # warm
        t0 = time.perf_counter()
        await asyncio.gather(
            *[
                worker(client, method, path, body, queue, latencies, errors)
                for _ in range(CONCURRENCY)
            ]
        )
        elapsed = time.perf_counter() - t0

    latencies.sort()
    q = quantiles(latencies, n=100)
    print(
        f"{label:28} {REQUESTS / elapsed:9,.0f} rps   "
        f"p50 {q[49]:6.2f}ms  p95 {q[94]:6.2f}ms  p99 {q[98]:6.2f}ms  "
        f"err {len(errors)}"
    )


async def main() -> None:
    print(f"target={BASE}  concurrency={CONCURRENCY}  requests={REQUESTS}\n")
    print(f"{'endpoint':28} {'throughput':>9}       latency")
    print("-" * 92)
    for label, method, path, body in ENDPOINTS:
        await run_one(label, method, path, body)


asyncio.run(main())
