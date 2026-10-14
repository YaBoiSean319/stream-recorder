# Connect a YouTube channel

An Administrator manages one shared channel in **Settings → YouTube channel**. Enable built-in authentication and sign in as an Administrator if other people can reach the recorder. Google consent is interactive and cannot be completed with an API key alone.

1. In [Google Cloud Console](https://console.cloud.google.com/apis/credentials), enable **YouTube Data API v3** and configure an OAuth consent screen. Request the `youtube.upload` and `youtube.readonly` scopes; complete Google's app verification process if your project requires it.
2. Create an OAuth **Web application** client. In Settings, enter the public HTTPS app origin (for example `https://record.example.org`) and copy the exact redirect URI shown. Add that URI to the client's **Authorized redirect URIs** in Google Cloud. Local loopback HTTP is supported for development only.
3. Enter the client ID and client secret, save, then select **Connect channel**. Approve access in Google. If you own more than one channel or Brand Account, choose the intended YouTube identity during Google's authorization flow. Confirm the channel name and ID shown after the redirect. If Google chose another channel, reconnect and select the correct account/channel during consent.
4. If Settings says reconnect required, select **Reconnect channel** and consent again. Disconnect removes the saved access and refresh tokens. Google may also need authorization revoked in [your Google account](https://myaccount.google.com/permissions) if you want to withdraw previously issued consent.

Secrets and refresh tokens are stored in the persistent SQLite database under the application data directory with private filesystem permissions. Back up that directory securely; container replacement must preserve the mounted data volume. The client secret is never sent back to the browser. Blank secret input retains the saved value. Changing credentials or the public origin disconnects the existing channel, since its OAuth grant may no longer apply.

## Administrator API

All routes below require `youtube:manage` and use the ordinary API key or Administrator browser session rules. Mutating browser requests require the normal CSRF token. The callback additionally requires the original browser binding, a one-use state, and an Administrator session when built-in authentication is enabled. `POST /connect` requires a browser, since the returned `authorization_url` must be visited interactively.

| Route | Purpose |
| --- | --- |
| `GET /api/v1/youtube/config` | Masked client configuration, callback URL, and connection status |
| `PUT /api/v1/youtube/config` | Update `client_id`, `public_url`, or a nonempty `client_secret`; omitted/blank secret is retained |
| `GET /api/v1/youtube/status` | Selected channel and reconnect state, with no token readback |
| `POST /api/v1/youtube/connect` | Start interactive consent; returns `authorization_url` |
| `GET /api/v1/youtube/callback` | Browser-bound Google redirect; consumes state once |
| `POST /api/v1/youtube/refresh` | Check/renew offline authorization; revoked grants produce 409 and reconnect required |
| `DELETE /api/v1/youtube/connection` | Disconnect shared authorization |

Use `https://www.youtube.com/channel/<channel ID>` to inspect the selected channel. Upload settings and upload operations are handled separately; a connection alone does not publish a video.

## Upload a completed recording

Choose **Upload to YouTube** beside a ready recording. The connected channel is shown before submission. A title is filled from the current recording title, while description, tags, category (default `22`), visibility (default **Unlisted**), subscriber notifications (off), and audience are editable. You must explicitly choose whether the video is made for kids. Metadata is fixed when the job is queued; renaming the local recording later leaves it untouched.

Only one upload transfers at a time. The recording displays the upload state, bytes sent, attempts, errors, and a copyable `youtube.com/watch?v=...` link when YouTube acknowledges creation. Processing and the video's actual visibility may change on YouTube; verify them in YouTube Studio. Some unverified Google API projects have visibility restrictions. The local MP4 remains available.

Uploads persist through application restarts. An interrupted session is probed before sending bytes again. Transient errors retry up to five times with delays; **Retry** restarts probing of the same session. **Cancel upload** stops future chunks without deleting a video already created. A queued or active job and an unreleased failed or uncertain job protect local media from cap cleanup and deliberate deletion, independently of pinning. **Release protection** on a stopped job permits cleanup and deletion. If YouTube's final outcome cannot be verified because a session expired, the job becomes **uncertain**: inspect your channel before choosing **Upload again**. That explicit action may create a second video. Disconnect pauses work; reconnecting the same channel allows retry, while a different channel requires an explicit new job.

### Upload API

`GET /api/v1/uploads` lists jobs newest first; `GET /api/v1/uploads/{job_id}` gets a job. `GET /api/v1/recordings/{recording_id}/uploads` filters the list. `POST /api/v1/recordings/{recording_id}/uploads` creates a job, accepting `title`, `description`, `privacy_status` (`unlisted`, `private`, `public`), `made_for_kids` (required Boolean), `notify_subscribers`, `tags` (array), and `category_id` (numeric string). Send `Idempotency-Key` for caller-scoped replay protection. `POST /api/v1/uploads/{job_id}/retry`, `/cancel`, `/release`, and `/requeue` control the job. Uncertain requeue requires `{"reconciled":true}` after checking the channel. Browser requests use CSRF; API keys require `uploads:read`, `uploads:create`, `uploads:retry`, or `uploads:cancel` as appropriate. The API never returns OAuth tokens or resumable session URLs.
