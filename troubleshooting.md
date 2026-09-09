# Troubleshooting journal

Keep chronological entries. Copy this block for each meaningful investigation.

## Entry 1 / 2026-09-09 / 19:53 UTC
- Symptom: app-01 and app-02 containers stayed "unhealthy" indefinitely after startup
- Hypothesis: the Docker healthcheck is targeting a route the app doesn't implement
- Command or test: docker compose -p barq-assessment logs app-01
- Actual output: repeated log lines "GET /healthz HTTP/1.1" 404 -
- Failed attempt and what changed your thinking: None — logs directly showed the wrong path on the first check
- Root cause: docker-compose.yml healthcheck test hits /healthz, but app/server.py only defines a /health route (no "z")
- Fix: changed the healthcheck test in docker-compose.yml from /healthz to /health
- Retest evidence: `docker compose ps -a` shows app-01/app-02 as (healthy); `docker inspect app-01` shows Health.Status="healthy" with 5 consecutive checks, ExitCode 0
- Related commit: [هنحطه بعد الـcommit]
- Remaining uncertainty: None

Do not fabricate a failed attempt just to fill the template. Record actual attempts.
