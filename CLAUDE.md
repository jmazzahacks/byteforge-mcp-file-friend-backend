# File Friend backend — development guide

Independent backend repository using ByteForge flask-smorest-api, flask-docker-deployment and uv-supply-chain-hardening. Python package `byteforge_mcp_file_friend_backend`; models/core come from immutable token-free GitHub dependencies, never sibling paths.

Keep HTTPException behavior intact. Do not change capability or auth contracts during extraction. API/worker share the service image; no startup migrations. Canonical schema/setup lives here; Athena owns production provisioning/migrations, Apollo owns deployment. Never modify another agent's infrastructure.

Root venv at `bin/activate`; preserve `.gitignore`. Run installed-wheel tests with disposable loopback PostgreSQL only. Install the immutable client test dependency from `test-requirements.txt`; missing it fails collection. The client is not a runtime dependency and stays out of the service image. MIT/public under jmazzahacks approved. Approved public repository: https://github.com/jmazzahacks/byteforge-mcp-file-friend-backend. No production deployment or image publication yet.
