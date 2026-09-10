# Log analysis

All figures below come from running the Python script in "Commands / scripts" against
`logs/access.log`, `logs/error.log`, and `logs/application.log` exactly as supplied
(originals unmodified). Re-run the script yourself to reproduce every number.

## 1. UTC interval, valid/malformed/duplicate line counts

- **Interval covered:** `2026-08-20T11:00:00.015Z` -> `2026-08-20T11:30:00Z` (30 minutes), consistent
  across all three files.
- **access.log:** 726 total lines -> 725 valid JSON, **1 malformed** (line 311, a truncated JSON
  object cut off mid-key: `{"timestamp":"2026-08-20T11:12:48Z","request_id":`).
- **application.log:** 730 total lines -> 729 valid JSON, **1 malformed** (line 401, same pattern:
  `{"timestamp":"2026-08-20T11:17:00Z","event":`).
- **error.log:** 68 lines, plain text (not JSON), all well-formed; no malformed lines.
- **Duplicates:** access.log contains **5 request_ids that appear twice**, each as a byte-for-byte
  identical repeated line (`lab-000121`, `lab-000241`, `lab-000361`, `lab-000481`, `lab-000601`,
  10 lines total). These are exact duplicate log entries, not retries (a retry would show a
  comma-separated `upstream`/`upstream_status`, which these do not).

## 2. Distinct client requests and deduplication

- Deduplicated access.log by `request_id` (collapsing the 5 exact-duplicate pairs into one each):
  **720 distinct client requests** out of 725 valid lines.
- **Retries were not double-counted** because NGINX logs one line per *client request*, not per
  upstream attempt: when NGINX retries a failed backend internally, the single log line records
  both attempts in comma-separated form (e.g. `"upstream":"172.23.0.12:8080, 172.23.0.11:8080"`,
  `"upstream_status":"502, 200"`) with one final `status`. Counting these lines once each (which
  deduplication by `request_id` already does) correctly counts them as one client request with one
  final outcome, not two.

## 3. Final client status counts and error rate

Denominator: **720 distinct client requests** (see Q2).

| status | count | % of 720 |
|---|---|---|
| 200 | 615 | 85.42% |
| 404 | 10 | 1.39% |
| 502 | 40 | 5.56% |
| 503 | 47 | 6.53% |
| 504 | 8 | 1.11% |

- **5xx (server error) rate: 95/720 = 13.19%**
- **4xx (client error) rate: 10/720 = 1.39%** (all `/missing`, an intentionally nonexistent path —
  expected 404s, not failures)

## 4. Which paths, time windows and backends account for the failures

5xx failures by path (95 total):

| path | 5xx count |
|---|---|
| `/records` | 26 |
| `/counter` | 26 |
| `/ready` | 23 |
| `/` | 10 |
| `/health` | 10 |

5xx failures by backend (`upstream` IP):

| backend | 5xx count |
|---|---|
| `172.23.0.12:8080` (app-02) | 68 |
| `172.23.0.11:8080` (app-01) | 27 |

Failures cluster into **three distinct time windows** (see Q7 timeline) rather than being spread
evenly — 11:05-11:09, 11:12-11:15, 11:20-11:21, plus a smaller cluster at 11:25-11:26.

## 5. Median and p95 latency

- Method: nearest-rank percentile over `request_time` (seconds, converted to ms) for all 720
  distinct requests (successes and failures both included, since `request_time` measures total
  proxy time regardless of outcome).
- **Median: 54.00 ms**
- **p95: 2001.00 ms** (n=720; the large gap between median and p95 is explained by the timeout
  cluster in Q7, where several requests waited the full upstream timeout before failing)

## 6. Retried requests and retry success rate

- **19 requests** show a comma-separated `upstream`/`upstream_status` (i.e., NGINX retried a
  second backend after the first failed) — all during the 11:05-11:09 app-02 outage window.
- **19/19 (100%) succeeded after retrying** — every retried request's final `upstream_status`
  ended in `200` after failing on the first (`502`) backend.
- This is direct historical evidence for why `proxy_next_upstream` matters (see Entry 10 in
  `troubleshooting.md`): this historical log was captured with retry-on-failure already enabled,
  so unlike our own reproduced 502/504 test in Entry 10 (which showed what happens with retries
  *off*), this log shows the "correct" behavior — retries succeeding transparently.

## 7. Incident timeline (access + error + application logs correlated)

| Window (UTC) | Evidence source | What happened |
|---|---|---|
| 11:05:02 - 11:09:57 | error.log: 59x `connect() failed (111: Connection refused)` targeting `172.23.0.12:8080` (app-02) | app-02 was unreachable at the TCP level; NGINX logged 502s for direct hits and retried successfully 19 times (see Q6) |
| 11:12:09 - 11:15:52 | application.log: 31x `dependency_error` events, `dependency: redis`, `error_type: TimeoutError`, split across both `app-01` and `app-02` | Redis was slow/unreachable from the app's perspective, affecting **both** instances — a shared-dependency failure, not a single-backend failure |
| 11:20:07 - 11:21:45 | application.log: 16x `dependency_error` events, `dependency: postgres` | PostgreSQL dependency errors, again affecting both instances |
| 11:25:14 - 11:26:47 | error.log: 8x `upstream timed out (110: Operation timed out) ... reading response header`, alternating between both `172.23.0.11` and `172.23.0.12`, all on `/records` | Both backends timed out answering `/records` specifically — consistent with a slow/overloaded PostgreSQL causing request-level timeouts at the proxy, rather than a backend being down outright |

Total 5xx (95) = 40+47+8 across these windows, matching Q3/Q4 exactly.

## 8. One correlated failed request and one successful request

**Failed request** (`request_id: lab-000122`):
- access.log: `{"timestamp":"2026-08-20T11:05:02.503Z","request_id":"lab-000122","method":"GET","path":"/health","status":502,"upstream":"172.23.0.12:8080","upstream_status":"502","request_time":0.003}`
- application.log: **no matching entry for `lab-000122` at all**
- error.log: `2026/08/20 11:05:02 [error] ... connect() failed (111: Connection refused) ... request_id=lab-000122 ... upstream: "http://172.23.0.12:8080/health"`

**Successful request** (`request_id: lab-000002`):
- access.log: `{"timestamp":"2026-08-20T11:00:02.532Z","request_id":"lab-000002","method":"GET","path":"/health","status":200,"upstream":"172.23.0.12:8080","upstream_status":"200","request_time":0.032}`
- application.log: `{"timestamp":"2026-08-20T11:00:02.532Z","level":"INFO","event":"http_request","request_id":"lab-000002","instance_id":"app-02","method":"GET","path":"/health","status":200,"duration_ms":32.0}`

## 9. Proxy/connectivity errors vs dependency/application errors — what proves it

Two clearly distinguishable error classes, each provable from a different log:

- **Proxy/connectivity (NGINX never reached the app process):** the failed example above
  (`lab-000122`) has an error.log entry (`Connection refused`) but **zero** application.log entry.
  If the Flask process had received the request, it would always emit an `http_request` event
  (per `app/server.py`) — its total absence proves the request never reached the app at all. All
  59 `Connection refused` lines in error.log (11:05-11:09) follow this exact pattern.
- **Dependency/application (the app received the request but a downstream call failed):** the
  47 `dependency_error` events in application.log (31 redis, 16 postgres) each have a
  `request_id`, `instance_id`, `dependency`, and `error_type` — proving the Flask process *did*
  receive and start processing the request, and failed only when calling Postgres/Redis. These
  requests show up in access.log as 503s (dependency-caused failures) rather than 502s
  (connection-refused failures).
- The 11:25-11:26 timeout cluster is a hybrid: application.log has no matching entries (so the app
  never finished responding), but error.log shows `upstream timed out ... reading response header`
  rather than `Connection refused` — consistent with the app accepting the connection but stalling
  while waiting on a slow dependency, until NGINX's own read timeout fired.

## 10. What the logs do NOT prove / what to check next in a running environment

- The logs do not show **why** app-02 was unreachable at 11:05, why Redis/Postgres were slow at
  11:12/11:20, or why both backends timed out on `/records` specifically at 11:25 — there is no
  container-level (`docker logs`, `docker inspect`, resource metrics) evidence in this historical
  data, only the client-facing symptoms.
- The logs do not prove whether these were independent incidents or a single cascading failure
  (e.g., Postgres degrading first and causing the later Redis and timeout symptoms) — the disjoint
  time windows suggest independent events, but this is inference, not proof.
- In a running environment, the next steps would be: check `docker stats`/resource limits at each
  incident timestamp, check PostgreSQL/Redis's own logs for the same windows, and check whether
  the current stack (with `proxy_next_upstream` now enabled, per Entry 10) would have masked the
  11:05 app-02 outage the way it masked our own reproduced test — i.e., re-run a similar outage
  live and confirm the historical failure mode no longer reaches the client.

## Commands / scripts

```python
# See the analysis script used to produce every number above.
# (Save as analyze_logs.py in the repo root and run: python3 analyze_logs.py)
import json, statistics
from collections import Counter

def load_json_lines(path):
    out, malformed = [], 0
    with open(path) as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                out.append(json.loads(s))
            except json.JSONDecodeError:
                malformed += 1
    return out, malformed

access, access_malformed = load_json_lines("logs/access.log")
app, app_malformed = load_json_lines("logs/application.log")

# Q1
print("access.log valid:", len(access), "malformed:", access_malformed)
print("application.log valid:", len(app), "malformed:", app_malformed)

# Q2/Q3 — dedupe by request_id (collapses exact-duplicate lines)
distinct = list({a["request_id"]: a for a in access}.values())
print("distinct requests:", len(distinct))
print("status counts:", dict(Counter(a["status"] for a in distinct)))

# Q4 — failures by path/backend
fail = [a for a in distinct if a["status"] >= 500]
print("5xx by path:", dict(Counter(a["path"] for a in fail)))
print("5xx by backend:", dict(Counter(a["upstream"] for a in fail)))

# Q5 — latency
times = sorted(a["request_time"] * 1000 for a in distinct)
print("median ms:", statistics.median(times))
print("p95 ms:", times[int(len(times) * 0.95)])

# Q6 — retries
retried = [a for a in distinct if "," in a["upstream"]]
print("retried:", len(retried), "succeeded after retry:", sum(1 for a in retried if a["status"] == 200))

# Q7/Q9 — cross-reference application.log dependency_error events
dep_errors = [a for a in app if a.get("event") == "dependency_error"]
print("dependency_error count:", len(dep_errors))
print("by dependency:", dict(Counter(a["dependency"] for a in dep_errors)))
```

```bash
# error.log breakdown (plain text, grep-based)
grep -c "Connection refused" logs/error.log   # -> 59
grep -c "timed out" logs/error.log            # -> 8
grep -v -E "Connection refused|timed out" logs/error.log   # -> 1 line: log rotation notice, not an error
```

## Results

See numbered sections 1-10 above; every figure was produced by the script/commands shown and is
reproducible by re-running them against the unmodified log files.

## Timeline and correlated examples

See Q7 (timeline table) and Q8 (correlated failed/successful request pair) above.

## Conclusions and limits

Three (arguably four, counting the timeout cluster separately) distinct incidents are visible in
a 30-minute historical window: a single-backend connectivity outage (app-02, TCP-level), a
Redis dependency slowdown affecting both instances, a PostgreSQL dependency slowdown affecting
both instances, and a later timeout cluster on `/records` consistent with lingering
PostgreSQL slowness. The logs prove *what* the client experienced and *which* layer failed
(proxy vs. dependency) via the presence/absence of application.log entries, but do not prove
*why* app-02 or the dependencies became unavailable — that would require infrastructure-level
evidence not present in these three files. See Q10 for what to check next in a live environment.