# File delivery and preview

Ask your Twin to deliver a finished file in the conversation. A delivered file
appears as an ordinary attachment card and keeps its original download link.
Preview bytes load only when the user opens the file.

| File | Action |
| --- | --- |
| Markdown | Preview icon opens rendered Markdown in the application. Embedded HTML remains text. |
| UTF-8 text | Preview icon opens escaped text. |
| PDF | Preview icon opens the browser's PDF viewer inside the application. |
| HTML | Open icon launches a separate tab with interactive, self-contained HTML and a Download link. |
| Supported images | Existing image preview. |
| Other files | Download link. |

Markdown, text and HTML previews are limited to 1 MiB. PDFs retain the 20 MiB
attachment limit. Failed or oversized previews show an English error and keep
the original download available. Closing a document preview restores focus to
its preview action and releases temporary PDF blob URLs.

The HTML tab runs inline scripts inside an opaque-origin sandbox. It has no
opener, platform DOM, cookies or local/session storage access. Its CSP blocks
fetch connections and external assets; this is a viewer for self-contained
interactive files, not hosting for arbitrary network-dependent websites.
The untrusted file is assigned only to the sandboxed iframe's `srcdoc`.

Publication captures a snapshot. Editing the workspace source later does not
change the previously delivered attachment. See the [file contract](implementation-contract.md#uploaded-files-and-the-real-workspace)
for authorization, persistence and API details.

## Sharing files with another Duet

Conversation consent lets Duets communicate. Sharing files asks the sharing
Duet's owner for a separate approval in the existing approval inbox. The request
names the files and recipient Duets; pending or rejected requests publish no
file content. The Duet can continue independent work while approval is pending.
Approval resumes the original action, so the owner does not need to resend it.

Approval is reused for the same owner's file at the same path, with identical
content and the same approved recipients. Changed file contents or a new
recipient require new approval. Files uploaded privately to a Duet follow the
same rule. Delivery captures an immutable snapshot; editing the source does not
change earlier downloads.

The human conversation's **Shared files** sidebar includes the files shared by
its participants' Duets, with the sharing Duet's name, preview and download.
Each viewer sees only files shared with them; adding a third person to a human
conversation does not grant that person access. Existing conversation workspace
files and direct upload remain available. Recipient Duets can discover and read
the approved snapshots through the existing workspace tools.

## Verification

The dedicated `file-delivery-preview` browser scenario uses the actual SDK
write and `present_files` tools, authenticated product APIs, canonical messages
and real uploads. Its provider decisions are deterministic offline fixtures;
it does not claim live-model verification or intercept business responses.
The cases cover previews and downloads, interactive HTML, enforced CSP/storage
boundaries, immutable delivered bytes after a source edit and reload, foreign
owner access denial, oversized preview errors, ordinary files and images.

On 2026-10-24, both cases passed in Chrome 155 using the deterministic offline
provider, actual SDK tools and product APIs, without business API or WebSocket
interception. The report and screenshots are under
`/tmp/twinteams-file-delivery-preview-offline-20261024-r2/` (`report.json`:
2 passed, 0 failed). Visual review confirmed rendered Markdown with literal
script text, the separate HTML tab with its counter at `Count: 1`, and readable
preview errors with Download retained. The HTML probe recorded opaque origin,
denied cookie/storage/parent access and Chrome's actual `connect-src 'none'`
denial for a credentialed platform fetch; another owner received HTTP 403.

The first run's `file-preview-pdf.png` in
`/tmp/twinteams-file-delivery-preview-offline-20261024/` visibly shows the PDF
page and its `PDF_delivery-42bc8cee-1-1` content. The second run's PDF screenshot
caught Chrome's native viewer before page paint; its authorized PDF bytes,
download and blob-URL cleanup checks passed. The initial run otherwise stopped
at a CSP DOM-event assumption, corrected using Chrome's enforced console denial
in the second run. No live-model result is claimed by these checks.

A separate isolated live check on the same date used only `gpt-6-luna` through
Responses: 6 of a hard maximum 16 HTTP attempts, including one connectivity
check, all returned 200. One natural request, without naming a tool, produced
and delivered a 118-byte Markdown decision note and a 1,279-byte self-contained
HTML counter page. Actual successful `write` and `present_files` receipts,
the canonical two-attachment message, authenticated download hashes and matching
text-only preview bytes are retained under
`/tmp/twinteams-file-delivery-live-20261024/`. An initial `present_files` call
failed and the model recovered before successful delivery; its original error
body was not retained, so no failure cause is inferred. This is one private
workflow, not broad model-behavior coverage or a live browser-interaction check.
Both disposable stacks removed their schema, Redis keys, temporary workspace
and owned processes; no production message or Memory was changed.

Run from `backend/`:

```sh
TWINTEAMS_BROWSER_CHANNEL=chrome .venv/bin/python checks/browser_e2e.py \
  --scenario file-delivery-preview \
  --node /usr/local/n/versions/node/22.14.0/bin/node \
  --artifact-dir /tmp/twinteams-file-delivery-preview-offline
```
