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
