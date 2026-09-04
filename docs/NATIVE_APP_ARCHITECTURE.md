# Rocky Native App Architecture

M7 adds a Capacitor shell around the existing React/Vite frontend. The same
frontend continues to serve desktop web, mobile web, PWA, iOS, and Android.

## Identifiers

- App display name: `Rocky`
- iOS bundle identifier: `com.phredsec.rocky`
- Android application ID: `com.phredsec.rocky`
- Capacitor web directory: `apps/web/dist`

No provider API keys, database credentials, JWT secrets, or private
certificates belong in the native bundle.

## Commands

From `apps/web`:

```bash
npm run build
npm run build:native
npm run build:native:release
npm run native:sync
npm run native:sync:android
npm run native:sync:android:release
npm run ios
npm run android
```

`npm run build` is the web/PWA production build and defaults to Rocky's
deployed Railway API when `VITE_API_BASE_URL` is unset. Local `npm run dev`
keeps the `/api` proxy default.
`npm run build:native`, `npm run native:sync`, and
`npm run native:sync:android` require `VITE_API_BASE_URL` and refuse to build a
native bundle that would send backend calls to Capacitor's local
`https://localhost` WebView origin.
`npm run build:native:release` and `npm run native:sync:android:release`
default to Rocky's deployed Railway API and require HTTPS if an override is
provided.

`npm run ios` and `npm run android` open the native IDEs after a native web
build and Capacitor sync.

## Backend Configuration

The frontend resolves API requests through `VITE_API_BASE_URL`.

- Web/PWA development default: `/api`, proxied by Vite to local FastAPI.
- Web/PWA production default:
  `https://rocky-platform-production.up.railway.app`.
- Native local Android emulator testing: run FastAPI on the host and set
  `VITE_API_BASE_URL` to an explicit backend origin reachable from the emulator.
  The Android emulator reaches the host machine at `10.0.2.2`; for example:

  ```bash
  cd /Users/adityamachiraju/rocky-platform/apps/api
  source .venv/bin/activate
  uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    --ssl-certfile "$ROCKY_DEV_HTTPS_CERT" \
    --ssl-keyfile "$ROCKY_DEV_HTTPS_KEY"

  cd /Users/adityamachiraju/rocky-platform/apps/web
  VITE_API_BASE_URL="https://10.0.2.2:8000" npm run native:sync:android
  ```

  The development certificate must be valid for the host name or address used
  in `VITE_API_BASE_URL`. For mkcert, install the mkcert root CA into the
  Android emulator's user certificate store, not the leaf certificate served by
  FastAPI:

  ```bash
  mkcert -CAROOT
  adb push "$(mkcert -CAROOT)/rootCA.pem" /sdcard/Download/rocky-mkcert-rootCA.pem
  ```

  Then install `rocky-mkcert-rootCA.pem` in the emulator Settings app as a CA
  certificate. Android debug builds include
  `apps/web/android/app/src/debug/res/xml/network_security_config.xml`, which
  trusts user-installed CAs only in debug builds. Release builds do not include
  that overlay and use Android system trust only.
- Production native builds: run `npm run native:sync:android:release`, which
  defaults to `https://rocky-platform-production.up.railway.app`. If
  `VITE_API_BASE_URL` is set for a native release build, it must be exactly that
  HTTPS origin.

Do not hardcode a Mac LAN IP into production config. Do not globally disable
ATS or Android cleartext protections. If local cleartext is ever required, add a
narrow development-only exception and remove it before release.

## Native Plugins

- `@capacitor/app`: foreground/background/resume lifecycle cleanup.
- `@capacitor/device`: native login device metadata.
- `@capacitor/network`: online/offline state.
- `@capacitor/preferences`: native storage bridge for durable refresh tokens.
- `@capacitor/push-notifications`: permission and token foundation.
- `@capacitor/status-bar`: native status bar styling.
- `@capacitor/splash-screen`: launch experience.
- `@capacitor/keyboard`: resize behavior for the composer.

Microphone capture remains on the existing `MediaRecorder/getUserMedia` path.
iOS and Android native microphone permissions are declared and the user is only
prompted when they start voice input.

## Auth Storage

Access tokens remain memory-scoped. Refresh tokens use the existing
browser-compatible storage path on web/PWA and Capacitor Preferences in native
builds. Capacitor Preferences improves native separation but is not a Keychain /
Keystore secure-storage plugin. Before production release, replace the native
backend of the `tokenStore` abstraction with an audited OS-backed secure storage
plugin or a first-party secure token strategy.

The server refresh rotation and logout/session invalidation model is unchanged.


## Lifecycle, Voice, and Offline

Capacitor app background/pause events dispatch into the existing Mission Control
voice cleanup path. Backgrounding while listening, transcribing, thinking, or
speaking cancels active media, aborts duplicate turns, stops playback, and
returns the UI to idle.

Network state is surfaced before backend-dependent conversation requests. Rocky
does not silently queue private mutations while offline.

## Push Foundation

The Push Notifications plugin is installed and native projects are ready for
permission/token handling. Full delivery still requires backend device-token
registration, APNs/Firebase setup, and product decisions for notification deep
links such as reminders, tasks, and Mission Control.
