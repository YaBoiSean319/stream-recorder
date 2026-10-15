# Signed webhooks

In Settings, create an administrator receiver with an HTTPS URL, a signing secret of 16–256 characters, and selected events. The configuration is shared across the application. HTTP URLs are restricted to `127.0.0.1` or `::1` for local testing. Public hostnames must resolve only to public addresses. Redirect responses are rejected. The service never sends signing secrets or saved source credentials in event payloads, and does not return a saved secret through the API.

Events are `recording.completed`, `recording.failed`, `upload.completed`, and `upload.failed`. A YouTube upload with an uncertain remote result emits `upload.failed` with `state: uncertain`. The JSON body contains a stable `id`, `type`, `created_at`, and `data` object. The `data` fields include the relevant recording or upload ID, state, and a completed video's public URL when available. Event IDs remain stable across retries. Receivers should deduplicate by event ID and return a 2xx response only after accepting the event.

A request has these headers:

- `X-Stream-Recorder-Event-Id`: stable event ID
- `X-Stream-Recorder-Event`: event type
- `X-Stream-Recorder-Timestamp`: Unix seconds at delivery
- `X-Stream-Recorder-Signature`: `sha256=` followed by lowercase hex HMAC-SHA256 over the UTF-8 string `timestamp + "." + raw_request_body`, keyed by the configured signing secret

To verify, compare the signature in constant time against the **raw** request bytes before parsing JSON. Reject timestamps outside a short window (for example five minutes), and deduplicate the event ID. A retry gets a new timestamp and signature but retains the same event ID and body. Keep the signing secret in your receiver's secret store.

The outbox persists in `state.sqlite`. It attempts delivery in the background with a ten second total timeout, exponential delays, and at most five attempts. A non-2xx response, including redirect, is a failed attempt. Status and attempt counts appear in Settings and `GET /api/v1/webhooks/deliveries`; only the most recent 200 deliveries are returned. An administrator key needs `webhooks:manage` for configuration and history. Disabling or deleting a receiver cancels pending deliveries. To rotate a secret, edit the receiver; future attempts use the new secret.

## Power Automate and n8n

Set up an HTTP trigger or webhook endpoint, then configure its HTTPS URL in Settings. Store the matching signing secret in the flow credential/secret store. First verify the raw body and signature with an HMAC-SHA256 step (and verify the timestamp and event ID), then parse the JSON and branch on `type`. Your workflow should acknowledge quickly with 2xx; queue any long-running actions after acknowledgement. Power Automate and n8n deployments differ in how they expose raw request bytes: verify this capability for your chosen trigger before using it as the receiver.
