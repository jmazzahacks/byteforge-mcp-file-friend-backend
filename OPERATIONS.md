# Operations reference

Historical workspace documentation adapted for backend imports. The client is a separate published package, pinned for backend tests in `test-requirements.txt`; validation counts below describe the original workspace, not this new repository. See README.md for current backend installation/tests.

# MCP File Friend

Out-of-band file transfer for remote MCP tools. Upload bytes through tusd, verify SHA-256, and pass a small file reference through MCP.

**Status:** PostgreSQL conversion is implemented and validated locally. The production topology is service/file storage on Apollo and PostgreSQL metadata on Athena. No production deployment has been performed. Gatekeeper/TLS, observability and target-volume acceptance remain.

## Local Docker setup

The backend uses separately packaged models and core, pinned to reviewed Git commits. The Python client is separately packaged. Docker runs the API, tusd, supervised worker and nginx on a shared local volume. Only nginx is exposed, bound to loopback.

Copy `example.env` to `.env` and set distinct random hexadecimal `FF_HOOK_TOKEN`, `FF_DEV_ADMIN_TOKEN` and `FF_DB_PASSWORD` values (for example, generate each with `openssl rand -hex 32`). For a **disposable local stack**, set `FF_DB_HOST=database`, `FF_DB_NAME=ff_test` and `FF_DB_SSLMODE=disable`. The test overlay starts a private PostgreSQL container and initializes its empty volume:

```bash
docker compose --env-file .env -p ff-local-test -f compose.yaml -f compose.test.yaml up --build -d --wait
curl http://127.0.0.1:8081/ready
```

`/health` checks the API; `/ready` additionally checks PostgreSQL and the worker heartbeat. The test database is disposable and has no published port. To remove only that test stack and its data, use the same Compose arguments with `down -v`. The normal `compose.yaml` expects an externally provisioned database and does not migrate it automatically. The local administrator token authorizes all uploads and deletions, including uploads created with another accepted token. Status IDs and download URLs are bearer capabilities. Keep them out of logs. Internal hooks and tusd are private; direct tusd downloads and HTTP method overrides are blocked.

This Compose configuration is for local use. Public deployment requires TLS, the chosen domain, Gatekeeper authentication and storage benchmarking. Do not expose the development token configuration publicly. No image has been published. The selected license is MIT.

## CLI

The `ff` command belongs to the separate Python client package (see `test-requirements.txt` for the reviewed revision), not this backend. Configure `FF_SERVER_URL` and `FF_TOKEN`, or `~/.config/ff/config.toml`:

```toml
server_url = "https://ff.example.com"
# token = "..."  # Protect this file if it contains a token.
```

Environment variables override the file. Loopback HTTP is allowed for local testing. Upload and deletion require the token; status and downloads do not send it.

```bash
ff upload ./report.pdf --ttl 24h
ff download <id-or-url> -o ./report.pdf
ff info <id-or-url>
ff rm <id-or-url>
```

Commands emit JSON on stdout and progress/errors on stderr. Upload returns a ready reference only after verification. Interrupted uploads resume using a locked cache keyed by server and source path, with full SHA-256/size/options checked before reuse. Changed sources require `--restart`. Lost PATCH responses recover via HEAD. `--chunk-size` defaults to 64 MiB; `--timeout` defaults to one hour and should be increased for slow or very large transfers.

Downloads resume through Range requests, verify the complete SHA-256, and atomically replace the destination only after verification. Redirects, foreign origins and malformed references are rejected. Partial files remain for retries. Lock acquisition and response-body reads respect the transfer deadline; special files are rejected. Small lock files intentionally remain beside destinations and in the upload cache to preserve coordination. Configure cache and size limits with `FF_CACHE_DIR` and `FF_MAX_BYTES`.

## Tool helper

```python
from pathlib import Path
from byteforge_mcp_file_friend_api_python import open_reference

with open_reference(reference, server_url="https://ff.example.com") as path:
    process_file(path)

# Tools sharing the trusted server volume can avoid copying:
with open_reference(reference, server_url="https://ff.example.com",
                    local_root=Path("/data/ff")) as path:
    process_file(path)
```

The trusted server may also come from `FF_SERVER_URL`. The helper ignores reference-supplied local paths. It validates identity, size and SHA-256; local traversal rejects symlinks. On Linux, a held descriptor keeps verified bytes accessible even if expiry unlinks the file. Otherwise it downloads into a private temporary directory. Use the returned path only inside the context; temporary bytes are removed on exit. `max_bytes` and `timeout` bound processing.

## Lifecycle and operations

- PostgreSQL is authoritative for lifecycle state and reservations. Admission uses a transaction-scoped advisory lock; version-checked updates reject stale transitions. File bytes stay on the local volume.
- Defaults: 50 GiB/file, 1 GiB free-space margin, 16 pending reservations. Admission is serialized.
- Incomplete uploads expire after 24 hours without activity or seven days total. Expiry rechecks activity inside tusd's termination hook, under tusd's upload lock.
- Reservations with no payload recover after five minutes. Delayed creations retain the expiry tombstone and are terminated on reconciliation.
- Completed TTL starts when ready: 24 hours by default, seven days maximum. Publication uses verified hardlinks. Expiry removes links during a sweep; already-open downloads remain readable.
- Terminal rows are retained for seven days after storage cleanup. No application lifecycle JSON files are written. tusd `.info` files, supervisor heartbeats and client resume records remain protocol/operational files. Fixed local lock stripes protect filesystem transitions.
- One supervisor owns the worker lock. A child watches its parent process and exits if the supervisor dies abruptly, including outside Docker. Each pass runs in a child with a one-hour deadline, bounded retry backoff, heartbeat and graceful shutdown. Compose restarts exited services. Configure thresholds through `example.env`.
- `ff-worker --once` performs one supervised pass; `ff-worker --healthcheck` checks heartbeat health. Workers read exported environment variables; they do not load `.env` themselves.
- During a database outage, admission, metadata APIs and mutations fail closed; existing static downloads can continue and expiry may be delayed. Already-admitted tusd uploads can finish writing; reconciliation resumes after recovery. There is no JSON fallback. Unknown filestore artifacts are reported, never adopted as new database records.
- Missing/corrupt tusd storage artifacts that prevent safe deletion remain reported failures for operator repair. Never manually unlink live uploads. This is a single-host, trusted-local-filesystem design.

Deletion unpublishes immediately. HTTP 202 means storage termination is pending; the worker retries. Unknown IDs return 404; retained deleted/expired IDs return 410. Hash mismatch remains a failed status.

## Development and validation

The development environment follows ByteForge's repository-root convention. Preserve `.gitignore` when creating it:

```bash
ignore_backup=$(mktemp)
cp .gitignore "$ignore_backup"
uv venv --python 3.13 --allow-existing .
cp "$ignore_backup" .gitignore
rm "$ignore_backup"
source bin/activate
uv pip install --require-hashes -r requirements.txt -r dev-requirements.txt -r build-requirements.txt
python dev_scripts/build_internal_wheels.py
uv build --wheel --no-build-isolation
uv pip install --no-deps dist/*.whl
uv pip install --no-deps --no-build-isolation -r test-requirements.txt
TUSD_BIN=/path/to/verified/tusd-v2.10.1 bash dev_scripts/test_local.sh -q
ruff check src tests spike/validate.py spike/test_lifecycle.py
```

`dev_scripts/test_local.sh` creates a temporary PostgreSQL 18 cluster, applies the schema twice, runs tests in isolated schemas, and stops/removes the cluster afterward. Set `FF_TEST_PG_BIN` for a different installed PostgreSQL binary directory. It uses loopback trust authentication exclusively for its disposable cluster; never copy that authentication policy to production. Without `TUSD_BIN`, real-tusd tests explicitly skip. Plain `pytest` without `FF_TEST_DATABASE=1` skips database-dependent tests; those tests are owned by the separate client and core repositories.

Before repository extraction, the full workspace suite passed **78 tests plus nine crash subtests**, including an actual PostgreSQL stop/restart during a tusd upload, lost commit acknowledgement, dead pooled connections, concurrent admission, rollback, retained deletion intent, process death, Range and reference safety. All 15 standalone protocol probes also passed against PostgreSQL. Docker/nginx validation passed a 100 MiB round trip, database stop/restart, continued static downloads during the outage and readiness recovery, with no application sidecar directory. To rerun those probes with `bash spike/run.sh`, first configure `FF_DB_*` for a disposable database and apply `database/schema.sql`; the lifecycle probe writes records there.

### PostgreSQL setup and ownership

Use the ByteForge `postgres-setup` conventions: SQL lives in `database/schema.sql`; explicit setup lives in `dev_scripts/setup_database.py`; the application never creates tables on startup. Schema fields are typed columns, with UUID internal keys, separate random capability IDs, BIGINT Unix timestamps, constraints and pending/expiry indexes.

`FF_DB_HOST`, `FF_DB_PORT`, `FF_DB_NAME`, `FF_DB_USER`, `FF_DB_PASSWORD`, `FF_DB_SSLMODE` and `FF_DB_POOL_SIZE` configure runtime and setup consistently. Defaults are database/user `byteforge_file_friend`, port 5432, pool maximum four **per process**. Production transport/TLS and aggregate connection limits must be agreed with Athena's agent. `FF_DB_SCHEMA` defaults to `public`; the local test harness overrides it for isolation.

For an existing disposable database, run `python dev_scripts/setup_database.py`. To explicitly create a local database/role, add `--create-database --pg-user <admin>` and supply `FF_DB_ADMIN_PASSWORD` (or `--pg-password`). Existing role passwords are not changed by replay; rotate them explicitly through the owner. A fresh schema replay is idempotent; future schema changes require explicit migrations.

The pool checks connections before use, discards broken sockets, bounds checkout retries and uses connection/statement/lock timeouts. A failed commit is not automatically replayed: reconciliation reads durable state first. Database transactions never span hashing or tusd calls. Local filesystem locks and durable publication/deletion intents bridge the non-atomic database/filesystem boundary.

Production provisioning and migration application belong to Athena's agent; host deployment belongs to Apollo's agent, through tickets. No production data exists to migrate. Startup rejects a volume containing legacy lifecycle sidecars; they are neither imported nor deleted automatically: use a clean test volume, or arrange an explicit offline import if those files must be retained. Coordinate database and volume backups; a restored database alone cannot reconstruct file bytes or safely recreate deleted capabilities.

To repeat the Docker check against an isolated local stack, export `FF_SERVER_URL` and `FF_TOKEN`, then run `python tests/validate_compose.py`. It creates and deletes a 100 MiB upload.

`requirements.in`, `dev-requirements.in` and `build-requirements.in` are dependency inputs; corresponding `.txt` files pin versions and hashes. Refresh using `uv pip compile --generate-hashes --exclude-newer '7 days' --output-file <lock> <input>`, preserving compatible constraints across locks, then rerun validation. Docker uses pinned image digests, hashed dependencies, nonroot application processes and a local wheel build. `build-publish.sh` builds locally by default; publishing is explicit and requires a license.

See [PLAN.md](PLAN.md), [CLAUDE.md](CLAUDE.md) and [spike results](spike/README.md) for architecture and remaining deployment gates.

## Code review follow-up

Reviewed the lifecycle/database layer, hooks/authentication, worker, CLI/helper, schema/setup tools, packaging and Docker/nginx configuration. Fixed malformed credential/hook/heartbeat handling, special-file blocking in downloads and verification, transfer lock and slow-body deadlines, HTTP error-response cleanup, stale deletion snapshots, long-lived client deadlines, protocol-error reporting, database cleanup signal handling and worker children surviving supervisor death. Readiness now distinguishes database and worker failures. Regression tests reproduce the original failures; the worker test also fails when the old spawning behavior is intentionally restored.

The pre-split suite had 78 tests plus nine crash subtests; see README.md for current per-repository validation. Production Gatekeeper/TLS, Loki/Prometheus, target-volume power-loss and large-file acceptance remain separate deployment work; this review does not claim those have been implemented or validated.

## Athena PgCat configuration and recovery

Athena review ticket `7e5cd161-2191-4626-ac2a-cf36b9021d0b` approves transaction pooling subject to application corrections and operator decisions. No provisioning or deployment is authorized by this document.

Production uses the Athena-approved tailnet hostname on port **6432**, not direct PostgreSQL 5432. Set `FF_DB_SSLMODE=disable` explicitly: the approved route uses Tailscale WireGuard transport, not PostgreSQL TLS. Apollo owns container routing/DNS and deployment. Passwords must use Athena's private handoff process and never tickets or source files. Local examples use loopback 5432 for disposable testing only.

Athena must provision role defaults `statement_timeout=5s`, `lock_timeout=2s`, `idle_in_transaction_session_timeout=10s`. PgCat drops startup options, so the application omits them and sets transaction-local statement/lock limits. Pre-ping ends its transaction; normal and exceptional exit paths release pool slots. SQLSTATE 58000 with the PgCat pool-checkout/AllServersDown diagnostic becomes HTTP 503 with Retry-After, with bounded acquisition retries and no automatic transaction-body replay. Retry reads or proven-unexecuted writes; reconcile ambiguous mutations before another attempt. A PgCat restart briefly disconnects clients; test reconnect and role defaults through 6432 before rollout.

The initial schema requires a cleanup timestamp for terminal states. This is still the **unprovisioned initial schema**; an existing production schema must not be silently updated via `IF NOT EXISTS`. Athena applies the final hash-pinned initial snapshot under its existence guard. Future schema changes require numbered migrations with published commit and SHA-256, explicit owner-run dry run/rollback and apply.

### Coordinated restore: keep the service stopped

There is **no implemented restore-specific or conservative reconciler mode**. Ordinary reconciliation is not a substitute for restore inventory. Keep public download ingress, API, worker and tusd stopped while Athena restores metadata and Apollo inventories/restores the volume. Static nginx downloads can expose files even without a database row, so blocking ingress is essential.

| Restored mismatch | Current normal behavior | Required offline recovery before reopening ingress |
|---|---|---|
| `ready` row, published bytes missing | Remains ready until expiry; no missing-file repair | Mark for failed cleanup through an explicitly reviewed state correction; do not advertise it as ready. Resolve both source and published artifacts before terminal `failed`/`deleted_at`. |
| `uploading`, `verifying`, `publishing` row | May continue verification/publication if source is complete; otherwise waits or expires | Fail these uncertain transfers through durable `deleting` intent with terminal `failed`, then controlled cleanup. Do not blindly resume the normal worker against a restored snapshot. |
| `deleting` row | Reconciler resumes unpublication and tusd termination | Preserve intent and finish deletion before marking the terminal state with `deleted_at`. Do not erase the intent while artifacts remain. |
| File with no row | Unknown `.info` artifacts are reported as `UnreservedUpload`, never adopted; payloads/publications without `.info` are not comprehensively inventoried | Apollo inventories both `uploads/` and `done/`, quarantines unowned artifacts outside served paths, and never creates database rows from them. Quarantine is an operator action, not an automatic feature. |
| `ready` row and matching files | Normal worker trusts the recorded ready state | Verify identity, size, SHA-256, expiry and deletion/revocation history against the recovery evidence. Do not renew timestamps or restore access to a previously revoked capability. |

Athena owns reviewed database corrections; Apollo owns stopped-volume/quarantine actions. Never unlink an active tusd upload. Prefer the normal deletion protocol in a controlled recovery window after state correction, with ingress still blocked. A terminal timestamp is set only after required storage cleanup. If prior revocations cannot be established from available recovery evidence, retire the affected capability rather than republishing it.

Metadata backups may lag bytes by up to 24 hours under the reviewed daily-dump policy; there is no PITR. Jason accepted the database error-statement logging exposure in Athena's 2026-10-24 ticket note: no role-level log_min_error_statement override is planned. Failed SQL can therefore expose capability IDs/filenames in access-controlled PostgreSQL/Loki logs; application validation remains important. Backup retention is still Jason's pending decision, not an application-agent approval. Enrollment and first successful backup must be verified before production readiness. A coordinated restore drill is still required; this runbook does not claim one has been performed for File Friend.
