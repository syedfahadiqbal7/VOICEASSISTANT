# External rendezvous relay QR protocol (v1)

## Purpose and boundary

This protocol lets an already-enrolled mobile device and its desktop establish
an authenticated rendezvous through a separately hosted WSS relay when they are
on different networks. The relay is a signaling forwarder only. It is not the
desktop command API, authorization engine, TURN server, or media path.

The current implementation includes desktop-side QR grant generation, an
authenticated outbound desktop WSS client, and a Flutter mobile QR verifier
and outbound WSS client with Ed25519 challenge proof. WebRTC and TURN remain
separate implementation gates. Two authenticated relay sockets alone do not
establish a usable remote-control connection.

## QR payload

The desktop creates a payload with:

- `type`: `voiceassistant_relay_session`
- `version`: `1`
- `relay_url`: the WSS relay origin
- `grant`: the exact signed grant object below, including the same `relay_url`
- `grant_signature`: unpadded base64url Ed25519 signature over canonical JSON
- `desktop_identity`: desktop identity ID, public key and SHA-256 fingerprint
- `mobile_identity`: enrolled mobile device ID and public-key fingerprint

The signed grant has exactly these fields:

    {
      "protocol_version": 1,
      "audience": "voiceassistant-relay",
      "relay_url": "wss://relay.example.com",
      "session_id": "<32 lowercase hex characters>",
      "desktop_id": "<32 lowercase hex characters>",
      "mobile_id": "<32 lowercase hex characters>",
      "desktop_public_key": "<Ed25519 public key, unpadded base64url>",
      "mobile_public_key": "<Ed25519 public key, unpadded base64url>",
      "issued_at_ms": 0,
      "expires_at_ms": 0
    }

The desktop signs UTF-8 canonical JSON: keys sorted lexicographically, compact
separators `,` and `:`, Unicode preserved, and NaN disallowed. The signature
is made with the existing encrypted desktop identity key. The QR grant binds
both enrolled identities, relay origin, audience, session ID and expiry. The
mobile and desktop clients reject a top-level relay URL that differs from the
signed grant value.

## Desktop issuance checks

The desktop must fail closed unless:

1. The desktop remote identity is initialized and uses Ed25519.
2. The selected mobile device is active in the encrypted device registry.
3. The mobile device has an enrolled Ed25519 public key.
4. The public-key fingerprint in the registry matches the actual key bytes.
5. The relay endpoint is WSS, has no user-info/query/fragment/path, and uses
   the supported TLS port (443 or 8443).
6. The audience is configured and the grant lifetime is between 60 and 900
   seconds.

The QR contains public keys and a short-lived signature only. It must never
contain private keys, device bearer tokens, long-lived secrets, or TURN
credentials.

## Endpoint join handshake

The relay endpoint is:

    wss://<relay-host>/v1/relay/<session_id>/<side>

where side is `desktop` or `mobile`.

After opening the WebSocket, the endpoint receives:

    {"type":"relay.challenge","challenge":"<random challenge>","protocol_version":1}

It replies with exactly:

    {
      "type": "relay.authenticate",
      "grant": { "...": "the exact QR grant object" },
      "grant_signature": "<the exact QR grant signature>",
      "issued_at_ms": 0,
      "proof_signature": "<signature over the join proof>"
    }

The join proof object, signed with the private key belonging to the side, is:

    {
      "protocol_version": 1,
      "session_id": "<grant session_id>",
      "side": "desktop or mobile",
      "challenge": "<challenge received on this socket>",
      "grant_sha256": "<SHA-256 hex of canonical grant JSON>",
      "issued_at_ms": 0
    }

The desktop signs with its encrypted desktop identity. The mobile signs with
the private key corresponding to the enrolled mobile public key. Both peers
must submit the exact same grant and grant signature. The relay independently
checks the desktop grant signature, audience, expiry, session binding and
side-specific proof-of-possession.

## Desktop relay client behavior

The desktop connects outbound to
`wss://<relay-host>/v1/relay/<session_id>/desktop`. It validates that the QR
grant still matches its encrypted local Ed25519 identity, validates the enrolled
mobile key fingerprint, checks the relay challenge shape, and signs the
side-specific join proof. The relay socket client runs on a worker thread; it
must not block the desktop UI thread. Incoming relay frames are untrusted
signaling data and must never be interpreted as desktop commands.

The current desktop client reports peer authentication only. It does not
create a WebRTC peer connection, negotiate a DataChannel, or provide remote
control/media transport.

## Mobile verification implemented in this client slice

The Flutter mobile client validates the QR schema, signed relay origin, desktop
grant signature, identity fingerprints, enrolled mobile public key and device
ID before opening the relay socket. It signs the fresh challenge with the
enrolled mobile private key, waits for the authenticated peer-ready notification,
and retains the signaling socket. The scan UI requires the user to compare the
desktop fingerprint with the value shown by the desktop app.

## Mandatory mobile verification before joining

The mobile client must not trust the relay's peer-ready event or the grant
merely because the relay accepts it. Before connecting it must:

1. Validate the QR payload shape and protocol version strictly.
2. Validate the desktop identity ID and public-key encoding.
3. Verify the desktop grant signature over canonical JSON.
4. Verify that the desktop public-key fingerprint matches the trusted
   fingerprint shown by the desktop UI during the user's pairing/QR confirmation.
5. Verify grant desktop/mobile IDs match the payload and the currently enrolled
   mobile device.
6. Verify the grant's mobile public key equals the mobile app's own protected
   enrolled public key; never accept a key supplied by the QR as a replacement.
7. Verify audience, relay URL policy, issue time and expiry locally.
8. Sign the fresh relay challenge with the enrolled mobile private key.

If any check fails, the app must stop and show a clear security error. It must
not silently fall back to a different relay, identity, key or transport.

## Signaling and command boundary

After both peers authenticate, the relay forwards bounded frames. Every SDP
and ICE signaling envelope must still be end-to-end signed and checked by the
receiving peer against the pinned sender identity, expected recipient, session,
expiry and replay guard. Bind the negotiated DTLS fingerprint to the signed SDP.

The relay's `relay.peer_ready` message is only a transport status notification.
It is not proof of application authorization. Desktop commands must continue
through the existing authenticated desktop command channel, RBAC, policy and
approval gates. Never execute a command because a relay or WebRTC connection
was established.

## Not included in this protocol slice

- Desktop UI integration to issue relay QR grants and manage the outbound worker.
- WebRTC offer/answer/ICE state machines and DataChannels.
- STUN/TURN, short-lived TURN credentials or media routing.
- Reconnect, revocation propagation and background/foreground lifecycle.
- Cross-network acceptance tests using independent Wi-Fi, cellular, CGNAT and
  restrictive networks.

Production readiness requires those implementation gates plus independent
security review, abuse/load testing and end-to-end tests.
