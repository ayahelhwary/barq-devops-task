"""
Log analysis for logs/access.log, logs/error.log, logs/application.log.
Run from the repository root: python3 analyze_logs.py
Every number printed here is referenced directly in log_analysis.md.
"""
import json
import statistics
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


def main():
    access, access_malformed = load_json_lines("logs/access.log")
    app, app_malformed = load_json_lines("logs/application.log")
    with open("logs/error.log") as f:
        error_lines = [l.strip() for l in f if l.strip()]

    # --- Q1: valid/malformed counts ---
    print("=== Q1: valid/malformed line counts ===")
    print(f"access.log: valid={len(access)} malformed={access_malformed}")
    print(f"application.log: valid={len(app)} malformed={app_malformed}")
    print(f"error.log: lines={len(error_lines)}")

    # --- Q2/Q3: dedupe by request_id, status counts ---
    distinct = list({a["request_id"]: a for a in access}.values())
    print(f"\n=== Q2: distinct client requests (deduped) ===")
    print(f"valid lines={len(access)} -> distinct requests={len(distinct)}")

    print(f"\n=== Q3: final status counts (denominator={len(distinct)}) ===")
    status_counts = Counter(a["status"] for a in distinct)
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count} ({count / len(distinct) * 100:.2f}%)")
    errors_5xx = sum(v for k, v in status_counts.items() if k >= 500)
    print(f"  5xx total: {errors_5xx} ({errors_5xx / len(distinct) * 100:.2f}%)")

    # --- Q4: failures by path / backend ---
    fail = [a for a in distinct if a["status"] >= 500]
    print("\n=== Q4: 5xx failures by path ===")
    print(dict(Counter(a["path"] for a in fail)))
    print("5xx failures by backend:")
    print(dict(Counter(a["upstream"] for a in fail)))

    # --- Q5: latency ---
    times_ms = sorted(a["request_time"] * 1000 for a in distinct)
    median = statistics.median(times_ms)
    p95 = times_ms[int(len(times_ms) * 0.95)]
    print(f"\n=== Q5: latency (n={len(times_ms)}) ===")
    print(f"median={median:.2f}ms p95(nearest-rank)={p95:.2f}ms")

    # --- Q6: retries ---
    retried = [a for a in distinct if "," in a["upstream"]]
    succeeded_after_retry = sum(1 for a in retried if a["status"] == 200)
    print(f"\n=== Q6: retries ===")
    print(f"retried requests={len(retried)} succeeded_after_retry={succeeded_after_retry}")

    # --- Q7/Q9: dependency errors from application.log, error.log breakdown ---
    dep_errors = [a for a in app if a.get("event") == "dependency_error"]
    print(f"\n=== Q7/Q9: application.log dependency_error events ===")
    print(f"total={len(dep_errors)} by_dependency={dict(Counter(a['dependency'] for a in dep_errors))}")

    conn_refused = [l for l in error_lines if "Connection refused" in l]
    timeouts = [l for l in error_lines if "timed out" in l]
    print(f"\nerror.log: connection_refused={len(conn_refused)} timeouts={len(timeouts)} "
          f"other={len(error_lines) - len(conn_refused) - len(timeouts)}")

    # --- Q8: one failed + one successful correlated example ---
    failed_example = next(a for a in distinct if a["status"] == 502)
    success_example = next(a for a in distinct if a["status"] == 200 and "," not in a["upstream"])
    app_by_id = {a["request_id"]: a for a in app}
    print(f"\n=== Q8: correlated examples ===")
    print("FAILED:", failed_example)
    print("  application.log match:", app_by_id.get(failed_example["request_id"], "NONE"))
    print("SUCCESS:", success_example)
    print("  application.log match:", app_by_id.get(success_example["request_id"], "NONE"))


if __name__ == "__main__":
    main()