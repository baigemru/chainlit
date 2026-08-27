# Baseline — FastAPI/Starlette stack, before the Litestar rebuild

Captured 2026-08-27 on the development machine (Darwin 25.5.0, Python 3.14.3,
uvicorn, `chainlit run chainlit/sample/hello.py -h --ci`). Re-run both scripts
against the rebuilt stack on the same machine to compare; absolute numbers are
machine-specific, ratios are not.

## Serialization — `bench_serde.py`

Today's step payload, encoded per streamed message and per element.

| operation                      |        ops/sec |    µs/op |
| ------------------------------ | -------------: | -------: |
| stdlib `json.dumps(dict)`      |        475,153 |     2.10 |
| pydantic `model_dump_json`     |        552,905 |     1.81 |
| **msgspec encode**             |  **3,394,889** | **0.29** |
| stdlib `json.loads`            |        382,681 |     2.61 |
| pydantic `model_validate_json` |        311,392 |     3.21 |
| **msgspec decode**             |    **599,659** | **1.67** |
| pydantic construct             |        857,625 |     1.17 |
| **msgspec construct**          | **12,704,801** | **0.08** |

encode **6.1x**, decode **1.9x**, construct **14.8x**.

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
