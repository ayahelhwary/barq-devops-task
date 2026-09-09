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
- Related commit: [c8cc46f]
- Remaining uncertainty: None

## Entry 2 / 2026-09-09 / 20:25 UTC
- Symptom: `curl http://127.0.0.1:8080/` returns "Empty reply from server" even though all containers show as running/healthy
- Hypothesis: NGINX is not actually listening on the port Docker maps to the host
- Command or test: docker compose ps -a (showed 127.0.0.1:8080->81/tcp); grep "listen" nginx/nginx.conf (showed "listen 80;")
- Actual output: port mismatch confirmed — compose maps host 8080 to container port 81, but nginx listens on 80 inside the container
- Failed attempt and what changed your thinking: None — the mismatch was visible directly by comparing docker-compose.yml and nginx.conf
- Root cause: docker-compose.yml nginx port mapping (":81") does not match the "listen 80;" directive in nginx.conf
- Fix: changed nginx port mapping in docker-compose.yml from "127.0.0.1:${PUBLIC_PORT:-8080}:81" to "127.0.0.1:${PUBLIC_PORT:-8080}:80"
- Retest evidence: curl now returns "HTTP/1.1 502 Bad Gateway" instead of "Empty reply from server" — confirms NGINX is now reachable and listening correctly; the 502 itself points to a separate, still-unfixed upstream issue (see Entry 3)
- Related commit: [pending commit]
- Remaining uncertainty: None for this specific issue




Do not fabricate a failed attempt just to fill the template. Record actual attempts.
