#!/usr/bin/env python3
"""
Environment validation for the BARQ DevOps assessment.

Checks (per assessment/TASK.md, Part 3):
  1. Public access to NGINX on the host.
  2. All required endpoints respond with the expected status/shape.
  3. Both backend instances (app-01, app-02) actually serve traffic through NGINX.
  4. PostgreSQL and Redis readiness (via /ready).
  5. Network isolation: postgres/redis must NOT publish host ports.
  6. Unknown routes return 404 (per the API contract).

Uses only the Python standard library (urllib, subprocess) so it runs with no
extra pip installs. Bounded waits: nothing loops forever; every wait has a
timeout and the script exits non-zero on any failure.

Usage:
    python3 validate.py [--url http://127.0.0.1:8080] [--project barq-assessment]
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

PASS = "PASS"
FAIL = "FAIL"


class Result:
    """Collects check outcomes so we can print a summary and pick an exit code."""

    def __init__(self):
        self.checks = []  # list of (name, status, detail)

    def record(self, name, ok, detail):
        status = PASS if ok else FAIL
        self.checks.append((name, status, detail))
        print(f"[{status}] {name} — {detail}")
        return ok

    def all_passed(self):
        return all(status == PASS for _, status, _ in self.checks)

    def summary(self):
        total = len(self.checks)
        passed = sum(1 for _, status, _ in self.checks if status == PASS)
        print(f"\n{passed}/{total} checks passed.")


def http_get(url, timeout=5):
    """Returns (status_code, parsed_json_or_None, raw_text). Never raises on HTTP errors."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        return None, None, str(exc)
    try:
        return status, json.loads(raw), raw
    except json.JSONDecodeError:
        return status, None, raw


def http_post(url, payload, timeout=5):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        return None, None, str(exc)
    try:
        return status, json.loads(raw), raw
    except json.JSONDecodeError:
        return status, None, raw


def wait_until(predicate, timeout, interval=1):
    """Bounded wait: polls predicate() until it returns truthy or timeout elapses."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return True, last
        time.sleep(interval)
    return False, last


def docker_port(container):
    """Returns the raw output of `docker port <container>` (empty string = no published ports)."""
    try:
        proc = subprocess.run(
            ["docker", "port", container], capture_output=True, text=True, timeout=10
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        return None, str(exc)
    if proc.returncode not in (0, 1):
        # docker port returns 1 with no output when nothing is published; anything else is unexpected
        return None, proc.stderr.strip()
    return proc.stdout.strip(), None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Public NGINX base URL")
    parser.add_argument("--project", default="barq-assessment", help="Docker Compose project name")
    parser.add_argument("--startup-timeout", type=int, default=30,
                         help="Seconds to wait for /ready before failing")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    result = Result()

    # 1) Public access — bounded wait in case the stack just started.
    ok, _ = wait_until(lambda: http_get(base + "/")[0] == 200, timeout=args.startup_timeout)
    result.record(
        "public_access", ok,
        f"GET {base}/ reachable" if ok else f"GET {base}/ did not return 200 within {args.startup_timeout}s",
    )
    if not ok:
        # No point checking anything downstream of NGINX if it never came up.
        result.summary()
        sys.exit(1)

    # 2) /health — process liveness, no dependency check expected.
    status, body, raw = http_get(base + "/health")
    result.record("health_endpoint", status == 200, f"status={status} body={raw[:120]}")

    # 3) /ready — bounded wait for PostgreSQL + Redis readiness.
    def ready_check():
        s, b, _ = http_get(base + "/ready")
        return s == 200 and isinstance(b, dict) and b.get("status") == "ready"

    ok, _ = wait_until(ready_check, timeout=args.startup_timeout)
    status, body, raw = http_get(base + "/ready")
    result.record(
        "ready_endpoint", ok,
        f"status={status} dependencies={body.get('dependencies') if isinstance(body, dict) else raw[:120]}",
    )

    # 4) /instance and both backends — fire enough requests to see both instance_ids.
    seen_instances = set()
    last_status = None
    for _ in range(10):
        last_status, body, raw = http_get(base + "/instance")
        if last_status == 200 and isinstance(body, dict):
            seen_instances.add(body.get("instance_id"))
    result.record("instance_endpoint", last_status == 200, f"last status={last_status}")
    result.record(
        "both_backends_serving",
        {"app-01", "app-02"} <= seen_instances,
        f"observed instance_ids over 10 requests: {sorted(seen_instances)}",
    )

    # 5) /records — GET, valid POST, invalid POST (contract: 400 for bad titles).
    status, body, raw = http_get(base + "/records")
    result.record(
        "records_get",
        status == 200 and isinstance(body, dict) and "records" in body,
        f"status={status} record_count={len(body.get('records', [])) if isinstance(body, dict) else 'n/a'}",
    )

    status, body, raw = http_post(base + "/records", {"title": "validate.py smoke test"})
    result.record(
        "records_post_valid",
        status == 201 and isinstance(body, dict) and "record" in body,
        f"status={status} body={raw[:150]}",
    )

    status, body, raw = http_post(base + "/records", {"title": ""})
    result.record(
        "records_post_invalid_title_rejected", status == 400,
        f"status={status} (expected 400 for empty title)",
    )

    # 6) /counter — Redis-backed, should increment across calls.
    status1, body1, _ = http_get(base + "/counter")
    status2, body2, _ = http_get(base + "/counter")
    counters_increment = (
        status1 == 200 and status2 == 200
        and isinstance(body1, dict) and isinstance(body2, dict)
        and body2.get("counter", -1) > body1.get("counter", -1)
    )
    result.record(
        "counter_endpoint", counters_increment,
        f"first={body1.get('counter') if isinstance(body1, dict) else status1} "
        f"second={body2.get('counter') if isinstance(body2, dict) else status2}",
    )

    # 7) Unknown route -> 404, per the API contract.
    status, body, raw = http_get(base + "/this-route-does-not-exist")
    result.record("unknown_route_404", status == 404, f"status={status}")

    # 8) Network isolation — postgres/redis must not publish host ports.
    for container in ("postgres", "redis"):
        output, err = docker_port(container)
        if err is not None:
            result.record(f"no_host_port_{container}", False, f"could not inspect container: {err}")
            continue
        result.record(
            f"no_host_port_{container}", output == "",
            "no published ports (expected)" if output == "" else f"UNEXPECTED published ports: {output}",
        )

    result.summary()
    sys.exit(0 if result.all_passed() else 1)


if __name__ == "__main__":
    main()
