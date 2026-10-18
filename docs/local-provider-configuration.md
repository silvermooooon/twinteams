# Local OpenAI credential saving and optional validation

This page is a local-development administrative tool, not a production credential
management service. It is available only through the explicit loopback
`backend/run_local.py` or `backend/run_credentials.py` launcher and the AAA administrator account. The other local
test identities cannot access its endpoints. Production and the isolated offline
acceptance fixture do not expose this feature. Use the backend-served same-origin page; the Vite development proxy is intentionally unsupported for credential submission.

## Standalone save page and explicit saved-key validation

From `backend`, run `.venv/bin/python run_credentials.py --port 8012`, then visit
`http://127.0.0.1:8012/api/local-provider-config/save-page`. Sign in as `DEV-AAA`
on the normal `http://127.0.0.1:8010` application first. The existing host-only
development cookie works across the loopback ports. This independent runner
includes only the guarded credential router. It does not import the application
entrypoint, initialize or migrate schema, start Runtime, or replay any backlog.

Personally enter the key, check the initially unchecked confirmation, and click
the save button. This calls only the same-origin `/api/local-provider-config/save`
endpoint. It makes no OpenAI request and does not depend on successful validation.
The page discloses the exact absolute path, plaintext storage with 0700 directory
and 0600 file permissions, replacement of an existing file, and future use only
through a separately requested process.

After a private file is safely saved, the same standalone page enables
**使用已保存 Key 验证 GPT-6 Luna（会产生少量 API 费用）**. Only the human's click
submits `POST /api/local-provider-config/verify-saved` with exactly
`{"confirm_billed_call": true}`. This separately authorizes one potentially billed
fixed-prompt request; loading the page, status checks and startup never read the
stored key or call OpenAI. The password field stays empty during this operation.
The page discloses the fixed prompt and warns that no application chat is sent,
timeouts or connection failures may still incur a charge, and automatic retry,
model enabling and backlog replay do not occur. The network may be unavailable.

This endpoint has the same AAA identity, direct loopback, exact same-origin and
custom-header checks as the other credential endpoints. It shares their attempt
gate. Only after consent and admission does the server open the private file
using directory file descriptors and `O_NOFOLLOW`, validate owner, 0600 mode,
single hard link and bounded size using `fstat`, and read exactly one safe
`OPENAI_API_KEY=...` assignment. Unsafe files or malformed contents are rejected
without a provider call or credential disclosure. Validation never rewrites the
file or enables the application. It uses the fixed official request and safe
timeout, connection, authentication and model-unavailable messages below.

The authoritative saved file is `backend/data/provider-secrets/openai.env`, with
one `OPENAI_API_KEY=...` assignment. Keys must match the expected official `sk-`
alphabet of ASCII letters, digits, hyphens and underscores. Whitespace, CR/LF,
control characters, shell operators, quotes, expansions and extra fields are
rejected before saving. The key is not set in the server's current environment,
global shell configuration, browser storage or an application Runtime.
Neither launcher automatically loads this file. A legacy `openai.json` is never
read or migrated. The optional validated-save flow uses the same authoritative
environment file.

## Optional user-controlled validation flow

1. Sign in as `DEV-AAA` on the normal application.
2. Open **OpenAI configuration** from the account menu.
3. Personally enter the existing key in the password field. Do not send it in chat.
4. Optionally select **Save for later use**. The page shows the exact local path,
   `backend/data/provider-secrets/openai.env`, and the administrative scope.
5. Personally click the explicitly billed validation button. With saving selected,
   the button states that it validates and saves; otherwise it only validates.

Validation makes one bounded request to the official
`https://api.openai.com/v1/responses` endpoint using `gpt-6-luna`, the fixed prompt
`Reply with OK.`, `store: false`, no tools and at most 32 output tokens. No application
chat, history, Tracking, memory or workspace content is sent. Redirects, custom base
URLs and environment proxies are not used. There are no automatic retries.
The entire provider exchange is limited to 90 seconds, with a 10-second
connection timeout. A timeout or lost connection does not establish whether the
provider processed or billed the call. Check the OpenAI account before explicitly
authorizing another attempt. A failed or unconfirmed validation never saves the
submitted key or enables application generation.

The page uses separate fixed messages for a provider timeout, a failed backend
connection to OpenAI, a lost browser connection to the local service, a rejected
fixed request, and an unexpected/incomplete provider response. No raw provider
error body or exception details are displayed. These categories describe what the
application observed; they do not diagnose an account or network beyond that.

The model identifier and supported reasoning setting were checked against the
[official GPT-6 Luna documentation](https://developers.openai.com/api/docs/models/gpt-6-luna).
`store: false` disables response storage requested through the Responses API; it
is not a claim that provider abuse-monitoring retention or contractual data
policies are disabled.

## Secret handling and saving

- The browser does not use localStorage/sessionStorage or expose a show-key action.
  It clears the input when submitting and when leaving the page.
- No endpoint reads the key back to the browser, even partially. Status reports
  only whether a local saved credential exists.
- Provider error bodies and exception details are not returned or logged by this
  route. The page displays fixed safe success/error messages.
- Without the unchecked save option, the submitted key is used only for that
  validation request and is not written to disk.
- The standalone save endpoint saves only after its explicit save confirmation.
  Optional saving on the validation page additionally requires validation success.
  Both use the displayed private path and owner-only permissions, and reject
  unsafe symlink/path conditions.
- The file is under ignored `backend/data/` and excluded from source-delivery
  archives. It is a local plaintext credential protected by OS permissions, not
  an encrypted vault, and should not be copied or included in backups casually.
- Saving **does not enable application generation**, configure the Runtime,
  replay pending requests, or authorize later API calls. A future use requires a
  separate explicit user request and an appropriately scoped execution flow.

## Acceptance boundary

Implementation checks use placeholder values, a fake HTTP transport and a temporary
credential directory. They do not call the real provider, save an actual key, or
inspect entered credentials. The final live validation and optional persistent
save are performed by the user personally after control is handed over. The
assistant must not inspect the field, page DOM, screenshots or network payloads
once credential entry begins.

Nine isolated fake-transport acceptance groups cover authority, same-origin,
fixed-payload, redaction, timeout/error mapping, rate/concurrency, explicit-save
and unsafe-file checks. The standalone save-only group checks strict consent,
environment-file injection rejection, a single assignment, 0700/0600 permissions,
and that saving invokes neither validation nor an HTTP client. They also check the 90-second overall and 10-second
connection bounds, distinct timeout/connection/rejected/invalid-response codes,
and one attempted request per failure. No live provider validation is performed
by these checks.

An additional saved-key group uses only an isolated fake credential file and mock
HTTP. It checks consent and authority before reads, metadata-only GET/page access,
the shared attempt gate, safe file modes/symlinks/hardlinks and strict bounded
single-assignment parsing, one fixed request, distinct safe error categories,
redaction and no rewriting, Runtime enabling or backlog changes.

## Credential-free connectivity check

Run `backend/checks/provider_connectivity_probe.py` using the backend virtual
environment in the same desktop/server network namespace as the local backend.
It makes one unauthenticated `GET https://api.openai.com/v1/models` with
`trust_env=False`, no Authorization header, no redirect and no retry. It reads
response headers only and prints only the HTTP status, elapsed seconds and
exception/cause class names. It does not read a key, credential file, form,
response body or environment output. A `401` is useful evidence that the official
API is reachable without establishing credential validity. Results from a
different shell or network namespace do not establish desktop-backend reachability.
