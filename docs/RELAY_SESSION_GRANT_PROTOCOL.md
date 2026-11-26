# Rendezvous relay session grant protocol v1

**Status:** Implementation contract for the relay prototype; client integration is not complete.

## 1. Purpose

The current remote-session QR flow redeems its one-time challenge by making an
HTTPS request directly to the desktop API. That is not sufficient for a phone
on cellular/another network when the desktop API is reachable only on the local
network. The relay QR flow must not depend on a direct request to the desktop.

The relay QR therefore carries a desktop-signed, short-lived grant. Both the
desktop and the already-enrolled phone connect outbound to the relay over WSS.
The relay challenge proves possession of the endpoint signing keys; the grant
binds both identities to one session.

## 2. QR payload

The future QR payload uses a distinct type so existing direct QR behavior
remains backward compatible:

- type: voiceassistant_relay_session
- version: 1
- relay_endpoint: public WSS URL, for example wss://relay.example.com/v1/relay/{session_id}/{side}
- grant: the exact grant object defined below
- grant_signature: canonical-base64url Ed25519 signature by the desktop identity

The QR contains no private key, device bearer token, remote-session ticket,
TURN secret or reusable relay credential. The session ID is a random 128-bit
identifier and the grant expires no later than 15 minutes after issue.

## 3. Signed grant object

The exact fields are:

- protocol_version: integer 1
- audience: configured relay audience string
- session_id: 32 lowercase hexadecimal characters generated with a CSPRNG
- desktop_id: pinned desktop identity ID
- mobile_id: already-enrolled device ID
- desktop_public_key: 32-byte Ed25519 public key, canonical base64url
- mobile_public_key: 32-byte Ed25519 public key, canonical base64url
- issued_at_ms: Unix epoch milliseconds
- expires_at_ms: Unix epoch milliseconds

The desktop signs canonical JSON of the grant object. The grant must be
immutable after signing. The relay requires the same grant digest from both
peers, not merely the same session ID and expiry.

## 4. Endpoint trust checks

### Mobile

Before connecting, the mobile client must:

1. Require the expected QR type and protocol version.
2. Verify the grant signature using the desktop public key already pinned in
   mobile secure storage during trusted pairing. Do not trust a public key just
   because it is embedded in the QR.
3. Compare desktop identity ID and key/fingerprint to the pinned identity.
4. Compare mobile ID and public key to the currently enrolled mobile identity.
5. Check relay audience, WSS scheme, expiry, clock skew and session ID format.
6. Connect to the relay and sign the fresh relay challenge with the mobile key.

The mobile must reject an unknown/changed desktop key and require explicit
trusted re-pairing. It must not fall back to the existing direct endpoint
silently when a relay grant fails.

### Desktop

Before connecting, the desktop must:

1. Resolve the device ID from its own encrypted device registry.
2. Require an active, non-revoked Ed25519 mobile identity.
3. Compare the grant's mobile public key and desktop identity to local records.
4. Sign the grant with the existing dedicated desktop identity key.
5. Connect outbound to WSS and sign the fresh relay challenge with the desktop
   identity key.
6. Bind the resulting peer/DataChannel to the current device principal and
   re-check role/revocation in the existing desktop authorization path.

The relay does not become an identity authority. Its successful authentication
response is not a substitute for either endpoint's local checks.

## 5. Relay challenge proof

After WSS connection, the relay sends a fresh unpredictable challenge. Each
endpoint signs canonical JSON containing:

- protocol_version: integer 1
- session_id: grant session ID
- side: desktop or mobile
- challenge: exact relay challenge
- grant_sha256: SHA-256 hex digest of canonical grant JSON
- issued_at_ms: Unix epoch milliseconds

The relay verifies the desktop proof with the grant desktop public key and the
mobile proof with the grant mobile public key. The challenge is unique per
connection and the proof is accepted only during the short authentication
window. The server never receives a private key.

## 6. Signaling after relay join

The relay forwards text frames without interpreting application signaling.
Peers must continue using the existing signed signaling envelope format:
protocol version, sender, recipient, session, message ID, sequence, issue/expiry,
message type, payload and Ed25519 signature.

Receiving clients must verify:
- signature against the already-pinned/enrolled peer key;
- sender, recipient and session binding;
- expiry, sequence and message ID replay protection;
- allowed message type and bounded payload;
- signed SDP content and DTLS fingerprint binding before applying SDP or ICE.

The relay must never be used for command execution, approval authority, file
transfer, transcripts or AI payloads. WebRTC DataChannels carry the encrypted
application channel; TURN, when added, forwards encrypted packets only.

## 7. Lifecycle and compatibility

- Desktop starts an outbound relay connection only after explicit owner action
  creates a relay QR/session.
- Mobile starts an outbound relay connection only after scanning and verifying
  the relay QR.
- Both peers use fresh session grants and fresh challenge proofs on reconnect.
- A relay disconnect never queues commands; existing direct LAN/VPN connection
  remains available and unchanged.
- Device revocation, desktop remote-access disable, grant expiry or desktop
  shutdown closes the local peer and invalidates the session.
- Existing voiceassistant_remote_session QR and direct WebSocket paths remain
  supported during migration. The relay QR is a separate versioned type.

## 8. Not yet implemented

The relay service PR does not yet create this grant from the desktop API, add
the new QR type, or implement either endpoint client. It also does not implement
WebRTC DataChannels, DTLS fingerprint binding, STUN/TURN, TURN credentials,
or cross-network acceptance tests. This document is a protocol contract, not
evidence that remote access is operational.
