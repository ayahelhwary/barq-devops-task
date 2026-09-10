#!/usr/bin/env python3
"""
Failure / recovery test for the BARQ DevOps assessment.

Stops one backend (app-01), proves NGINX keeps serving traffic through the
surviving backend (app-02), measures errors seen during the outage, restores
app-01, and proves it is serving requests again afterwards.

Uses only the Python standard library plus `docker` on PATH. Bounded waits
everywhere; exits non-zero on any failed check.

Usage:
    python3 failure_test.py [--url http://127.0.0.1:8080] [--target app-01]
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
    def __init__(self):
        self.checks = []

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


def http_get(url, timeout=3):
    """Returns (status_code_or_None, parsed_json_or_None, raw_or_error_str)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        return None, None, str(exc)
    try:
        return status, json.loads(raw), raw
    except json.JSONDecodeError:
        return status, None, raw


def docker(*args, timeout=15):
    proc = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def wait_until(predicate, timeout, interval=0.5):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return True, last
        time.sleep(interval)
    return False, last


def burst_instance_ids(base, count, timeout=3):
    """Fires `count` requests to /instance and returns (statuses, instance_ids_seen)."""
    statuses = []
    seen = set()
    for _ in range(count):
        status, body, _ = http_get(base + "/instance", timeout=timeout)
        statuses.append(status)
        if status == 200 and isinstance(body, dict):
            seen.add(body.get("instance_id"))
    return statuses, seen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Public NGINX base URL")
    parser.add_argument("--target", default="app-01", help="Backend container to stop")
    parser.add_argument("--survivor", default="app-02", help="Backend expected to keep serving")
    parser.add_argument("--recovery-timeout", type=int, default=30,
                         help="Seconds to wait for the stopped backend to become healthy again")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    result = Result()

    # 0) Baseline - both instances must be reachable before we start the test.
    statuses, seen = burst_instance_ids(base, 10)
    result.record(
        "baseline_both_instances_serving",
        {args.target, args.survivor} <= seen,
        f"observed instance_ids over 10 requests: {sorted(seen)}",
    )
    if not result.all_passed():
        result.summary()
        print("Baseline failed - refusing to run the failure test against a broken environment.")
        sys.exit(1)

    # 1) Stop the target backend.
    rc, out, err = docker("stop", args.target)
    result.record("stop_target_backend", rc == 0, f"docker stop {args.target} -> rc={rc} {err[:120]}")

    # 2) During the outage: fire a burst of requests, prove the survivor keeps
    #    responding and record how many requests failed/errored while NGINX
    #    routed around the stopped backend.
    time.sleep(1)  # give NGINX/healthcheck a moment to notice
    outage_statuses, outage_seen = burst_instance_ids(base, 20)
    outage_errors = sum(1 for s in outage_statuses if s not in (200,))
    result.record(
        "survivor_keeps_serving_during_outage",
        args.survivor in outage_seen and args.target not in outage_seen,
        f"instance_ids seen during outage: {sorted(outage_seen)} "
        f"(errors/non-200 responses: {outage_errors}/{len(outage_statuses)})",
    )

    status, body, raw = http_get(base + "/")
    result.record(
        "public_endpoint_still_reachable_during_outage",
        status == 200,
        f"GET {base}/ during outage -> status={status}",
    )

    # 3) Restore the target backend.
    rc, out, err = docker("start", args.target)
    result.record("restart_target_backend", rc == 0, f"docker start {args.target} -> rc={rc} {err[:120]}")

    def target_healthy():
        rc, out, err = docker(
            "inspect", args.target, "--format", "{{.State.Health.Status}}"
        )
        return rc == 0 and out.strip() == "healthy"

    ok, _ = wait_until(target_healthy, timeout=args.recovery_timeout, interval=1)
    result.record(
        "target_backend_healthy_again", ok,
        f"docker inspect {args.target} health status == healthy within {args.recovery_timeout}s"
        if ok else f"{args.target} did not become healthy within {args.recovery_timeout}s",
    )

    # 4) Prove the recovered backend is actually serving requests again
    #    (not just "healthy" - NGINX must be routing to it too).
    def recovered_serving():
        _, seen = burst_instance_ids(base, 10)
        return args.target in seen

    ok, _ = wait_until(recovered_serving, timeout=args.recovery_timeout, interval=1)
    final_statuses, final_seen = burst_instance_ids(base, 10)
    result.record(
        "recovered_backend_serving_traffic", ok,
        f"instance_ids seen after recovery: {sorted(final_seen)}",
    )

    result.summary()
    sys.exit(0 if result.all_passed() else 1)


if __name__ == "__main__":
    main()
