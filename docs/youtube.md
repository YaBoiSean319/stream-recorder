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
