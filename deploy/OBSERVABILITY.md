# File Friend observability handoff — preparation only

Applied ByteForge byteforge-loki-logging and byteforge-prometheus-metrics skills.
Application tags: `file-friend-api` and `file-friend-worker`. No Redis. No live
scrape jobs, dashboards, alerts or Loki settings have been installed.

## Logging

API initializes logging inside create_app(), after Gunicorn forks (never preload).
Supervisor and each spawned reconciliation child initialize their own logger.
Child exceptions become a safe failure event and exit status, not a traceback
containing capability paths. Child shutdown flushes with a bounded timeout.

Set DEBUG_LOCAL=false and LOG_LEVEL=INFO on API and worker; supply privately:
LOKI_ENDPOINT=https://LOKI_HOST/loki/api/v1/push, LOKI_USER, LOKI_PASSWORD,
LOKI_CA_BUNDLE_PATH=/app/certs/loki-ca.pem. Apollo mounts its existing
/path/to/approved/loki-ca.pem at that path read-only. The full push path is
required. Credentials in the URL, query strings and disabled TLS verification
are rejected. Missing configuration fails startup; an unavailable Loki probe
falls back to safe stdout with a visible warning, per the library's behavior.
This fallback does not automatically reconnect: restart after Loki recovers.

A handler filter runs before either stdout or the Loki queue. It copies only
static event names and bounded method/route/status/duration/failure fields into
a fresh LogRecord. Unknown dependency errors become `dependency_error`; their
messages, arbitrary extras and tracebacks are dropped. This intentionally trades
traceback detail for capability secrecy. No raw access logs: safe request events
supply route-class timings. Gunicorn master handlers also receive the filter.
Loki transport-internal diagnostics remain library-managed; endpoint validation
prevents embedded credentials/capabilities there. Do not log request bodies or
add handlers that bypass the filter. Raw tusd logging remains disabled.

ByteForge logger pinned at 3df22201209c5ccfd3800ec8e7e5fc26d4784d44;
waylay python-logging-loki at 2b34c1ee2be8fd220191a184a4929af646df52ae.
Both commits predate the seven-day gate. Builder fetches exact commits and builds
wheels with hash-locked build tools; runtime installation is --no-deps. Requests,
rfc3339 and Prometheus dependencies are hash-locked with the seven-day age gate.
The explicit waylay pin constrains the upstream library's unpinned dependency.

## API metrics

GET /metrics on the API's private port 8081. Keep it blocked at the public nginx
vhost. API metrics use bounded method, route and status labels; arbitrary methods
collapse to OTHER and paths/IDs never become labels. Metrics requests are excluded.

Use the image's default CMD: sh /app/entrypoint.sh. Do NOT override it with a raw
gunicorn command; that skips shard cleanup. /app/gunicorn_conf.py selects four
workers, no preload, master log filtering and child_exit shard cleanup. The
entrypoint sets the fixed directory /tmp/file-friend-prometheus and removes only
its contents before startup. Mount it as tmpfs with uid=1000,gid=1000,mode=0700.
A bad mount fails startup visibly; it does not reuse stale shards. Do not share
this directory with another API instance or the worker.

- ff_http_requests_total{method,route,status}
- ff_http_request_duration_seconds{method,route} histogram
- ff_database_errors_total: API DatabaseUnavailable responses

Flask exporter provides the internal multiprocess endpoint. Its default metrics
are disabled; our explicit metrics bound HTTP method labels as well as routes.
Do not use the skill's flask_http_* dashboard queries for these custom names.
Counters and histograms preserve totals through Gunicorn worker churn and reset
on container startup. Metric application labels come from scrape target config.

## Worker metrics

Set FF_WORKER_METRICS_HOST=0.0.0.0 and FF_WORKER_METRICS_PORT=9101 in the private
worker container only. Default native host is loopback; port 0/unset disables
serving. Do NOT set PROMETHEUS_MULTIPROC_DIR in the worker environment. The
supervisor serves metrics; spawned passes do not bind ports or own counters.

- ff_worker_passes_total{result}: success/failure, including timeout as failure
- ff_worker_pass_duration_seconds histogram
- ff_worker_healthy, ff_worker_heartbeat_age_seconds, ff_worker_consecutive_failures
- ff_database_up: query success; on DB failure state/commitment series are omitted
  rather than reporting misleading zeros. Disk and heartbeat gauges remain.
- ff_uploads{state}, ff_upload_oldest_age_seconds{state}: admission age, NOT time
  in state or inactivity age; do not use it to infer incomplete-TTL violations.
- ff_storage_available, ff_storage_free_bytes, ff_storage_budget_bytes,
  ff_storage_reserve_bytes, ff_storage_committed_bytes (nonterminal declared sizes)

Database values are grouped in one short read transaction per scrape. No per-ID
labels and no duplicated per-Gunicorn-worker lifecycle gauges. This adds a
supervisor metrics pool: with FF_DB_POOL_SIZE=4, budget up to 25 client connections
(16 API, 4 pass child, 4 supervisor metrics, 1 healthcheck). PgCat admission and
queueing still apply; no new schema or server-side pooling changes.

## Owner actions and acceptance

Apollo: prepare private-only metrics routing. Its proposed tailnet ports 9861
(API /metrics only) and 9862 (worker 9101) can be used, subject to owner review.
Do not publish API port 8081 directly on the tailnet merely to expose metrics:
that would expose hooks/admin routes too. Use a metrics-only nginx listener and
restrict scrape source Janus. Janus owns the scrape jobs/dashboard/alerts.
`prometheus-file-friend.yml`, `file-friend-alerts.yml` and
`file-friend-dashboard.json` are reviewable templates, not applied configuration.

Local tests prove queue sanitization (mock Loki HTTP transport), safe stdout
fallback, database-outage gauges, standalone worker scrape, repeated factories,
and real three-worker Gunicorn counter stability: 20 across ten scrapes; after
killing/replacing one worker and sending another request, 21 across five scrapes.
Real Loki TLS delivery and live Prometheus/Grafana remain deployment acceptance:
verify both application labels, inspect actual payloads/logs for a synthetic
capability sentinel, verify monotonic application counters, check rates against
redacted nginx timings, and verify all metrics routes are absent publicly.
Process_* metrics are not used as proof of multiprocess aggregation.

Container validation: built the service image locally, ran it isolated with no
network as uid1000 and a 0700 uid1000 metrics tmpfs, verified shard creation and
five requests aggregated consistently across ten scrapes. The image is local
validation evidence, not a published production digest. Current tests:49 core
and56 backend plus9 crash subtests; the final heartbeat correction had a targeted
three-test rerun. Existing models code is unchanged.
