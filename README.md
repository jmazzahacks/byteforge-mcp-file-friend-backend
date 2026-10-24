# File Friend backend

Public repository: https://github.com/jmazzahacks/byteforge-mcp-file-friend-backend.

Flask API, tusd hooks and service image for File Friend. Python 3.13+, MIT licensed. Models and core are separate packages pinned to reviewed GitHub commits in `pyproject.toml`. Backend includes no copies of those packages.

## Local validation

The virtual environment lives at this repository root. Preserve `.gitignore` when recreating it.

```bash
source bin/activate
uv pip install --require-hashes -r requirements.txt -r dev-requirements.txt -r build-requirements.txt
python dev_scripts/build_internal_wheels.py
uv build --wheel --no-build-isolation --out-dir dist
uv pip install --no-deps dist/*.whl
uv pip install --no-deps --no-build-isolation -r test-requirements.txt
TUSD_BIN=/path/to/verified/tusd-v2.10.1 bash dev_scripts/test_local.sh -q
ruff check src tests conftest.py dev_scripts spike/validate.py spike/test_lifecycle.py
```

The internal wheel builder fetches the exact public models/core commits from package metadata and uses the installed hash-locked build tools. Docker uses the same builder and installs hash-locked third-party dependencies. No sibling path dependencies or credentials are required.

The test runner initializes and removes only a disposable loopback PostgreSQL cluster. Never point tests at Athena. Tests use installed wheels, with no source-path overrides. The client is pinned to published commit `737c38210654201ff7fd5fa60c23f71898614842` in `test-requirements.txt`, installed separately from hashed third-party locks. It is test-only and is not installed in the service image. A missing client fails test collection instead of silently skipping end-to-end coverage. `tests/validate_compose.py` uses the same client. Real-tusd tests require the verified binary. The 15 protocol probes in `spike/run.sh` additionally require a configured disposable database; the runner installs the pinned core package for them.

## Service and operations

See `OPERATIONS.md` for the existing API, lifecycle, local Compose configuration, operational constraints and historical validation evidence. These notes include examples for the separate Python client; the backend package does not provide the `ff` command. Both API and worker run from the same service image. `database/schema.sql` and `dev_scripts/setup_database.py` are the canonical schema/setup artifacts; apply explicitly, never at runtime. Athena owns production database changes; Apollo owns host deployment.

This extraction preserves API routes, shared administration, `FF_*` configuration and lifecycle behavior. Production Gatekeeper/TLS, observability and target-volume validation remain pending. No deployment or image publication is authorized by this local checkpoint.

## Extraction validation

Initial extraction checkpoint: 28 tests and nine crash subtests passed before client availability. With the client installed from the exact published Git revision in a fresh temporary environment, all 33 tests and nine crash subtests passed without skips. Lint passed, and the temporary environment/database were removed. Lint passed. Docker image built and a disposable Compose stack passed a 1 MiB verified upload/download, Range, deletion, proxy authorization and worker restart checks. Test containers and volumes were removed. The standalone 15-probe protocol suite was retained but not rerun in this checkpoint; its prior evidence is historical. Production scale and 20–40 GB transfers remain unvalidated.

## Athena compatibility review

The backend pins core `67f38db1be02540a85337fc5ba68920cac7e0d78`, which omits PgCat-ignored startup options, enforces transaction cleanup and handles SQLSTATE 58000 as temporary unavailability without replaying writes. Terminal states require `deleted_at` in the initial schema. Deployment examples explicitly use `FF_DB_SSLMODE=disable` for the approved Tailscale route. See OPERATIONS.md for required role defaults, PgCat routing, retry policy and coordinated restore limitations. Validation after review corrections: 34 backend tests and nine crash subtests passed with real tusd and disposable PostgreSQL, plus lint; core passed 42 tests, including real SQLSTATE diagnostics that distinguish PgCat pool failures from unrelated system errors. Athena must still verify its actual PgCat settings after separately authorized provisioning.

Shared-disk admission now supports `FF_STORAGE_BUDGET_BYTES` (200 GiB default), including ready uploads. Apollo sizing requires `FF_MIN_FREE_BYTES=64424509440` (60 GiB); see OPERATIONS.md for accounting and limits. Current validation: 35 backend tests and nine crash subtests; 45 core tests and 28 models tests.

Production authentication now has an explicit `FF_AUTH_MODE=ingress` backend mode:
only the private ingress secret authorizes administration; development bearer
tokens and identity headers do not. Hooks use their own separate credential.
See [the Gatekeeper handoff](deploy/GATEKEEPER.md) for configuration, trust
boundaries and Apollo's required acceptance checks. Local Compose stays in
`development` mode. Live nginx/Gatekeeper wiring remains pending.

Authentication validation: 38 models tests, 45 core tests, 49 backend tests and
nine crash subtests passed with installed wheels, disposable PostgreSQL and real
tusd. Lint and the local nginx configuration syntax check passed.

Apollo review follow-up: URL-safe hook credentials are required; ingress mode hides /internal/auth. Publication permissions are explicit. Local tusd logging persistence is disabled because verbose=false still logs capability paths. See deploy/GATEKEEPER.md for evidence and deployment requirements. Validation: 50 models, 46 core, 52 backend tests plus nine crash subtests passed; lint passed.
