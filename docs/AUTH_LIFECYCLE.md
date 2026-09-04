# Rocky authentication lifecycle

Rocky requires email verification before normal authentication. Registration at
`POST /identity/users` creates an active, unverified user and sends a
single-use verification link. Password-reset requests always return the same
outward response, whether or not an eligible account exists.

## Production configuration

- `PUBLIC_APP_URL`: origin of the authenticated Rocky web application. In
  production this is mandatory and must not point at the marketing site unless
  that site actually hosts the authenticated application.
- `EMAIL_FROM`: a sender identity already verified with the configured email
  provider, for example `Rocky OS <no-reply@example.com>`.
- `RESEND_API_KEY`: Resend API credential.
- `AUTH_ACTION_TOKEN_PEPPER`: an independent high-entropy server secret used
  for HMAC-SHA256 token hashing.
- `EMAIL_VERIFICATION_TTL_HOURS`: verification lifetime; defaults to 24.
- `PASSWORD_RESET_TTL_MINUTES`: reset lifetime; defaults to 60.
- `EMAIL_VERIFICATION_COOLDOWN_SECONDS`: resend cooldown; defaults to 60.

Provider credentials and sender verification must be completed before enabling
public registration. Tests replace the provider through FastAPI dependency
injection and never call Resend.

## Email links and native clients

Verification links open `${PUBLIC_APP_URL}/verify-email?token=...`; password
reset links open `${PUBLIC_APP_URL}/reset-password?token=...`. This works for
browser and PWA users and remains the v1 fallback for Android users.

A later native enhancement can register verified Android App Links and route
these URLs into Capacitor. The web routes must remain available as a fallback;
the backend does not accept caller-controlled callback URLs.
