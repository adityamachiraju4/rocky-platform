# M4 Real Device QA

This runbook prepares Rocky for manual testing on phones and tablets. It does
not certify compatibility. Record a result only after it has been observed on
the named physical device and browser.

## Chosen Access Model

Use a trusted local HTTPS certificate and expose only Vite to the private LAN.
Vite serves Rocky and proxies same-origin `/api` requests to the API on the
Mac's loopback interface.

This keeps the API off the LAN, avoids CORS and cookie changes, preserves the
existing bearer-token refresh flow, and does not expose Rocky through a public
tunnel. Device mode is opt-in and refuses to start without certificate paths.

## One-time Certificate Setup

Install `mkcert` and create a local certificate. Replace `en0` if the active
network interface is different.

```bash
brew install mkcert
mkcert -install

mkdir -p "$HOME/.config/rocky/certs"
LAN_IP="$(ipconfig getifaddr en0)"
LOCAL_HOST="$(scutil --get LocalHostName).local"

mkcert \
  -cert-file "$HOME/.config/rocky/certs/device.pem" \
  -key-file "$HOME/.config/rocky/certs/device-key.pem" \
  "$LAN_IP" "$LOCAL_HOST" localhost 127.0.0.1 ::1
```

Keep `device-key.pem` and the mkcert root private key on the Mac. Never copy
either private key to a device or the repository.

Install only the mkcert root certificate on each QA device:

```bash
mkcert -CAROOT
```

The file to transfer is `rootCA.pem`, not `rootCA-key.pem`.

- iPhone/iPad: install the certificate profile, then enable full trust under
  Settings > General > About > Certificate Trust Settings.
- Android: install `rootCA.pem` as a CA certificate using the device security
  settings. Menu names vary by Android version.
- Remove the QA CA from each device when M4 testing is complete.

Keep the Mac and devices on the same trusted private network. Do not use guest
Wi-Fi with client isolation. Permit incoming Node connections in the macOS
firewall only for this private network.

## Start Rocky

Terminal 1, API reachable only through Vite:

```bash
cd /Users/adityamachiraju/rocky-platform/apps/api
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Terminal 2, production PWA preview over HTTPS:

```bash
cd /Users/adityamachiraju/rocky-platform/apps/web
export ROCKY_DEV_HTTPS_CERT="$HOME/.config/rocky/certs/device.pem"
export ROCKY_DEV_HTTPS_KEY="$HOME/.config/rocky/certs/device-key.pem"
npm run preview:device
```

For HMR while debugging UI code, use `npm run dev:device` instead. Use
`preview:device` for installation, service-worker, and update testing because
it serves the production build.

Find the current LAN address and open the printed HTTPS URL on each device:

```bash
ipconfig getifaddr en0
```

The default URLs are `https://<LAN_IP>:4173` for preview and
`https://<LAN_IP>:5173` for development. Regenerate the certificate if the LAN
address changes. Do not hardcode the address in source or environment files.

Verify the same-origin proxy before device testing:

```bash
LAN_IP="$(ipconfig getifaddr en0)"
curl --fail --show-error "https://$LAN_IP:4173/api/health"
curl --fail --show-error --head "https://$LAN_IP:4173/"
```

In the device browser, confirm the page has no certificate warning. A warning
means the secure context is not valid and microphone/PWA results are invalid.

## Architecture Checks

- Frontend API paths remain relative to `/api`.
- Vite forwards `/api` to `http://localhost:8000` on the Mac.
- The browser never connects directly to port 8000.
- No CORS middleware or credentialed cross-origin requests are required.
- Auth uses an in-memory access token plus a refresh token in local storage;
  it does not use cookies. Each device/origin must log in independently.
- `/conversation`, `/transcribe`, and `/speech` use the same authenticated
  fetch path as the other private API routes.
- `getUserMedia` requires the trusted HTTPS origin. `MediaRecorder` support and
  actual MIME selection must be recorded per physical browser.
- The manifest uses `/` for `start_url` and scope, standalone display, and the
  Rocky 192/512 icons.
- The page uses `viewport-fit=cover` and safe-area CSS.
- The service worker precaches static assets only. `/api` is denied from the
  navigation fallback and has no runtime cache policy.

## Result Values

Use `Pass`, `Fail`, `Blocked`, or `Not tested`. For a failure, record device
model, OS/browser version, installed-versus-browser mode, exact step, visible
error, and whether retrying changed the result. Do not record passwords,
tokens, full personal context, or audio.

## iPhone Safari Checklist

- [ ] Login and session refresh/reload
- [ ] Mission Control loads
- [ ] Typed English response
- [ ] Typed Hindi response
- [ ] Typed Telugu response
- [ ] Typed Tamil response
- [ ] Microphone permission prompt and grant
- [ ] English STT
- [ ] Hindi STT
- [ ] Telugu STT
- [ ] Tamil STT
- [ ] Silence auto-stop
- [ ] Second-tap manual stop
- [ ] Background during recording stops safely
- [ ] Foreground recovery permits a new turn
- [ ] English Kokoro playback is audible
- [ ] Hindi browser/device speech fallback is audible
- [ ] Telugu browser/device speech fallback is audible
- [ ] Tamil browser/device speech fallback is audible
- [ ] Replay
- [ ] Spoken Replies off and on
- [ ] Software keyboard does not cover the active composer
- [ ] Composer resizes for multiline input
- [ ] Portrait-to-landscape rotation
- [ ] Notch/home-indicator safe areas
- [ ] Bottom navigation
- [ ] More menu
- [ ] Projects
- [ ] Tasks
- [ ] Notes and editor keyboard behavior
- [ ] Lists and editor keyboard behavior
- [ ] Reminders and date/time input
- [ ] Notifications
- [ ] Settings

## Installed iPhone PWA Checklist

- [ ] Add to Home Screen and standalone launch
- [ ] Login survives app relaunch through refresh
- [ ] Microphone permission and recording
- [ ] English STT full turn
- [ ] Non-English STT full turn
- [ ] English backend audio playback
- [ ] Non-English browser/device speech fallback
- [ ] Replay and Spoken Replies toggle
- [ ] Background recording cleanup and foreground recovery
- [ ] Safe areas in portrait and landscape
- [ ] Bottom navigation and More menu
- [ ] Update prompt appears after a new build
- [ ] Update waits while voice is active
- [ ] Relaunch after update loads the new build

## Android Chrome and Installed PWA Checklist

- [ ] Chrome login, reload, and Mission Control
- [ ] Install prompt or Add to Home Screen
- [ ] Standalone launch and relaunch
- [ ] Microphone permission and recording
- [ ] English, Hindi, Telugu, and Tamil STT turns
- [ ] Silence auto-stop and second-tap manual stop
- [ ] Background cleanup and foreground recovery
- [ ] English backend audio playback
- [ ] Non-English browser/device speech fallback
- [ ] Replay and Spoken Replies toggle
- [ ] Software keyboard and multiline composer
- [ ] Portrait/landscape navigation and safe areas
- [ ] Projects, Tasks, Notes, Lists, Reminders, Notifications, Settings
- [ ] Service-worker update prompt and post-update relaunch

## iPad Safari Checklist

- [ ] Login and Mission Control
- [ ] Tablet rail at portrait and landscape widths
- [ ] Typed English and multilingual responses
- [ ] Microphone, STT, conversation, and audible speech
- [ ] Background cleanup and foreground recovery
- [ ] Hardware and software keyboard behavior
- [ ] Rotation and safe areas
- [ ] Project, task, note, list, and reminder layouts

## Brave and Firefox Desktop Sanity

- [ ] Login and navigation
- [ ] Recorder permission and recording
- [ ] Local STT transcript
- [ ] Conversation response
- [ ] English backend TTS
- [ ] Non-English browser speech fallback
- [ ] Replay and Spoken Replies toggle

## Multilingual Device Matrix

Record one row per language and device/browser combination. Latency is from
speech end to audible response start when voice input is used.

| Language | Device/browser | Typed response | Voice transcript | Response language | Speech provider/fallback | Audible output | Latency | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| English |  |  |  |  |  |  |  |  |
| Hindi |  |  |  |  |  |  |  |  |
| Telugu |  |  |  |  |  |  |  |  |
| Tamil |  |  |  |  |  |  |  |  |
| Spanish |  |  |  |  |  |  |  |  |

## Known Limitations

- General assistant provider latency can be several seconds.
- Multilingual voice issues are tracked in
  `docs/MULTILINGUAL_VOICE_HARDENING.md`.
- Automatic STT language detection has a deterministic backend stabilizer, but
  real-device multilingual behavior still requires retesting.
- Non-English automatic browser/device speech can be silent; Replay often
  works.
- Tamil and Telugu local Kokoro speech is unavailable.
- Browser fallback routing exists but still needs broad iPhone/iPad proof.
- Live weather and time work without provider keys. News, markets, web search,
  and places require server-side provider credentials.
