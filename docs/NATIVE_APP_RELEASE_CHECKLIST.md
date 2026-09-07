# Native App Release Checklist

## Apple

- Apple Developer account access.
- Bundle ID: `com.phredsec.rocky`.
- Xcode signing team and provisioning profiles.
- App Store Connect app record.
- Privacy policy URL.
- Support URL.
- Final app icon and splash/launch artwork.
- iPhone screenshots.
- Microphone purpose copy.
- Location purpose copy.
- Privacy manifest and App Privacy questionnaire.
- Encryption/export compliance review.
- TestFlight build upload.
- Internal/external TestFlight tester groups.
- App Review notes for backend-driven assistant behavior.

## Google

- Play Console access.
- Application ID: `com.phredsec.rocky`.
- Play app signing configuration.
- Release `.aab`.
- Store listing copy.
- Final app icon, feature graphic, and screenshots.
- Privacy policy URL.
- Data Safety form.
- Microphone permission disclosure.
- Location permission disclosure.
- Internal testing track.
- Production rollout plan.

## Backend And Provider Setup

- Production HTTPS Rocky backend URL:
  `https://api.rockyos.in`.
- Production frontend origin in API `CORS_ALLOWED_ORIGINS` once the web/PWA
  hosting domain is chosen.
- Staging HTTPS backend URL for pre-release builds.
- No provider keys in frontend or native bundles.
- Server-side optional provider keys:
  `NEWS_API_KEY`, `TAVILY_API_KEY`, `FINNHUB_API_KEY`, `GEOAPIFY_API_KEY`.
- Production sports-provider decision.
- Push backend device-token registration.
- APNs key/certificate setup.
- Firebase Cloud Messaging project and `google-services.json` for Android.

## Physical iPhone QA

- Install a development build from Xcode on a physical iPhone.
- Confirm launch icon and splash show Rocky branding.
- Sign in against a reachable HTTPS backend.
- Background and foreground on Mission Control while idle.
- Start recording, background the app, and confirm recording cancels cleanly.
- Ask a typed weather request with an explicit city.
- Ask `What's the weather today?`, grant location, and confirm a weather reply.
- Deny location, retry a local request, and confirm Rocky asks for a city/place.
- Toggle Spoken Replies and use Replay.
- Kill and relaunch the app; confirm session boot behavior.
- Turn network off and confirm backend requests show an offline state.
- Reconnect and confirm requests resume without duplicate turns.

## Physical Android QA

- Install a debug build or internal-test build on a physical Android device.
- Confirm application ID and app name.
- Sign in against a reachable HTTPS backend.
- Use hardware/system Back from More menu, detail pages, and root.
- Confirm keyboard resize keeps the Mission Control composer and Send usable.
- Start recording, background the app, and confirm recording cancels cleanly.
- Ask `restaurants near me`, grant approximate location, and confirm places flow
  reaches the backend when Geoapify is configured.
- Deny location and confirm Rocky asks for a city/place.
- Turn network off and confirm offline state.
- Reconnect and confirm no duplicate request is sent.
- Build a release `.aab` once Play signing and backend URLs are configured.
