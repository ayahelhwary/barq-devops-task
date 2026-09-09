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
- Related commit: [5d71ae8]
- Remaining uncertainty: None for this specific issue

## Entry 3 / 2026-09-09 / 20:31 UTC
- Symptom: curl to http://127.0.0.1:8080/ returns "HTTP/1.1 502 Bad Gateway"
- Hypothesis: NGINX upstream definition points to the wrong port for app-01
- Command or test: docker compose logs nginx
- Actual output: "connect() failed (111: Connection refused) ... upstream: http://172.21.0.3:8081/"
- Failed attempt and what changed your thinking: None — nginx error log directly named the wrong port
- Root cause: nginx/nginx.conf upstream block listed "server app-01:8081" instead of 8080; the Flask app listens on 8080 (per Dockerfile EXPOSE and APP_PORT)
- Fix: changed "server app-01:8081" to "server app-01:8080" in nginx/nginx.conf
- Retest evidence: after this fix alone, curl still returned 502 but the nginx error log target changed from "172.21.0.3:8081" to "172.21.0.3:8080" — confirming this specific fix worked and exposed a separate remaining issue (see Entry 4)
- Related commit: [3ff8f21]
- Remaining uncertainty: None for this specific issue

## Entry 4 / 2026-09-09 / 20:42 UTC
- Symptom: even with the correct upstream port (8080), nginx still returns 502 with "Connection refused" to http://172.21.0.3:8080/
- Hypothesis: the Flask app inside app-01 is not actually listening on an address reachable from other containers
- Command or test: docker exec nginx wget -qO- --timeout=2 http://app-01:8080/health
- Actual output: "wget: can't connect to remote host (172.21.0.3): Connection refused"
- Failed attempt and what changed your thinking: None — the direct wget test from inside the nginx container isolated the problem to the app process binding, not networking or DNS
- Root cause: docker-compose.yml set APP_HOST: "127.0.0.1" for both app services, so Flask only listened on the container's own loopback interface and was unreachable from other containers on the same network
- Fix: changed APP_HOST from "127.0.0.1" to "0.0.0.0" in docker-compose.yml
- Retest evidence: `docker exec nginx wget ... /health` now returns `{"instance_id":"app-01","service":"barq-api","status":"alive","version":"2.0.0"}`; `curl http://127.0.0.1:8080/` now returns HTTP/1.1 200 OK with a valid JSON body
- Related commit: [dd4a554]
- Remaining uncertainty: None for this specific issue

## Entry 5 / 2026-09-09 / 20:50 UTC
- Symptom: /instance endpoint always returned "app-01" regardless of which backend actually served the request (6/6 requests showed app-01)
- Hypothesis: both app-01 and app-02 services are configured with the same INSTANCE_ID value
- Command or test: grep -A5 "app-02:" docker-compose.yml
- Actual output: app-02 service block had INSTANCE_ID: "app-01" (same as app-01's own value)
- Failed attempt and what changed your thinking: None — the duplicate value was visible directly in docker-compose.yml
- Root cause: docker-compose.yml set INSTANCE_ID: "app-01" under the app-02 service instead of "app-02", so both instances reported the same identity even though NGINX was actually load-balancing between two separate containers
- Fix: changed INSTANCE_ID under the app-02 service block from "app-01" to "app-02"
- Retest evidence: 6 consecutive requests to /instance now alternate correctly: app-02, app-01, app-02, app-01, app-02, app-01
- Related commit: [fdfcc2c]
- Remaining uncertainty: None for this specific issue




Do not fabricate a failed attempt just to fill the template. Record actual attempts.
