# Optional authentication

Authentication is off on new installations. An external access boundary can remain in place. Administrators can create a local username and password (at least 12 characters) in Settings, then enable built-in authentication. Local sign-in remains available with SSO enabled.

Operators can preview, capture, stop, download and pin/unpin. Only administrators read/change shared settings, manage access and keys, or deliberately delete recordings. APIs return 401/403 without redirecting to a login page. Downloads and preview resources require the same session or API key as JSON APIs. API keys do not override busy admission or active-media protection.

Use HTTPS for production. Set the HTTPS public origin in Authentication settings when TLS terminates at a reverse proxy: this makes browser cookies Secure and establishes the allowed request Origin. The application does not trust arbitrary forwarded headers. Restrict direct backend access to your trusted deployment network. Browser sessions expire after eight hours and do not survive application restart; credentials and keys do. Browser state-changing requests require `X-CSRF-Token` from `/api/auth/status` or local login. Cookies are HttpOnly and SameSite=Lax.

API keys default to operator. Copy the key once at creation and keep it in your automation credential store. Use `Authorization: Bearer YOUR_API_KEY` on requests, for example:

```sh
curl -H 'Authorization: Bearer YOUR_API_KEY' https://recorder.example/api/status
curl -X POST -H 'Authorization: Bearer YOUR_API_KEY' -H 'Content-Type: application/json' -d '{"duration_seconds":3600}' https://recorder.example/api/recordings
curl -X POST -H 'Authorization: Bearer YOUR_API_KEY' https://recorder.example/api/recordings/RECORDING_ID/stop
```

Revoking a key immediately denies that key without affecting other keys or captures. Listing keys returns metadata, never reusable material. Only SHA-256 verifiers of high-entropy random keys are persisted. Local passwords use salted scrypt verifiers. Source and OIDC client secrets necessarily remain recoverable in the persistent database; protect the data volume and backups.

## OIDC

Register a confidential authorization-code client in Authentik or Microsoft Entra. Set the exact redirect URI to `https://recorder.example/api/auth/oidc/callback`. Configure the exact issuer advertised in provider discovery, including any trailing slash (Authentik commonly uses `https://identity.example/application/o/recorder/`; Entra uses a tenant-specific v2 issuer). Enter client ID, secret, public application URL, group claim and explicit operator/admin groups in Settings.

The provider must emit the selected group claim as an array in the signed ID token. Authentik group property mappings can provide names; Entra group claims commonly provide group object IDs, which must be mapped exactly. Group overage that omits the array is denied; configure an appropriately scoped application group claim. Unmapped users are denied; admin wins if both roles match. RS256 and ES256 signing are supported through maintained Authlib/joserfc validation. State, nonce, issuer, audience, expiry, browser binding and PKCE protect the flow. Issuer/public URL must use HTTPS, with loopback-only HTTP allowed for development.

Saved provider secret is masked, blank retains it, and `remove_client_secret: true` explicitly removes it via the configuration API. SSO configuration does not automatically enable application authentication. Provider outage or a bad mapping does not disable local administrator login.

## Local recovery

Keep the local administrator password in a password manager. If it is lost, stop the container, back up the persistent data volume, and use the recovery command below against that volume. It resets only the local administrator password and invalidates browser sessions through restart; other settings, keys, records and media remain intact.

```sh
docker compose stop recorder
docker compose run --rm recorder python -m recorder.recover /data
docker compose up -d
```

The command prompts interactively without echoing the password. Use the actual Compose service name and persistent data mount in your deployment. Never delete the database to recover access.
