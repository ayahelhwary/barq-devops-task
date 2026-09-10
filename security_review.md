# Security and production-readiness review

9 findings below, covering secrets, ports, container user, image selection, networks,
persistence/backup, logging/monitoring, and availability, as required. Completed fixes are
separated from open production follow-ups in each entry.

## Finding 1 — Secrets baked into the Docker image and tracked in git
- **Risk and evidence:** `config/app.env` (containing the real, though synthetic-lab-only,
  PostgreSQL password) was both tracked in git (`git ls-files` confirmed it) and copied into every
  built image layer via `COPY config/app.env /srv/app.env` in the Dockerfile. Anyone with image
  access (`docker save`/`docker history`) or git clone access could extract the password.
- **Impact:** Credential exposure beyond the intended runtime scope; the password becomes
  effectively public the moment the image or repo is shared, regardless of later fixes.
- **Implemented fix / commit:** Removed the `COPY` line from the Dockerfile, ran
  `git rm --cached config/app.env`, added it to `.gitignore`, and committed a
  `config/app.env.example` with placeholder values. Commit `489a291`.
- **Production follow-up:** The password still exists in earlier git history (commit `16eed12`
  and others predate the fix) — untracking a file does not remove it from history. A real
  production incident would require rotating the credential and rewriting history
  (`git filter-repo`/BFG) or, better, moving to a secrets manager entirely.
- **How to verify:** `git log --all --full-history -- config/app.env` still shows the old commits
  containing the plaintext value; `git ls-files | grep app.env` no longer lists it as currently
  tracked; `docker history barq-assessment-app-01` no longer shows an `app.env` COPY layer.

## Finding 2 — Secrets logged in plaintext on every container start
- **Risk and evidence:** `app/server.py`'s startup log line originally logged
  `DATABASE_URL`/`REDIS_URL` verbatim, including the password, into `docker logs` on every start.
- **Impact:** Anyone with read access to container logs (which is often broader than access to the
  env files themselves — e.g., a shared logging platform) could recover the database password.
- **Implemented fix / commit:** Added a `redact()` helper that masks the userinfo portion of the
  URL (`postgresql://***:***@postgres:...`) before logging. Commit `489a291`.
- **Production follow-up:** Extend the same redaction pattern to any future logging of
  connection strings, and consider a structured-logging library with built-in secret-scrubbing
  rules rather than a single hand-written regex.
- **How to verify:** `docker compose logs app-01 | grep configuration_loaded` shows `***:***`
  in place of the credentials.

## Finding 3 — Application container ran as root
- **Risk and evidence:** The Dockerfile created a non-root user (`app`, uid 10001) but ended with
  `USER root` before `CMD`, so the actual running process was root.
- **Impact:** A compromised dependency or application bug has full root privileges inside the
  container, which is a stronger foothold for a container-escape exploit than a non-root process.
- **Implemented fix / commit:** Changed `USER root` to `USER app`. Verified with
  `docker exec app-01 whoami` -> `app`. Commit `489a291`.
- **Production follow-up:** Add `read_only: true` for the container root filesystem and drop all
  unneeded Linux capabilities (`cap_drop: [ALL]`) for further defense in depth beyond just the UID.
- **How to verify:** `docker exec app-01 whoami` returns `app`; `docker exec app-01 id` shows
  uid=10001, not 0.

## Finding 4 — PostgreSQL and Redis were directly reachable from the host
- **Risk and evidence:** `docker-compose.yml` published `postgres` (15432) and `redis` (16379)
  directly to `127.0.0.1`, letting anything on the host bypass the app layer and its authorization
  logic entirely and connect to the databases directly.
- **Impact:** Any process on the host (or, in a shared/cloud host, potentially other
  users/containers depending on network setup) could read/write the database without going
  through the API.
- **Implemented fix / commit:** Removed the `ports:` entries for both services. Commit `d0aaa63`.
  Verified with `docker port postgres`/`docker port redis` returning empty, and codified as an
  automated check in `validate.py` (`no_host_port_postgres`, `no_host_port_redis`).
- **Production follow-up:** Add an explicit network-policy test that attempts a connection from
  outside the `backend` network and asserts failure, rather than only asserting the absence of a
  host port mapping.
- **How to verify:** `docker port postgres` and `docker port redis` both return no output;
  `python3 validate.py` includes and passes both isolation checks.

## Finding 5 — NGINX had no automatic failover between backends
- **Risk and evidence:** `proxy_next_upstream off;` meant that when one backend was stopped,
  roughly half of all client requests received raw 502/504 errors instead of being served by the
  healthy backend — confirmed by `validate.py` showing 6/12 failing checks during a live outage
  test.
- **Impact:** A single backend failure directly and needlessly degraded the client-visible service
  quality by ~50%, defeating the purpose of running two backend instances.
- **Implemented fix / commit:** Enabled `proxy_next_upstream error timeout http_502 http_503
  http_504;` with `proxy_next_upstream_tries 2;`. Commit `5d9cb0b`. Verified: 11/12 `validate.py`
  checks pass during an outage (only the both-backends-presence check fails, by design), 12/12
  once restored.
- **Production follow-up:** See Finding 6 — the fix does not remove the underlying static-DNS
  limitation below.
- **How to verify:** `docker compose stop app-02 && python3 validate.py` — no 502/504 should reach
  the client for any endpoint other than the both-backends-presence check.

## Finding 6 — NGINX resolves upstream hostnames only once, at startup (single point of failure)
- **Risk and evidence:** While testing Finding 5's fix, restarting NGINX while `app-02` was
  stopped caused NGINX itself to fail to start entirely
  (`nginx: [emerg] host not found in upstream "app-02:8080"`). Stock NGINX's `upstream {}` block
  resolves all member hostnames once at config-load time and treats a resolution failure as fatal,
  not as "this member is currently down."
- **Impact:** If NGINX is ever restarted (deploy, crash, host reboot) while any backend happens to
  be unreachable, the entire edge — including the otherwise-healthy backend — goes down. This is a
  single point of failure that failover (Finding 5) does not address, because it only helps once
  NGINX is already running.
- **Implemented fix / commit:** None implemented — this is a structural limitation of stock NGINX
  open-source, not a misconfiguration. Documented as a known limitation in `troubleshooting.md`
  Entry 10.
- **Production follow-up:** Use NGINX's `resolver` directive with a variable-based `proxy_pass`
  (dynamic re-resolution at request time instead of config-load time), NGINX Plus's `resolve`
  upstream parameter, or move backend discovery to an orchestrator (Kubernetes Service/Consul)
  that handles this natively and doesn't require NGINX to know all backend addresses upfront.
- **How to verify:** Stop a backend, then run `docker compose restart nginx` — reproduces the
  `emerg` failure today; the fix would be verified by the same sequence succeeding.

## Finding 7 — No restart policy on postgres, redis, or nginx
- **Risk and evidence:** `docker-compose.yml` sets `restart: "no"` for the app services (via the
  `x-app` anchor) and leaves postgres/redis/nginx at Compose's implicit default (`"no"`) as well.
  If any of these containers crash (OOM, transient error, host Docker daemon restart), they stay
  stopped until someone notices and runs `docker compose up` manually.
- **Impact:** Reduced availability after any transient failure; the task explicitly asks for
  "restart policies" to be set correctly, and none are currently configured beyond the default.
- **Implemented fix / commit:** Not yet implemented at time of writing.
- **Production follow-up:** Add `restart: unless-stopped` to postgres, redis, and nginx (and
  reconsider `"no"` on the app services, weighed against Finding 6 — if nginx or a backend
  restarts in a crash loop while a dependency is down, `unless-stopped` will keep retrying rather
  than surfacing the failure clearly, so this needs to be paired with proper healthchecks and
  alerting rather than applied blindly).
- **How to verify:** `docker inspect <container> --format='{{.HostConfig.RestartPolicy.Name}}'`
  should report `unless-stopped` (or similar) instead of `no`.

## Finding 8 — No CPU/memory resource limits on any service
- **Risk and evidence:** No service in `docker-compose.yml` sets `deploy.resources.limits` (or the
  legacy `mem_limit`/`cpus`). A single misbehaving container (e.g., a memory leak in the Flask app,
  or an unbounded query against PostgreSQL) can consume all host resources and starve every other
  service, including NGINX and the healthy backend.
- **Impact:** A resource issue in one component becomes a full-stack outage instead of a contained
  failure of one container.
- **Implemented fix / commit:** Not yet implemented at time of writing.
- **Production follow-up:** Add explicit memory and CPU limits per service (sized from observed
  baseline usage under `docker stats`), and pair with the readiness/health checks already in place
  so an over-limit container is both constrained and correctly reported as unhealthy rather than
  silently degraded.
- **How to verify:** `docker stats` under a load test should show each container capped at its
  configured limit rather than growing unbounded.

## Finding 9 — No backup automation or off-host storage; single PostgreSQL instance
- **Risk and evidence:** `backup.sh`/`restore.sh` exist and were proven end-to-end (full
  `docker compose down -v` destroying the named volume, then successful `restore.sh` recovery —
  see `troubleshooting.md` Entry 13), but backups are (a) manual/on-demand only, no schedule, and
  (b) written to `./backups/` on the same host as the database itself. There is also only one
  PostgreSQL instance — no replica, no automatic failover for the database layer itself (unlike
  the app layer, which has two instances behind NGINX).
- **Impact:** A host-level disaster (disk failure, accidental `rm -rf`) destroys both the live data
  and every backup simultaneously; PostgreSQL itself remains a single point of failure regardless
  of how well the app/NGINX layer tolerates a backend outage.
- **Implemented fix / commit:** `backup.sh`/`restore.sh` implemented and verified end-to-end
  (commit `dab41c0`); this finding documents what remains beyond that.
- **Production follow-up:** Schedule `backup.sh` via cron/a CI job, ship backups to off-host/object
  storage (S3-compatible), and evaluate PostgreSQL streaming replication or a managed
  database service for the availability half of this problem.
- **How to verify:** Confirm no cron/scheduled job currently exists (`crontab -l`,
  `.github/workflows/`); confirm `./backups/` is local disk only (`df -h .`), not a remote target.
