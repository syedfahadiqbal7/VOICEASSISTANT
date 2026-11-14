# Secure mobile connection setup

The desktop mobile API now binds to `127.0.0.1` by default. This keeps the endpoint local to the desktop unless LAN access is deliberately enabled.

## Enable access from a phone on the LAN

1. Generate a random secret with at least 32 characters (for example, use a password manager).
2. Set these environment variables for the Windows user account that launches VoiceAssistant:
   - `VOICEASSISTANT_MOBILE_HOST=0.0.0.0`
   - `VOICEASSISTANT_MOBILE_TOKEN=<your-random-secret>`
3. Restart VoiceAssistant. The server refuses non-loopback binding if the token is missing or shorter than 32 characters.
4. In the Flutter app, enter the desktop LAN IP and the same token. The token is sent in the WebSocket handshake header and is not placed in the URL.
5. Restrict Windows Firewall access to trusted private networks and devices. Do not expose port 8765 to the public internet. For untrusted networks, put the API behind a TLS reverse proxy, forward the `X-VoiceAssistant-Token` header to the desktop upstream, and enter the proxy's `wss://host[:port]/ws` URL in the Flutter app. A VPN is another option.

All clients must authenticate before the server accepts their WebSocket session. A mobile approval challenge is sent to one authenticated client and approval responses are bound to that connection ID. The desktop approval UI remains available.

The token is a shared secret, not per-user identity or device certificate authentication. Rotate it if a device is lost or the token is disclosed.
