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

## Entry 6 / 2026-09-09 / 20:53 UTC
- Symptom: /ready endpoint returns {"status":"not_ready","dependencies":{"postgres":"unavailable","redis":"unavailable"}}
- Hypothesis: DATABASE_URL and REDIS_URL in config/app.env point to wrong ports and/or wrong credentials
- Command or test: docker compose logs app-02 | grep dependency_error; compared config/app.env against docker-compose.yml service definitions
- Actual output: app log showed "error_type":"OperationalError" for postgres and "error_type":"ConnectionError" for redis;
  config/app.env had DATABASE_URL pointing to postgres:5433 (actual: 5432, no override in compose) with password
  ending in "...vK8d", while docker-compose.yml POSTGRES_PASSWORD ends in "...vK8c"; REDIS_URL pointed to redis:6380
  (actual: 6379, no override in compose)
- Failed attempt and what changed your thinking: None — comparing the two files directly showed all three mismatches at once
- Root cause: config/app.env had three unrelated but compounding errors: wrong PostgreSQL port (5433 vs 5432),
  a one-character password mismatch with docker-compose.yml's POSTGRES_PASSWORD, and wrong Redis port (6380 vs 6379)
- Fix: corrected config/app.env — DATABASE_URL now uses port 5432 and the matching password (...vK8c);
  REDIS_URL now uses port 6379
- Retest evidence: after `docker compose up -d --build` (app.env is baked into the image), /ready now returns
  {"status":"ready","dependencies":{"postgres":"ready","redis":"ready"}}
- Related commit: [16eed12]
- Remaining uncertainty: None for this specific issue

## Entry 7 / 2026-09-09 / 21:05 UTC
- Symptom: postgres and redis containers had host ports published (127.0.0.1:15432:5432 and 127.0.0.1:16379:6379), allowing direct access to the databases from the host machine, bypassing the app layer
- Hypothesis: removing the `ports` mapping for postgres and redis in docker-compose.yml would block host-level access while keeping inter-container communication intact (since services on the same Compose network communicate via service name regardless of published ports)
- Command or test: `docker compose -p barq-assessment up -d --force-recreate postgres redis` then `docker port postgres` and `docker port redis`; also `curl -s http://127.0.0.1:8080/ready`
- Actual output: `docker port postgres` and `docker port redis` returned no output (no published ports); `/ready` still returned `{"status":"ready","dependencies":{"postgres":"ready","redis":"ready"}}`
- Failed attempt and what changed your thinking: None — removing the `ports` lines was sufficient on the first attempt
- Root cause: docker-compose.yml unnecessarily published postgres (15432->5432) and redis (16379->6379) directly to 127.0.0.1 on the host, violating the requirement that only NGINX be reachable from the host
- Fix: removed the `ports:` line from both the postgres and redis service definitions in docker-compose.yml
- Retest evidence: `docker port postgres` and `docker port redis` both return empty (no host ports published); `/ready` continues to report both dependencies as ready, confirming app-01/app-02 still reach postgres and redis internally via service name over the backend network
- Related commit: [9295005]
- Remaining uncertainty: None for this specific issue

## Entry 8 / 2026-09-09 / 21:52 UTC
- Symptom: PostgreSQL data did not persist across container recreation; named volume `postgres-data` was mounted to `/var/lib/postgresql/backup` (not PostgreSQL's actual data directory), while `/var/lib/postgresql/data` was overridden with `tmpfs`, so all data lived in RAM and was wiped on every container removal
- Hypothesis: remapping the named volume to the correct data directory and removing the tmpfs override would make records survive container recreation
- Command or test: created a record ("Persistence proof", id 3), ran `docker compose up -d --force-recreate postgres`, confirmed id 3 still present with no init.sql re-run; added a second record ("Real persistence proof v2", id 4); ran full `docker compose down` (no -v) then `up -d`; checked `/records` again
- Actual output: after `--force-recreate postgres` alone, id 3 remained and init.sql did not re-seed; after full `down`/`up -d`, all four records (ids 1-4) were present with no duplication or reset
- Failed attempt and what changed your thinking: an earlier persistence test (before this fix was actually applied to the running container) appeared to pass — id 3 survived a down/up cycle — but this was a false positive: tmpfs was still wiping data, and init.sql was simply reseeding ids 1-2 from scratch each time, with the next inserted record coincidentally landing on id 3 again due to auto-increment starting fresh. This taught me to verify that a config change is actually live in the running container (via `--force-recreate` + a config check) before trusting a retest, and to use a second distinguishing record + a second full down/up cycle to rule out coincidental ID matches
- Root cause: docker-compose.yml mounted the named volume to the wrong path (`/backup` instead of `/data`) and additionally overrode the real data directory with `tmpfs`, so PostgreSQL never wrote to persistent storage
- Fix: changed the volume mount from `postgres-data:/var/lib/postgresql/backup` to `postgres-data:/var/lib/postgresql/data`; removed the `tmpfs: [/var/lib/postgresql/data]` line entirely
- Retest evidence: records with ids 1-4 all survived a full `docker compose down` (without `-v`) followed by `up -d`, with no init.sql re-seeding and no ID reset
- Related commit: [078c2d9]
- Remaining uncertainty: None for this specific issue

## Entry 9 / 2026-09-09 / 22:15 UTC
- Symptom: Dockerfile created a dedicated non-root user (`app`, uid 10001) via `groupadd`/`useradd`, but the final `USER root` directive before CMD meant the container actually ran as root, contradicting the task's requirement to avoid root/privileged operation where practical
- Hypothesis: switching `USER root` to `USER app` would run the process as the intended non-root user without breaking functionality, since no remaining step in the Dockerfile required root privileges after the earlier removal of `COPY config/app.env /srv/app.env`
- Command or test: changed `USER root` to `USER app` in Dockerfile; `docker compose build --no-cache app-01 app-02`; `docker compose up -d --force-recreate app-01 app-02`; `docker compose ps -a`; `curl /ready`; `curl -X POST /records`; `docker exec app-01 whoami`
- Actual output: both containers built and started healthy; `/ready` returned `{"status":"ready", ...}`; POST to `/records` succeeded (id 5, "USER app test"); `docker exec app-01 whoami` returned `app`
- Failed attempt and what changed your thinking: None — the fix worked on the first attempt, confirming `USER root` was leftover/unnecessary rather than required
- Root cause: Dockerfile ended with an unnecessary `USER root` directive, likely a leftover from when the image also copied a secrets file (`config/app.env`) into `/srv`; once that COPY was removed, no step required root privileges at runtime
- Fix: changed `USER root` to `USER app` in Dockerfile (final line before EXPOSE/CMD)
- Retest evidence: `docker exec app-01 whoami` confirms the process runs as `app`, not `root`; all endpoints (/ready, /records) continue to function normally with no permission errors
- Related commit: [489a291]
- Remaining uncertainty: None for this specific issue

## Entry 10 / 2026-09-09 / 22:50 UTC
- Symptom: with one backend (app-02) stopped, NGINX returned raw 502/504 errors to the client for roughly half of all requests instead of transparently serving them from the healthy backend (app-01)
- Hypothesis: NGINX's default `proxy_next_upstream off;` setting disabled automatic failover to the other pool member on connection failure/timeout/5xx
- Command or test: stopped app-02; ran validate.py before and after adding `proxy_next_upstream error timeout http_502 http_503 http_504;` and `proxy_next_upstream_tries 2;` to nginx.conf
- Actual output: before the fix, 6/12 validate.py checks failed with 502/504 while app-02 was down; after the fix (and loading it correctly — see note below), 11/12 checks passed while app-02 was down, with only `both_backends_serving` failing as expected, and all other endpoints (health, records, counter, unknown_route) served successfully via app-01 with no client-visible errors
- Failed attempt and what changed your thinking: reloading the new nginx.conf by running `docker compose restart nginx` AFTER already stopping app-02 caused NGINX itself to fail to start entirely (`nginx: [emerg] host not found in upstream "app-02:8080"`), because NGINX resolves all upstream server names once at config-load time and refuses to start if any name cannot be resolved. This taught me that NGINX's static `upstream` block treats DNS resolution as a hard startup dependency, not a runtime check — a real single point of failure if NGINX itself ever needs to restart while any backend is down. The correct fix was to reload NGINX's config via `--force-recreate` while ALL backends were still running, and only stop a backend afterward, without touching NGINX again
- Root cause: `nginx.conf` explicitly set `proxy_next_upstream off;`, disabling NGINX's default retry-on-failure behavior between pool members
- Fix: changed to `proxy_next_upstream error timeout http_502 http_503 http_504;` with `proxy_next_upstream_tries 2;`
- Retest evidence: validate.py confirms 11/12 (only the both-backends-presence check fails, by design) while one backend is down, and 12/12 once it is restored, with zero 502/504 responses reaching the client in either state
- Related commit: [5d9cb0b]
- Remaining uncertainty: NGINX's static upstream resolution at startup remains a known limitation — if NGINX itself is ever restarted while a backend is unreachable, NGINX will fail to start entirely. Documented as a production follow-up in security_review.md (needs `resolver` + dynamic resolution, or an orchestrator like Kubernetes/Consul that handles this natively)

## Entry 11 / 2026-09-10 / 01:19 UTC
- Symptom: validate.py was a placeholder stub that exits 2 ("NOT IMPLEMENTED")
- Hypothesis: N/A — required deliverable, not a bug to diagnose
- Command or test: implemented validate.py per assessment/TASK.md Part 3
  (public access, /health, /ready, both backends via /instance, /records GET+POST,
  /counter increment, unknown route 404, network isolation for postgres/redis)
- Actual output: `python3 validate.py` -> 12/12 checks PASS, exit code 0:
  public_access, health_endpoint, ready_endpoint (postgres/redis both ready),
  instance_endpoint, both_backends_serving (app-01 and app-02 both observed),
  records_get, records_post_valid, records_post_invalid_title_rejected (400),
  counter_endpoint (incremented 13 -> 14), unknown_route_404, no_host_port_postgres,
  no_host_port_redis
- Failed attempt and what changed your thinking: None
- Root cause: N/A (deliverable implementation)
- Fix: implemented validate.py with bounded waits and PASS/FAIL reporting
- Retest evidence: see "Actual output" above — full run, exit code 0
- Related commit: [db13814]
- Remaining uncertainty: None

## Entry 12 / 2026-09-10 / 01:24 UTC
- Symptom: failure_test.py and backup.sh/restore.sh were placeholder stubs that exit 2 ("NOT IMPLEMENTED")
- Hypothesis: N/A — these were required deliverables, not bugs to diagnose
- Command or test: implemented failure_test.py to stop app-01, verify app-02 keeps serving,
  restart app-01, and verify it rejoins the pool
- Actual output: `python3 failure_test.py` -> 7/7 checks passed
  (baseline_both_instances_serving, stop_target_backend, survivor_keeps_serving_during_outage
  [0/20 errors during outage], public_endpoint_still_reachable_during_outage,
  restart_target_backend, target_backend_healthy_again, recovered_backend_serving_traffic)
- Failed attempt and what changed your thinking: None
- Root cause: N/A (deliverable implementation, not a bug fix)
- Fix: implemented failure_test.py per assessment/TASK.md Part 3 requirements
- Retest evidence: exit code 0, 7/7 PASS, zero errors observed on the surviving backend during the outage
- Related commit: [c45b308]
- Remaining uncertainty: None

Do not fabricate a failed attempt just to fill the template. Record actual attempts.
