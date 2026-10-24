# Protocol and lifecycle validation spike

Updated 2026-10-24: **15 real-tusd probes and 10 lifecycle tests passed**, including nine process-crash checkpoints and cross-process races. Tested tusd v2.10.1 / tuspy 1.1.0 on Linux x86_64 / Python 3.13.15. The validated lifecycle prototype now lives in `src/ff/core/lifecycle.py`; these probes exercise that implementation, not a separate mock. Deployment acceptance gates remain below.

**PostgreSQL update:** the lifecycle implementation now requires `FF_DB_*` pointing to an initialized disposable PostgreSQL database. The full application suite passes 78 tests plus nine crash subtests, including a real database outage during upload. Run `dev_scripts/test_local.sh` for isolated database setup and cleanup. The protocol runner's lifecycle probe also writes PostgreSQL records; it no longer runs the standalone sidecar test suite.

From the repository root:

```bash
bash spike/run.sh
```

Requires Linux x86_64, Python 3.13, uv, curl, tar, sha256sum and network access to GitHub/PyPI. The runner verifies the pinned release archive SHA-256, installs the captured dependency versions into a temporary environment, and cleans that environment afterward. The test harness starts its own tusd and stdlib HTTP hook stub on random loopback ports, uses temporary storage, and stops its processes on completion. It needs no Docker daemon, credentials, production storage or existing services. Two local ports must be available; the tusd port is selected before process startup, so concurrent port allocation can require a rerun.

To reuse an existing environment and binary:

```bash
/path/to/venv/bin/python spike/validate.py --tusd /path/to/tusd
```

The harness enforces the tusd/tuspy versions. `requirements.txt` captures the initial experiment's transitive versions; it is not the production dependency policy.

## Observed results

| Probe | Observation | Design consequence |
|---|---|---|
| Extensions and download blocking | Creation, deferred length, creation-with-upload and termination advertised; checksum absent. `-disable-download` makes file GET return 405. | Disable unused extensions; reject deferred lengths in the production pre-create hook. Do not depend on per-chunk checksum enforcement. |
| IDs | Generated IDs are 32 lowercase hex characters; `ChangeFileInfo.ID` is honored. | Default IDs are suitable, with entropy established by source review below, not samples. |
| Pre-create rejection | `RejectUpload` produces the configured 403. | Admission validation can reject before creation. |
| Pre-finish rejection flag | `RejectUpload` is ignored; PATCH returns 204 and full bytes remain. | Never use this flag for whole-file hash rejection. |
| Custom pre-finish error | PATCH returns configured 422; HEAD still reports the full offset and bytes remain. | HTTP failure does not roll back upload storage. |
| JSON body with default final status | Final response remains 204 with an empty body. | Return ready references through the info API. |
| Hook failure and timeout | Hook 500 or exceeding the configured 300 ms test timeout causes PATCH 500; full bytes remain detectable by HEAD and storage inspection. | Reconcile completed bytes independently of hook success. The production default hook timeout is 15 seconds. |
| Zero-byte / creation-with-upload | Both finish on POST with 201, persisted bytes and post-finish events. | Verification must handle completion without a PATCH. |
| Lost final response | A raw client disconnects while pre-finish is held; a new HEAD retrieves the complete offset and stored bytes survive. | Recover transport progress from the persisted URL; verification status still requires File Friend metadata. |
| Hardlink and termination | Link shares the source inode; tus DELETE removes data and `.info`, emits post-terminate and returns 404 on subsequent HEAD. Published link remains readable. | File Friend must unpublish and reconcile its own links. |
| Cross-process tuspy resume | First process stops at 65,536 bytes; second process uses the same persisted URL and finishes a 262,144-byte file exactly. | Persistent URL storage works for an unchanged file. |
| Changed-file fingerprint | Equal-size files with identical first 65,536 bytes but different tails get identical default fingerprints despite differing SHA-256. | Supply a full SHA-256/size/server fingerprint; do not use the default for correctness. |

## Pinned source inspection

- [tusd v2.10.1 ID generator](https://github.com/tus/tusd/blob/v2.10.1/internal/uid/uid.go) reads 16 bytes using `crypto/rand` and hex-encodes them: 128 random bits.
- [tusd v2.10.1 filestore](https://github.com/tus/tusd/blob/v2.10.1/pkg/filestore/filestore.go) reconstructs offset from the data file's size; `FinishUpload` is a no-op. A reconciliation candidate is a known-length, non-concatenated upload whose data size equals declared size. This is not proof of SHA-256 verification or freedom from concurrent mutation. Coordinate with tusd before consuming it; do not treat the `.info` offset alone as authoritative.
- The installed tuspy 1.1.0 `tusclient/uploader/uploader.py` creates uploads with an empty POST. It does not use creation-with-upload in its standard synchronous upload path. The server extension was tested directly with HTTP.
- The installed tuspy 1.1.0 `tusclient/request.py` can add chunk checksums, but the tested tusd filestore does not advertise checksum support. This option is not an end-to-end integrity solution for this stack.
- tusd binary commit: `388a27cb81c33b2349e0308b5e97e23797e86fd9`. Linux archive SHA-256: `e8c8d7738275d2d9377db953aecbc242262d3620e77fbd3c1491361d9cea6ac8`.

## Lifecycle follow-up results

- Killed tusd during a PATCH after bytes reached disk, restarted it on the same volume, resumed from HEAD's offset and recovered the exact 256 KiB fixture.
- DELETE during a live, stalled PATCH terminates the upload through tusd's own locking; subsequent HEAD and PATCH return 404. The spike uses 50 ms lock polling to keep this probe fast; production defaults differ.
- The real filestore plus lifecycle verifier publishes matching bytes and removes mismatches through tusd DELETE. Zero-byte behavior is also covered by the application integration test.
- Separate processes exit abruptly at `verifying`, `hashed`, `publishing`, `linked`, `ready`, `deleting`, `unpublished`, `terminated`, and `deleted`. A new store instance reconciles to ready or the deletion tombstone without republishing deleted IDs.
- A verifier paused after hashing loses to deletion in another process; three simultaneous verifiers publish idempotently.
- Reconciliation discovers completed uploads without either post-create or post-finish notifications, leaves partials unpublished, retains mismatch failures after restart, and sweeps expired ready files.
- A failed termination leaves an unpublished, retryable tombstone. An unrelated existing hardlink is never overwritten.

`test_lifecycle.py` uses a fake storage-termination adapter for deterministic process-crash boundaries. Actual tusd cancellation/termination is covered separately by `validate.py`; `tests/test_live_tusd.py` tests the real Flask hook/worker/tusd flow, including pre-terminate coordination. No test here claims power-loss durability.

The completed server/CLI suite passed **78 tests plus nine crash subtests**, with `TUSD_BIN` set so the real Flask/tusd integration test ran (no skipped integration test). Ten lifecycle tests are included in that count; the 15 protocol probes run separately. Ruff, wheel build/contents checks and installed CLI/worker help commands also passed.

Additional implementation tests cover concurrent admission without overbooking, injected ENOSPC during atomic reservation writes, corrupt-record isolation, API authentication, token rotation/shared administration, malformed metadata and client redirect handling.

## Completed server/CLI follow-up and remaining deployment gates

Application tests now cover incomplete inactivity/absolute expiry, stale-candidate rechecks through real tusd hooks, orphan reservation recovery, late creation, retention cleanup, worker timeout/single-instance supervision, client-process death, lost PATCH acknowledgements, changed-file refusal, Range 200/206/416 handling, malicious references and helper cleanup. The production client uses an explicit stdlib adapter; tuspy remains the baseline spike only.

A 100 MiB upload/download through the built Docker/nginx stack passed SHA-256, Range, authentication boundary and deletion checks. This caught and fixed nginx regex-capture clobbering in the static-file route. Application tests use native Python 3.13.15; the pinned Docker image uses Python 3.13.17.

Remaining production gates:

- Real disk exhaustion under sustained multi-upload load (current ENOSPC test is injected).
- Deployment-volume hardlinks, fsync/rename behavior and power-loss durability.
- 20–40 GB throughput/streaming-memory benchmarks on the selected target disk.
- Gatekeeper/TLS integration, Loki/Prometheus, and exact expiry if required.
- A real MCP tool pilot and license selection before publication.
