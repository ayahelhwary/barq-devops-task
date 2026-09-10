<img src="assets/barq-logo.svg" alt="BARQ Systems" width="180">

# DevOps Internship Assessment — Solution

This repository contains my completed solution for the BARQ Systems DevOps Internship Assessment.

The provided environment was intentionally broken. The solution focuses on troubleshooting the supplied application, repairing the Docker/NGINX/PostgreSQL/Redis environment, validating the required behavior, testing failure recovery, and documenting the investigation and design decisions.

> **Lab environment only:** The supplied credentials and data are synthetic and intended only for this disposable assessment environment. Do not reuse them for real services or expose this environment publicly.

---

## Architecture

The application consists of:

* NGINX as the only publicly exposed service.
* Two Flask application instances: `app-01` and `app-02`.
* PostgreSQL for persistent application data.
* Redis for counter/state functionality.
* Separate frontend and backend Docker networks.
* A named PostgreSQL volume for persistence.

Traffic flow:

```text
Host
  |
  | 127.0.0.1:8080
  v
+----------------+
|     NGINX      |
|      :80       |
+----------------+
        |
        | frontend network
        |
   +----+----+
   |         |
   v         v
+-------+ +-------+
|app-01 | |app-02 |
| :8080 | | :8080 |
+---+---+ +---+---+
    |         |
    +----+----+
         |
         | backend network
     +---+-------+
     |           |
     v           v
+----------+ +-------+
|PostgreSQL| | Redis |
|   :5432  | | :6379 |
+----------+ +-------+
     |
     v
postgres-data
```

The backend network is marked as internal, so PostgreSQL and Redis are not published to the host.

Only NGINX is published on the host.

---

## Prerequisites

* Linux, macOS, or WSL2 with a Linux-compatible Docker environment.
* Docker with Docker Compose.
* Python 3.12+.
* Git.
* Internet access for the initial image/package downloads.

Verify the environment:

```bash
docker version
docker compose version
python3 --version
git --version
```

---

## Setup

Clone the repository and enter the project directory:

```bash
git clone <repository-url>
cd barq-academy
```

Create the local environment file:

```bash
cp .env.example .env
```

The default public port is `8080`.

Check the repository state:

```bash
git status
git log -2 --oneline
```

---

## Build and Run

Build and start the complete stack:

```bash
docker compose -p barq-assessment up --build -d
```

Check the service status:

```bash
docker compose -p barq-assessment ps -a
```

View logs:

```bash
docker compose -p barq-assessment logs --no-color
```

Follow logs for a specific service:

```bash
docker compose -p barq-assessment logs -f nginx
docker compose -p barq-assessment logs -f app-01
docker compose -p barq-assessment logs -f app-02
docker compose -p barq-assessment logs -f postgres
docker compose -p barq-assessment logs -f redis
```

---

## Application Endpoints

The public API is accessed through NGINX:

```text
http://127.0.0.1:8080
```

Useful endpoints:

```bash
curl -s http://127.0.0.1:8080/
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/ready
curl -s http://127.0.0.1:8080/instance
curl -s http://127.0.0.1:8080/records
curl -s http://127.0.0.1:8080/counter
```

The `/ready` endpoint verifies PostgreSQL and Redis readiness.

The `/instance` endpoint can be called repeatedly to observe traffic reaching both `app-01` and `app-02`.

---

## Validation

Run the automated environment validation:

```bash
python3 validate.py
```

The default validation URL is:

```text
http://127.0.0.1:8080
```

A custom URL can be supplied when required:

```bash
python3 validate.py --url http://127.0.0.1:8080
```

The validation checks:

* Public NGINX access.
* `/health` and `/ready`.
* PostgreSQL and Redis readiness.
* Both application instances serving traffic.
* `/records` GET and POST behavior.
* Invalid record rejection.
* Redis-backed counter behavior.
* Unknown routes returning `404`.
* PostgreSQL and Redis having no published host ports.

A successful run should report:

```text
12/12 checks passed.
```

---

## Failure and Recovery Test

Run:

```bash
python3 failure_test.py
```

The test:

1. Confirms both application instances are serving traffic.
2. Stops `app-01`.
3. Sends requests while `app-01` is unavailable.
4. Verifies `app-02` continues serving traffic.
5. Restarts `app-01`.
6. Waits for it to become healthy.
7. Verifies that the recovered instance receives traffic again.

The test is bounded and exits non-zero if a required recovery check fails.

---

## PostgreSQL Backup

Create a PostgreSQL custom-format backup:

```bash
./backup.sh
```

Backups are written to:

```text
./backups/
```

The script automatically creates a timestamped `.dump` file.

A specific PostgreSQL container can also be selected through:

```bash
POSTGRES_CONTAINER=postgres ./backup.sh
```

Do not commit generated backup files to Git.

---

## PostgreSQL Restore

Restore the most recent backup:

```bash
./restore.sh
```

Or specify a backup explicitly:

```bash
./restore.sh ./backups/<backup-file>.dump
```

The restore uses `pg_restore` against the PostgreSQL container.

For persistence testing, verify the data after restoring and restarting/recreating the application environment.

Do not use `docker compose down --volumes` during normal persistence testing because that removes the named PostgreSQL volume.

---

## Network Isolation

The intended network layout is:

* `frontend`: NGINX and Flask application instances.
* `backend`: Flask application instances, PostgreSQL, Redis, and NGINX.
* `backend` is an internal Docker network.

PostgreSQL and Redis do not publish host ports.

Verify this with:

```bash
docker port postgres
docker port redis
```

Both commands should produce no published-port mappings.

NGINX is the only service exposed to the host:

```bash
docker port nginx
```

---

## Persistence

PostgreSQL uses the named Docker volume:

```text
postgres-data
```

mounted at:

```text
/var/lib/postgresql/data
```

This keeps database data separate from the PostgreSQL container lifecycle.

The persistence behavior was verified using backup/restore and container lifecycle testing.

---

## CI

The repository includes:

```text
.github/workflows/ci.yml
```

The CI workflow performs automated checks for the project.

CI should be treated as an additional verification layer; local validation and the actual Docker environment remain the primary evidence for the assessment.

Secrets required by CI are provided through GitHub Actions repository secrets rather than being hard-coded into the workflow.

---

## Troubleshooting and Investigation

The troubleshooting investigation is documented in:

```text
troubleshooting.md
```

The historical synthetic logs are analyzed in:

```text
log_analysis.md
```

The documentation records the observed symptoms, root causes, fixes, verification steps, and relevant evidence.

---

## Design Decisions

The main design decisions are documented in:

```text
decisions.md
```

Key decisions include:

* Binding the Flask applications to `0.0.0.0` inside their containers.
* Using a named PostgreSQL volume at the actual PostgreSQL data directory.
* Running the application as a non-root user.
* Keeping PostgreSQL and Redis private to the Docker network.
* Exposing only NGINX to the host.
* Configuring NGINX to fail over between the two application instances.

---

## Security Review

Security findings and their implementation status are documented in:

```text
security_review.md
```

The review distinguishes between issues fixed as part of this assessment and additional production-hardening recommendations.

---

## Evidence

The evidence index is available at:

```text
docs/EVIDENCE_INDEX.md
```

It maps assessment requirements to the relevant documentation, commands, tests, and repository evidence.

---

## Recorded Challenge

The supplied challenge script is intentionally kept unchanged.

Run it only during the required continuous video recording:

```bash
./video_challenge.sh
```

The challenge starts from the repaired environment and performs the required live runtime changes.

The challenge receipt is retained at:

```text
.assessment/challenge.json
```

The challenge should not be reset by deleting its one-run marker or by using `docker compose down` to reset the runtime state.

---

## Cleanup

Outside the recorded challenge, stop the assessment environment with:

```bash
docker compose -p barq-assessment down
```

Do not use:

```bash
docker compose down --volumes
```

when database persistence needs to be preserved.

Avoid global Docker cleanup commands such as:

```bash
docker system prune
```

because they may affect unrelated Docker resources.

---

## Repository Documentation

| Document                                | Purpose                                   |
| --------------------------------------- | ----------------------------------------- |
| `assessment/TASK.md`                    | Assessment requirements                   |
| `assessment/APPLICATION.md`             | API contract                              |
| `troubleshooting.md`                    | Investigation and fixes                   |
| `log_analysis.md`                       | Historical log analysis                   |
| `decisions.md`                          | Architecture and implementation decisions |
| `security_review.md`                    | Security findings and status              |
| `AI_USAGE.md`                           | AI assistance disclosure                  |
| `docs/EVIDENCE_INDEX.md`                | Evidence mapping                          |
| `architecture.png` / `architecture.pdf` | Architecture diagram                      |

---

## Final Verification

Before submission, run:

```bash
git status
docker compose -p barq-assessment ps -a
python3 validate.py
python3 failure_test.py
```

Also verify that:

* Both `app-01` and `app-02` are healthy.
* PostgreSQL and Redis have no published host ports.
* The application is reachable through NGINX.
* PostgreSQL data persists as expected.
* Required documentation is present.
* No real secrets, backups, virtual environments, or challenge state are unintentionally committed.
* The recorded challenge is performed only once in the video working copy.
