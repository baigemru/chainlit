# Baseline — FastAPI/Starlette stack, before the Litestar rebuild

Captured 2026-08-27 on the development machine (Darwin 25.5.0, Python 3.14.3,
uvicorn, `chainlit run chainlit/sample/hello.py -h --ci`). Re-run both scripts
against the rebuilt stack on the same machine to compare; absolute numbers are
machine-specific, ratios are not.

## Serialization — `bench_serde.py`

Today's step payload, encoded per streamed message and per element.

| operation                      |       ops/sec |    µs/op |
| ------------------------------ | ------------: | -------: |
| stdlib `json.dumps(dict)`      |       475,153 |     2.10 |
| pydantic `model_dump_json`     |       552,905 |     1.81 |
| **msgspec encode**             | **3,566,970** | **0.28** |
| stdlib `json.loads`            |       389,818 |     2.57 |
| pydantic `model_validate_json` |       311,607 |     3.21 |
| **msgspec decode**             |   **614,060** | **1.63** |
| pydantic construct             |       871,661 |     1.15 |
| **msgspec construct**          | **3,397,701** | **0.29** |

encode **6.7x**, decode **2.0x**, construct **3.9x**.

An earlier revision of this file claimed 14.8x on construct. That number was
wrong: it built a 20-field pydantic model against a 3-field msgspec struct.
Both rows now build all 20 fields. The encode and decode rows were always a
like-for-like comparison of the same object and the same payload.

Wire size for the same step: **1620 -> 731 bytes**. The halving is `omit_defaults`:
today every `null` and `false` of ~20 optional step fields is serialized and sent.

## HTTP — `bench_http.py`, concurrency 32, 2000 requests

| endpoint                    | throughput |     p50 |      p95 |      p99 |
| --------------------------- | ---------: | ------: | -------: | -------: |
| `GET /health`               |    844 rps | 21.95ms | 115.89ms | 192.53ms |
| `GET /project/translations` |    868 rps | 20.74ms | 119.03ms | 188.56ms |
| `GET /auth/config`          |  1,128 rps | 17.29ms |  82.70ms | 137.82ms |
| `GET /` (SPA shell)         |    957 rps | 18.92ms | 106.96ms | 163.74ms |

Two things worth reading off this table:

- `/health` is the cheapest handler in the codebase and the _slowest_ of the
  four. It is a sync `def` (`server.py`), and FastAPI dispatches sync handlers
  to a threadpool. Litestar makes that an explicit `sync_to_thread` choice.
- `GET /` re-renders the SPA shell on every request: a file read, a regex
  substitution and several string replacements over the whole document. The
  rebuild renders it once at app init.
