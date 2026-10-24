# Gatekeeper ingress contract for Apollo

File Friend implements the backend half of delegated authorization. Apollo owns
nginx/network changes; Gatekeeper's administrator owns routes and grants. This
contract does not authorize host changes. The shipped `deploy/nginx.conf` and
Compose stack remain local-development validation configurations.

## Service configuration

Set `FF_AUTH_MODE=ingress`, unset `FF_DEV_ADMIN_TOKEN`, and configure
`FF_INGRESS_SHARED_SECRET` with a generated random secret (for example 32 random
bytes encoded as 64 hex characters). The accepted syntax is 32–512 URL-safe ASCII
characters. Generate a separate `FF_HOOK_TOKEN`; using the same secret is rejected.
Store secrets privately in the service environment and nginx's protected include,
never in tickets, Git, logs, or client configuration. API and worker share the
validated configuration. Production startup fails on missing/invalid ingress
credentials, a development token, or a reused hook secret. Development mode
rejects configured ingress credentials rather than silently ignoring them.

The API accepts `X-FF-Ingress-Auth` only in ingress mode, using constant-time
comparison. It never treats Authorization, X-API-Key, forwarded identity, or
client IP as an alternate administrator credential in this mode. Possession of
the ingress secret grants shared administration: protect it like a bearer secret.

## Required nginx boundary

- API, tusd and hook ports must stay private on a dedicated network. Do not expose
  the API directly through the tunnel or host ports.
- Strip client-supplied `X-FF-Ingress-Auth` everywhere. Inject the configured secret
  only when forwarding a Gatekeeper-authorized admin request to the API. Never
  echo it to clients or forward it to Gatekeeper, static downloads, or tusd.
- Use an internal `auth_request` subrequest to Gatekeeper `/authz`, no request body,
  and an empty Content-Length. Gatekeeper receives the original method and fixed
  original host `ff.mazza.vc`. Send a normalized route (`/files/` or `/api/files/`)
  instead of a capability-containing URI; omit query strings and original referer.
  Forward the client credentials only to Gatekeeper, then strip Authorization and
  X-API-Key before proxying to the service. Preserve Origin for Gatekeeper policy.
- `/files/`: allow POST/PATCH/HEAD/DELETE/OPTIONS only, require Gatekeeper approval
  on every allowed method, then forward to tusd. Strip `X-FF-Expiry`. The hook
  credential authenticates tusd callbacks separately; ingress headers do not
  authenticate hooks. Do not short-circuit OPTIONS before the access phase.
- `/api/files/<32 lowercase hex ID>`: GET/HEAD stay public; DELETE requires
  Gatekeeper approval and the injected ingress secret. Reject other methods.
  If all methods use auth_request, Gatekeeper must explicitly allow public
  GET/HEAD; never reuse a public authorization result for DELETE.
- `/f/<ID>/<filename>`: GET/HEAD stay public capability access through the existing
  static file design. No ingress secret, credential forwarding, directory listing,
  or cache. The backend does not introduce a download authorization endpoint.
- `/internal/*`, `/health`, `/ready`, Gatekeeper subrequest locations and all other
  paths stay inaccessible publicly. `/internal/auth` is a private verifier, not
  a Gatekeeper client; calling it before Gatekeeper is not a substitute for authz.
- Reject X-HTTP-Method-Override, X-Method-Override and X-HTTP-Method. The API also
  rejects these headers before handling any request.
- Follow the ByteForge gatekeeper-nginx-setup skill for auth_request phase ordering
  and variable proxy_pass with a Docker resolver. Use Apollo's verified private
  tailnet Gatekeeper endpoint. For the Cloudflare Tunnel use the fixed external
  HTTPS scheme and host; no origin HTTP-to-HTTPS redirect.
- Keep capability paths and credentials out of both nginx and Gatekeeper logs.
  Error logs can embed request lines too. Disable caching for capability routes.

## Acceptance by the infrastructure owners

Exercise valid/invalid client grants for every protected method through nginx;
spoofed ingress and identity headers without a valid grant must fail before tusd
or the API is reached. Verify credential stripping, public GET/HEAD and Range,
DELETE across uploads, https Location headers, hook authentication isolation,
Gatekeeper unavailability failing closed, and inaccessible internal routes.
Inspect logs for a test capability ID and credentials without publishing either.
The local backend tests verify its trust boundary, not the live Gatekeeper policy
or Apollo's eventual nginx configuration.
