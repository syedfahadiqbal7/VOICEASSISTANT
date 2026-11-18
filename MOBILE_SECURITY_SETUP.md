# Secure mobile connection setup

The desktop mobile API now binds to `127.0.0.1` by default. This keeps the endpoint local to the desktop unless LAN access is deliberately enabled.

## Legacy shared-token mode (before device enrollment)

Use this compatibility mode only when `history/mobile_devices.json` does not exist. Prefer the per-device enrollment workflow below.

1. Generate a random secret with at least 32 characters (for example, use a password manager).
2. Set these environment variables for the Windows user account that launches VoiceAssistant:
   - `VOICEASSISTANT_MOBILE_HOST=0.0.0.0`
   - `VOICEASSISTANT_MOBILE_TOKEN=<your-random-secret>`
3. Restart VoiceAssistant. In legacy mode, the server refuses non-loopback binding if the token is missing or shorter than 32 characters.
4. In the Flutter app, enter the desktop LAN IP and the same token. The token is sent in the WebSocket handshake header and is not placed in the URL.
5. Restrict Windows Firewall access to trusted private networks and devices. Do not expose port 8765 to the public internet. For untrusted networks, put the API behind a TLS reverse proxy, forward the `X-VoiceAssistant-Token` header to the desktop upstream, and enter the proxy's `wss://host[:port]/ws` URL in the Flutter app. A VPN is another option.

All clients must authenticate before the server accepts their WebSocket session. A mobile approval challenge is sent to one authenticated client and approval responses are bound to that connection ID. The desktop approval UI remains available.

The token is a shared secret, not per-user identity or device certificate authentication. Rotate it if a device is lost or the token is disclosed.



## Per-device roles (Phase 8.2)

Once the encrypted device registry is in use, every enrolled device has a server-controlled role. The Flutter client cannot choose or elevate its role. New devices default to `operator`.

Enroll the trusted owner device as an administrator:

```powershell
python mobile_device_cli.py add "Owner phone" --role admin
```

Enroll additional devices with the default operator role, or explicitly assign a narrower role:

```powershell
python mobile_device_cli.py add "Work tablet" --role operator
python mobile_device_cli.py add "Approval phone" --role approver
python mobile_device_cli.py add "Status display" --role viewer
python mobile_device_cli.py list
python mobile_device_cli.py set-role DEVICE_ID viewer
python mobile_device_cli.py revoke DEVICE_ID
```

Roles:

- `admin`: all mobile commands, including approval responses.
- `operator`: submit assistant requests, cancel schedules and use undo; cannot approve security gates.
- `approver`: read session/schedule state and respond to approval challenges; cannot submit assistant requests.
- `viewer`: read-only session/schedule access.

The server checks permissions for every WebSocket message. Unknown commands and roles are denied. Legacy shared-token and unauthenticated loopback clients are treated as `operator`, never as administrators or approvers. Approval challenges are sent only to an active `admin` or `approver` connection; if none is available, the desktop approval UI remains the fallback. Device role changes and revocations are rechecked on each message and approximately every 10 seconds while idle.

For safety, the CLI refuses to demote or revoke the last active admin device. Enroll a second admin before replacing the owner device. A missing role in an older encrypted registry is migrated to `operator` (least privilege) on its next registry access. An unsupported role or corrupt registry fails closed.

The CLI is intended to run locally on the trusted desktop. Do not expose device administration as a remote mobile API operation.


## Phase 4 mobile connection hardening

### Token and saved connection

- The mobile token must be at least 32 characters when the desktop API binds to a non-loopback address.
- The Flutter client stores the desktop address and token with `flutter_secure_storage` (Android Keystore-backed encrypted storage / iOS Keychain). It does not write the token to SharedPreferences or a plaintext config file.
- After a successful authenticated handshake, the client remembers the connection and reconnects on the next app launch. Reconnect attempts use bounded exponential backoff (1, 2, 4, 8, 16, then 30 seconds).
- Authentication rejection (WebSocket close code 1008) stops automatic retries so the user can correct the token. The **Forget saved connection** action disconnects the active socket, stops reconnect attempts and removes stored host/token values.

### Transport security

For remote/LAN connections, prefer `wss://` with a trusted TLS certificate. The desktop server can terminate TLS directly by setting both environment variables:

```powershell
$env:VOICEASSISTANT_TLS_CERTFILE = "C:/VoiceAssistant/certs/server.crt"
$env:VOICEASSISTANT_TLS_KEYFILE = "C:/VoiceAssistant/certs/server.key"
```

Both files must exist. The API prints `wss://` when TLS is configured. If using a reverse proxy instead, terminate TLS at the proxy and expose only the proxy endpoint to the phone.

Android permits cleartext traffic because local development/VPN deployments may use `ws://`; the app therefore asks for explicit confirmation before a non-loopback unencrypted connection and displays a persistent warning. Treat `ws://` as suitable only for a trusted private LAN or VPN: the token and conversation are not encrypted in transit. iOS requires `wss://` for non-loopback remote connections.

### Abuse and approval behavior

- Each WebSocket connection is limited to 120 messages/minute and 20 user messages/minute. Exceeding the global limit closes the connection with code 1013.
- Schedule cancellation IDs are validated as bounded strings.
- Mobile undo is available only when the desktop session reports an undo entry and requires a confirmation dialog on the phone.
- Approval responses are bound to the authenticated connection that received the challenge. A disconnected mobile client cannot approve a request from another connection; the desktop approval dialog remains a fallback when available.
- Real-device tests are still required for Android Keystore, iOS Keychain, WSS certificate trust, background/resume and reconnect behavior.


### Platform permission and backup policy

- iOS declares `NSMicrophoneUsageDescription`, `NSSpeechRecognitionUsageDescription` and `NSLocalNetworkUsageDescription` for microphone, speech recognition and trusted desktop-LAN access.
- Android requests microphone permission at runtime. Android app backup is disabled because the mobile access token is stored in Keystore-backed secure storage and must not be restored independently of its encryption key.
- Approval buttons remain visible until the desktop acknowledges the exact request ID; a missing acknowledgement times out locally and permits retry. A stale/rejected acknowledgement does not dismiss a different pending challenge.


### Microphone privacy and language

The app requests microphone permission only after the user taps the microphone button; it does not prompt during app startup. Speech recognition uses the device/system default locale instead of forcing `en_US`, so supported device languages can be recognized by the installed speech service.


### Build and release limitations

GitHub Actions validates both the Android release variant and an unsigned iOS release build. The Android Gradle release variant currently uses the debug signing key; it is suitable for CI validation, not Play Store distribution. Production Android distribution needs a protected release keystore and CI signing configuration. The iOS build uses `--no-codesign`; a signed IPA requires an Apple Developer signing identity and provisioning profile.


### Concurrent mobile connection limit

The WebSocket server accepts at most five concurrent clients by default. Set `VOICEASSISTANT_MOBILE_MAX_CONNECTIONS` to an integer from 1 through 32 to adjust the ceiling. Capacity is reserved before awaiting the WebSocket handshake, preventing simultaneous connection attempts from exceeding the configured limit. Excess connections are rejected with a retry-later close code. This process-local capacity cap is independent of the per-device authentication registry.
