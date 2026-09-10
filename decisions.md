# Technical decisions

Each decision below is backed by a troubleshooting.md entry and a real commit — nothing here is
retroactively invented; the "Why" and "Alternative" reflect what was actually considered at the
time, based on the evidence gathered before the fix.

## Decision 1: Bind Flask to 0.0.0.0, not 127.0.0.1
- **Choice:** Set `APP_HOST=0.0.0.0` inside each app container.
- **Why:** `wget`/`curl` from the `nginx` container to `app-01:8080` returned "Connection refused"
  even though the app was healthy from inside its own container — direct evidence that Flask was
  only listening on loopback, invisible to any other container on the Docker network.
- **Alternative considered:** Use `network_mode: host` for the app containers to bypass the
  binding issue entirely.
- **Trade-off:** `network_mode: host` would have removed Docker's network isolation between
  services and made the "frontend/backend network" requirement in the task impossible to satisfy;
  binding to `0.0.0.0` is the standard, minimal-blast-radius fix and keeps the two-network design.
- **Evidence / commit:** `troubleshooting.md` Entry 4; commit `dd4a554`.
- **Production improvement:** Bind explicitly to the container's network interface rather than
  `0.0.0.0` where the runtime allows it, to avoid accidentally exposing the process on interfaces
  beyond the intended Docker network if the container ever gets a second NIC.

## Decision 2: Fix the PostgreSQL volume mount instead of adding a new one
- **Choice:** Remap the existing named volume `postgres-data` from `/var/lib/postgresql/backup`
  to the actual data directory `/var/lib/postgresql/data`, and remove a `tmpfs` override that was
  shadowing that same path.
- **Why:** Data was being wiped on every container recreation. Investigation showed two compounding
  causes rather than one: the volume was mounted at the wrong path, AND `tmpfs` was mounted over
  the correct path, making the volume irrelevant either way.
- **Alternative considered:** Add a second, correctly-pathed volume alongside the existing
  (wrong) one, leaving the original in place "for backups."
- **Trade-off:** Keeping the original mapping would have left a functionally dead volume in the
  compose file — confusing to a future reader and implying the `/backup` path is doing something
  it isn't. Removing it entirely keeps the config honest about what actually persists.
- **Evidence / commit:** `troubleshooting.md` Entry 8 (including a documented false-positive retest
  that had to be caught before trusting the fix); commit `078c2d9`.
- **Production improvement:** Add a scheduled `pg_dump` job (beyond the manual `backup.sh`) writing
  to separate, off-host storage — a single Docker volume on one host is still a single point of
  failure for data durability.

## Decision 3: Remove host-published ports for PostgreSQL and Redis
- **Choice:** Delete the `ports:` mappings that published `postgres` (15432) and `redis` (16379)
  directly to `127.0.0.1` on the host.
- **Why:** The task requires only NGINX be reachable from the host; the starter config published
  both databases directly, letting anything on the host machine bypass the app layer entirely.
- **Alternative considered:** Keep the ports but restrict them with a firewall rule or bind to a
  non-default interface.
- **Trade-off:** A firewall rule is host-specific and not portable across machines/CI; removing the
  `ports:` mapping is enforced by Compose/Docker itself for anyone who runs this file, with no
  extra configuration needed.
- **Evidence / commit:** `troubleshooting.md` Entry 7 (`docker port postgres`/`docker port redis`
  returned empty after the fix, confirmed by `validate.py`'s `no_host_port_*` checks); commit
  `d0aaa63`.
- **Production improvement:** None needed for this specific control — it's a complete fix. The
  remaining production concern is `/ready` and `/records` are the only *evidence* the app can
  still reach these services after ports are removed; a stronger check would be a dedicated
  network-policy test (e.g., `docker run --network <backend> ... nc -zv postgres 5432` from a
  container NOT on the backend network, expecting failure).

## Decision 4: Run the app container as a non-root user, not root
- **Choice:** Change the Dockerfile's final `USER root` to `USER app` (the non-root user already
  created earlier in the same Dockerfile via `useradd`).
- **Why:** The task requires avoiding root/privileged operation where practical; the image already
  had the machinery for a non-root user, but a leftover `USER root` line negated it.
- **Alternative considered:** Leave `USER root` in place and document it as an accepted risk,
  reasoning that the container has no other privileged access (no host mounts, no `--privileged`).
- **Trade-off:** Running as root inside a container is still a real defense-in-depth loss if the
  application process is ever compromised (e.g., an unpatched Python dependency) — a root process
  inside a container is closer to root on the underlying kernel via any container-escape bug than
  a non-root process is. Given switching cost zero (the user already existed), fixing it outright
  was strictly better than documenting around it.
- **Evidence / commit:** `troubleshooting.md` Entry 9 (`docker exec app-01 whoami` -> `app`, all
  endpoints unaffected); commit `489a291`.
- **Production improvement:** Add a Kubernetes/Compose-level `read_only: true` root filesystem and
  drop all Linux capabilities not required, going beyond just the process UID.

## Decision 5: Enable NGINX failover between backends (`proxy_next_upstream`)
- **Choice:** Change `proxy_next_upstream off;` to
  `proxy_next_upstream error timeout http_502 http_503 http_504;` with `proxy_next_upstream_tries 2;`.
- **Why:** With one backend stopped, `validate.py` showed 6/12 checks failing with raw 502/504
  responses reaching the client, even though a healthy backend (app-01) was available the entire
  time. `off` meant NGINX never tried the second pool member.
- **Alternative considered:** Add an active health-check module (`nginx_upstream_check_module` or
  NGINX Plus's `health_check` directive) so NGINX proactively stops routing to a dead backend
  before a client request ever hits it.
- **Trade-off:** Active health checks require either a paid NGINX Plus license or a third-party
  module not present in the stock `nginx:1.28-alpine` image used here; `proxy_next_upstream` is a
  built-in, reactive (per-request) solution that needed no image change, at the cost of the first
  request to a dead backend still incurring the connect/timeout delay before retrying.
- **Evidence / commit:** `troubleshooting.md` Entry 10 (11/12 `validate.py` checks pass with one
  backend down, versus 6/12 before the fix; zero 502/504 reaching the client in either state);
  commit `5d9cb0b`.
- **Production improvement:** Move to NGINX Plus, a service mesh (e.g., Envoy/Istio), or an
  orchestrator (Kubernetes Service + readiness probes) that removes unhealthy pods from rotation
  proactively, and also solves Decision 5's related limitation below (static upstream DNS
  resolution at NGINX startup — see `security_review.md`).

## Decision 6: Keep real secrets out of git and the built image
- **Choice:** Stop copying `config/app.env` into the Docker image (`COPY config/app.env /srv/app.env`
  removed from the Dockerfile), untrack it from git (`git rm --cached`), add it to `.gitignore`,
  and commit a `config/app.env.example` with placeholder values instead. Also redact the password
  portion of `DATABASE_URL`/`REDIS_URL` before they are logged at startup.
- **Why:** `config/app.env` (containing a real, if synthetic-lab-only, PostgreSQL password) was
  tracked in git and baked into every built image layer — directly violating the task's explicit
  "keep secrets out of images, code and Compose" requirement — and the same password was being
  written to `docker logs` in plaintext on every container start.
- **Alternative considered:** Rotate to a secrets manager (Docker secrets, Vault, or a cloud KMS)
  immediately.
- **Trade-off:** A secrets manager is the right production answer but is disproportionate setup
  for a local Compose lab; the chosen fix (gitignore + `env_file` + redaction) fully satisfies the
  task's stated requirement with no new infrastructure, at the cost of the password still needing
  to be distributed out-of-band (e.g., via `.env.example` + a README instruction) rather than
  centrally managed.
- **Evidence / commit:** `troubleshooting.md` Entry (image/secrets hardening); commits `489a291`
  and `38829d3`.
- **Production improvement:** Adopt a real secrets manager, rotate the lab password (it is
  effectively public now, having been committed to git history even though it has since been
  removed from tracking), and purge it from git history with `git filter-repo`/BFG if this repo
  were ever to become a real production codebase rather than a disposable lab exercise.