# Mobile Remote Identity and Signaling Threat Model

**Status:** Design review artifact; no runtime implementation is included.  
**Related:** `MOBILE_RELAY_ARCHITECTURE.md`, PR #55  
**Date:** 2026-11-25

## 1. Current implementation facts

The current mobile device registry is encrypted at rest and stores a per-device ID, name, SHA-256 digest of the bearer token, role and lifecycle timestamps. The bearer token is returned only during enrollment. The registry schema is version 1 and currently has no public-key identity field.

The current desktop local-data protection uses AES-256-GCM. On Windows, the data-encryption key is protected with the current user's DPAPI profile. An explicit versioned keyring can protect newer writes and supports key rotation/recovery. Key administration is local-only and is not exposed through mobile dispatch.

The current QR pairing flow:
- Displays a short-lived one-time challenge and HTTPS endpoint.
- Requires an explicit desktop approval.
- Creates a normal device-registry entry with a least-privilege role.
- Delivers the device bearer credential once the phone proves possession of the QR challenge.
- Does not currently exchange asymmetric device identity keys or pin a desktop identity key.

These facts mean the existing bearer-token model is sufficient for the current authenticated WebSocket connection, but it is not yet a complete cryptographic identity system for authenticated WebRTC signaling.

## 2. Assets to protect

- Desktop identity private key.
- Enrolled mobile-device signing public keys and mobile private keys.
- Device bearer tokens and short-lived signaling/TURN credentials.
- SDP offers/answers and DTLS certificate fingerprints.
- ICE candidates and network metadata.
- Active WebRTC session state and replay protection counters.
- Desktop command/approval messages and all application payloads.

## 3. Threat actors and assumptions

Consider:
- A malicious or compromised signaling service attempting to substitute SDP, fingerprints, ICE candidates or peer identity.
- A malicious TURN operator observing packet metadata and attempting to inspect or alter relayed traffic.
- A nearby attacker presenting a fake QR code or attempting to enroll an unauthorized device.
- A stolen mobile device with access to app storage.
- A local unprivileged process attempting to read desktop key material or tamper with the encrypted registry.
- Replay, downgrade, session fixation, rendezvous enumeration and resource-exhaustion attacks.

Assume the operating system, cryptographic primitives and selected WebRTC implementations are not compromised. The relay service may be malicious; therefore it must not be trusted with application payload confidentiality or desktop authorization.

## 4. Identity model

### Desktop identity

- Generate a dedicated asymmetric signing key pair on first explicit remote-access setup.
- Do not reuse the AES-GCM data-encryption key, DPAPI master material, device bearer token or TLS certificate private key as a signing key.
- Persist the private key only inside an authenticated encrypted desktop store. Reuse the existing `agent.storage` encrypted persistence and atomic-write patterns only after confirming backup, restore, migration and key-rotation registration for the new store.
- On Windows, rely on the existing DPAPI-protected data-key path for at-rest protection; do not export plaintext private key material to logs, environment variables or command-line arguments.
- Expose only the public key and a human-verifiable fingerprint.
- Define explicit key rotation and recovery. A changed desktop identity key must require a trusted re-pair/re-pin operation; never silently accept a replacement.

### Mobile identity

- Generate a device signing key pair on the phone during QR enrollment.
- Store the private key using platform secure storage; it must not be written to app preferences, logs, backups or QR payloads.
- Submit the public key during the HTTPS pairing request. The desktop approval screen must show a short fingerprint for the pending mobile public key, alongside the device name and requested role.
- Persist the public key against the device ID in the encrypted desktop registry.
- Device revocation must invalidate both the bearer credential and the public-key identity for all new remote sessions.
- Re-enrollment creates a new identity; do not silently overwrite an existing device key.

### Registry evolution

The existing registry is schema version 1. Adding signing public keys requires an explicit, tested schema migration (proposed schema version 2):
- Preserve existing IDs, names, token hashes, roles, timestamps and revocation state.
- Validate key type, encoding, exact length and uniqueness.
- Reject malformed, duplicate or unknown key metadata.
- Do not synthesize public keys for legacy devices.
- Existing devices without a registered public key may continue using their existing direct WebSocket path, but remote WebRTC mode remains unavailable until they complete a trusted identity-enrollment step.
- Keep the last-active-admin invariant and interprocess locking unchanged.
- Verify rollback and backup/recovery behavior before migration ships.

## 5. Trusted QR enrollment changes

The QR payload may add:
- Protocol version.
- HTTPS pairing endpoint.
- Existing short-lived one-time challenge and expiry.
- Desktop identity public key (or a compact fingerprint plus a secure way to retrieve the public key).

It must never contain:
- Desktop or mobile private keys.
- Long-lived device bearer tokens.
- TURN long-term shared secrets.
- Reusable signaling credentials.

Enrollment sequence:
1. Desktop generates or loads its identity key pair and displays the desktop public-key fingerprint in the pairing window.
2. Desktop generates the existing one-time QR challenge and includes the desktop public key in the signed/pinned enrollment descriptor.
3. Mobile scans the QR and displays the desktop identity fingerprint for user verification.
4. Mobile generates its device key pair and submits its public key together with the one-time challenge over HTTPS.
5. Desktop approval UI displays the device name, public-key fingerprint and least-privilege role. Approval must be a deliberate local action.
6. Desktop stores the mobile public key in the encrypted registry and issues the existing unique bearer credential.
7. Mobile stores its private key and device credential in secure storage before acknowledging receipt.
8. If receipt acknowledgement fails, existing provisional-enrollment cleanup revokes the device and the key binding.

The desktop identity fingerprint must be shown in the trusted desktop UI, not only returned by the signaling service. A user must be able to compare it with the fingerprint shown by the phone. The app should make the comparison easy and should not imply that scanning an arbitrary QR is proof of desktop identity.

## 6. Authenticated signaling envelope

Every signaling message must be bound to:
- Protocol version.
- Random session ID and high-entropy rendezvous ID.
- Desktop identity key ID.
- Mobile device ID and registered public-key identity.
- Message type (offer, answer, ICE candidate, end-of-candidates, close).
- Monotonic per-session sequence number.
- Creation and expiry timestamps.
- Cryptographic signature over a canonical, unambiguous serialization of all fields and payload.

Rules:
- Desktop signs its offer and all desktop-originated signaling messages.
- Mobile signs its answer and all mobile-originated signaling messages.
- Each peer verifies the signature against the already-pinned public key before applying the SDP or ICE candidate.
- The signaling server cannot change the DTLS certificate fingerprint, SDP, ICE credentials, peer ID, role, expiry or session ID without invalidating the signature.
- Reject duplicate sequence numbers, expired envelopes, wrong-session messages, unknown message types and malformed candidates.
- Bind the negotiated DTLS fingerprint to the signed offer/answer and the expected peer identity.
- Never trust a role or authorization claim supplied by signaling. The desktop resolves the current role from its own registry on session establishment and revalidates it during the session.
- Keep signaling payload sizes and candidate counts strictly bounded.

Canonical serialization and signature algorithm selection must be documented and tested against cross-language test vectors before implementation. Do not invent a cryptographic encoding.

## 7. TURN credentials and privacy

- Issue short-lived, scoped TURN credentials only after the device has authenticated to the desktop/signaling control plane.
- TURN credentials authorize relay allocation only; they do not authorize a desktop session or command.
- Never expose the coturn shared secret to desktop/mobile clients.
- Rotate TURN REST shared secrets using a documented overlap procedure.
- Use TLS for signaling and configure TURN/TLS as a fallback where the client implementation and network support it.
- TURN sees source/destination network metadata, timing and traffic volume, but WebRTC DataChannel contents remain protected by peer DTLS.
- Do not log SDP, ICE candidates, TURN passwords, device tokens, public-key private material or application payloads.
- Document the network metadata retained by self-hosted and hosted deployments.

## 8. Session authorization and revocation

1. Authenticate the enrolled device.
2. Verify device is active and its public key matches the enrolled registry record.
3. Verify the signed signaling envelope and desktop identity pin.
4. Complete the WebRTC DTLS handshake and confirm the negotiated fingerprint matches the signed descriptor.
5. Bind the DataChannel to the authenticated device principal.
6. Apply existing server-side role checks and approval routing to every application command.
7. On device revocation, role change, desktop disable or desktop shutdown, close the DataChannel and peer connection and invalidate signaling/TURN session credentials.
8. On reconnect, require fresh signed signaling, fresh ICE credentials and a new DTLS session. Never replay a previous application command automatically.

## 9. Failure behavior

- Unknown or changed desktop fingerprint: refuse remote connection and require trusted re-pairing.
- Unknown/replaced mobile key: refuse session; require explicit desktop enrollment.
- Invalid signature, replay, expired ticket or protocol downgrade: close the session and emit a redacted audit event.
- Signaling unavailable: remote connection unavailable; direct LAN/VPN mode remains usable.
- TURN unavailable: allow a direct ICE path if policy permits; otherwise fail clearly. Do not fall back to plaintext.
- Desktop offline: show unavailable; never queue commands for later execution.
- Device revoked or role changed: terminate or reauthorize the active session according to a documented policy; no stale role is trusted.

## 10. Required security tests

- Registry v1 → v2 migration preserves every legacy field and fails closed on corrupt data.
- Public key validation rejects malformed, duplicate, wrong-length and unsupported keys.
- Device public key replacement is denied unless explicit local re-enrollment is completed.
- Desktop identity key is encrypted at rest and is never present in logs or plaintext backup artifacts.
- Key rotation/recovery preserves identity or forces an explicit re-pin; no silent identity reset.
- Modified SDP, DTLS fingerprint, ICE candidate, peer ID, role, expiry or session ID invalidates the signature.
- Cross-language signature test vectors pass in Python and Dart.
- Replay, out-of-order, cross-session and expired signaling envelopes are rejected.
- TURN credentials expire, are scoped and cannot authenticate to the desktop command API.
- Revocation closes an active WebRTC session and prevents reconnection.
- A malicious signaling/TURN test harness cannot read DataChannel application payloads or alter a session undetected.
- Direct LAN/VPN WebSocket behavior and existing approval/RBAC regression suites remain unchanged.

## 11. Implementation gate

Do not implement production remote WebRTC access until:
- The desktop/mobile identity lifecycle and schema migration are approved.
- A canonical signed signaling format and cross-language vectors exist.
- Secure storage and recovery behavior are verified on Windows, Android and iOS.
- The selected aiortc/flutter_webrtc versions pass target-platform build and interoperability tests.
- TURN/UDP, TURN/TCP and TURN/TLS behavior is verified in actual two-network tests.
- An independent security review approves the signaling identity binding and threat model.

