# Mobile Internet Relay — Security and Architecture Design

**Status:** Proposed design; implementation is not included in this document.  
**Date:** 2026-11-25

## 1. Purpose

Allow an enrolled Flutter companion to reach its paired Windows desktop when the phone and PC are on different networks, without requiring the user to configure port forwarding, expose the desktop API, or operate a VPN. Preserve direct LAN, VPN and user-managed TLS/WSS connections.

The relay is an optional transport path. It is not an assistant backend, command executor, approval authority, device registry authority, or data store.

## 2. Current verified boundary

The application already supports desktop-displayed short-lived QR pairing, mobile QR scanning, explicit desktop approval, per-device credentials and authenticated WebSocket sessions. QR enrollment requires HTTPS and does not embed permanent credentials.

Cross-network reachability currently depends on an operator-provided VPN or a publicly reachable TLS/WSS endpoint. QR pairing does not traverse NAT/firewalls. There is no built-in relay or automatic NAT traversal.

## 3. Recommended topology

Use WebRTC DataChannels between the Windows desktop and Flutter mobile app, with an authenticated signaling service and a TURN relay for NAT traversal. Both endpoints initiate outbound network traffic. When a direct ICE path is impossible, TURN forwards encrypted WebRTC packets; it does not terminate the peer DTLS session.

- Desktop remains the trusted execution and authorization authority.
- Mobile continues to authenticate as its enrolled device.
- Desktop initiates signaling and ICE connectivity outbound; no inbound PC listener or router port-forwarding is required.
- Signaling carries only authenticated session descriptions, ICE candidates and short-lived setup metadata. It must not carry application payloads.
- TURN is an optional packet relay, independently deployable by the user or organization. Direct LAN/VPN paths remain preferred when available.
- WebRTC DataChannels are encrypted with DTLS by the protocol; TURN forwards encrypted packets and cannot read channel contents.
- Keep the current WebSocket transport as a separate direct path during rollout; do not replace it until parity and recovery tests pass.

### ICE connectivity policy

- Default `all` policy: let the maintained WebRTC ICE implementation try direct host/server-reflexive paths first and use TURN when direct connectivity fails. This is standards-based ICE connectivity, not custom application NAT traversal.
- Optional `relay-only` policy: send relay candidates only and force all peer traffic through TURN. This hides endpoint host/server-reflexive candidates from the peer but increases TURN bandwidth, latency and operating cost.
- The owner or organization must be able to select `relay-only` where peer-to-peer address disclosure is unacceptable. Do not silently change this policy or downgrade from a required relay-only session.
- No custom UDP hole-punching code is to be written; all candidate gathering, connectivity checks and consent freshness are delegated to the selected WebRTC implementation.

## 4. Trust boundaries and security invariants

1. **No relay authority:** the relay cannot approve plans, authorize tools, elevate roles, mint device credentials, or decide whether an action is safe.
2. **Desktop policy remains authoritative:** every command and approval is re-authorized by the desktop using its existing device registry, RBAC, rate limits and approval flow.
3. **End-to-end protection:** use an authenticated, forward-secret desktop↔mobile session above the relay transport. The relay forwards ciphertext and routing metadata only; it must not receive plaintext prompts, transcripts, tool arguments, files, approvals or model output.
4. **No permanent QR secret:** QR pairing remains the current short-lived, one-time challenge flow. Relay enrollment is a separate authenticated operation after device pairing.
5. **Short-lived signaling/TURN credentials:** desktop-issued, audience-bound, expiring, single-session tickets; bind them to desktop identity, device identity, role, session ID and protocol version. TURN credentials authorize packet relay only and never replace desktop authorization.
6. **No public desktop listener:** desktop connects outbound to relay over TLS. Do not require router port forwarding or a public inbound desktop port.
7. **Fail closed:** invalid signatures, expired/replayed tickets, unknown devices, protocol mismatch, oversized frames, rate-limit violations or failed cryptographic handshakes terminate the session.
8. **Least data:** relay stores no conversation history, files, prompts or durable message payloads. In-memory queues are bounded and cleared on disconnect.
9. **Revocation:** device revocation at desktop invalidates its relay sessions promptly; desktop disconnect or lease expiry tears down the paired route.
10. **No silent downgrade:** relay transport must never fall back to plaintext or disable certificate validation.

## 5. Session establishment (proposed)

1. User selects **Remote connection** in the desktop/mobile connection settings. Existing direct LAN/VPN WebSocket remains available.
2. Desktop and mobile authenticate to a minimal signaling service using short-lived credentials issued after normal desktop-approved device enrollment.
3. Desktop publishes a random, high-entropy, short-lived rendezvous ID and its WebRTC offer. Mobile retrieves the offer only after proving its enrolled device identity.
4. Signaling exchanges the offer, answer and ICE candidates. Every signaling envelope is authenticated and bound to the enrolled desktop/device identities, session ID, expiry and protocol version. The desktop's DTLS certificate fingerprint must be bound to a desktop identity key that the phone pinned during a trusted enrollment step. Mobile's answer/fingerprint must likewise be authenticated to the desktop. A signaling server must not be able to substitute either fingerprint.
5. Both peers create a WebRTC RTCPeerConnection and a reliable, ordered RTCDataChannel. ICE first attempts direct connectivity; if unavailable, it uses short-lived TURN credentials. Do not require the desktop to accept inbound connections.
6. WebRTC performs its standard DTLS handshake. DataChannel application payloads are encrypted peer-to-peer; the TURN service forwards encrypted packets only. The signaling service never sees DataChannel payloads.
7. The desktop maps the authenticated peer connection to the existing device registry entry and validates every received application message against the existing command schema, role, approval policy, rate limiter and active-session state. Do not trust the signaling server's role claims.
8. On disconnect, expire the rendezvous/tickets and close the peer connection. Reconnection creates fresh credentials, ICE state and DTLS keys; never replay commands or resume an old transport session.

Use maintained WebRTC implementations and the standards-defined DTLS/SCTP DataChannel security rather than inventing an application cryptographic handshake. The exact identity binding and signaling envelope format remain mandatory security-review items.

## 6. Signaling and TURN service requirements

- Separate signaling from TURN packet forwarding. Signaling exchanges only SDP and ICE metadata; TURN carries encrypted WebRTC packets.
- Use WebRTC-compliant STUN/TURN (for example, coturn) with authenticated, short-lived TURN REST credentials. Never enable anonymous TURN or the dangerous server-relay mode.
- Offer TURN/UDP first and TURN/TCP or TURN/TLS fallback for restrictive networks. Validate actual client support for `turns:` on every target platform; do not claim port 443 fallback until device/network tests pass.
- Enforce authenticated rendezvous, high-entropy session IDs, protocol versioning, expiry, anti-replay and per-device rate limits.
- Bound concurrent peer sessions, candidate counts, signaling payload sizes, TURN allocations, bandwidth and session lifetime.
- Apply backpressure and close unhealthy sessions; never queue application commands for an offline desktop.
- No SDP, ICE candidate, TURN credential, token or private key in routine logs. Redact network metadata where practical.
- Health endpoints expose service health and aggregate capacity metrics only.
- TURN has no route to desktop LAN resources, filesystem, process table or local API. Signaling has no application command execution path.

## 7. Desktop and mobile integration

### Desktop
- Add a transport abstraction so current direct WebSocket transport remains intact.
- Add an aiortc peer-connection adapter and authenticated signaling client; use bounded reconnect/backoff and explicit lifecycle cleanup.
- Keep device registry, RBAC, approvals, audit and command handling in the desktop process.
- Surface connection mode, relay health, peer presence and reconnect status in the UI.
- Allow owner to disable remote access and revoke all relay sessions.
- Never start remote access automatically without explicit owner configuration.

### Flutter
- Add a separate Remote connection mode and signaling/TURN configuration.
- Use flutter_webrtc DataChannels and platform secure storage for device identity material and short-lived signaling credentials.
- Keep existing QR pairing and direct connection paths.
- Display desktop identity, connection mode and role; provide disconnect and forget-device controls.
- Reconnect only with fresh session tickets and fresh cryptographic handshakes.

### Relay service
- Prefer a separately deployable signaling service plus a standard TURN server (for example, coturn), with documented container deployment, health checks and resource limits.
- Support self-hosted operation without a vendor account. Keep signaling replaceable and TURN standards-compliant.
- Hosted relay, if offered later, must be opt-in and clearly disclose metadata, region, retention and operational access.
- Relay should be replaceable; no proprietary protocol dependency in desktop/mobile core.

## 8. Availability and lifecycle

- Desktop offline: mobile shows unavailable; relay does not queue commands for later execution.
- Mobile offline: desktop remains usable locally; relay route is released after a bounded grace period.
- Relay outage: direct LAN/VPN connections continue to work; relay sessions fail closed.
- Network change: reconnect with fresh tickets and keys. Do not replay commands automatically.
- Duplicate connection: enforce a documented per-device policy (default: newest authenticated session replaces the old one, with an audit event).
- Desktop shutdown/restart: all relay sessions and ephemeral session state are destroyed.

## 9. Abuse and privacy controls

- Require explicit owner opt-in, a visible remote-access indicator and an immediate disable control.
- Default to no relay service configured and no remote listener.
- Apply quotas by authenticated account/device and global service capacity.
- Prevent rendezvous enumeration with high-entropy identifiers and uniform failure responses.
- Reject oversized messages before allocation and cap decompression (prefer no compression initially).
- Do not persist application payloads or retain session keys.
- Provide an audit event for relay connect, disconnect, rejected authentication, device revocation and owner disable; redact credentials and sensitive metadata.
- Publish a threat model and data-flow diagram before hosted deployment.

## 10. Rollout plan and acceptance gates

### R0 — Design and protocol review
- Approve trust boundaries, identity/key lifecycle, ticket claims, cryptographic handshake and relay threat model.
- Review protocol with an independent security reviewer before production use.

### R1 — Signaling and TURN deployment prototype
- Deploy authenticated signaling and coturn with short-lived credentials, bounded quotas and telemetry.
- Add protocol, authorization, abuse, load, resource-exhaustion and disconnect tests.
- Prove signaling cannot substitute authenticated DTLS fingerprints and TURN cannot decode DataChannel payloads.

### R2 — Desktop WebRTC adapter
- Add opt-in aiortc peer connection, authenticated signaling, bounded reconnect and shutdown cleanup.
- Verify no inbound firewall rule or port-forwarding is required.

### R3 — Flutter WebRTC mode
- Add flutter_webrtc DataChannel transport, connection UI, secure device identity storage and authenticated signaling.
- Preserve existing direct QR pairing and direct transport behavior.

### R4 — End-to-end acceptance
- Test PC on home/office network and phone on cellular network, then reverse the networks.
- Test CGNAT/double-NAT, restrictive outbound networks, TLS failures, relay restart, desktop sleep/restart, mobile background/foreground, device revocation and replay attempts.
- Verify transcript, audio/status events, plans, approvals and cancellation work without duplicate execution.
- Verify relay logs and storage contain no plaintext application content or long-lived credentials.

### R5 — Production readiness
- Independent security review, penetration test, dependency review, operational runbooks, backup/restore for relay configuration, monitoring, capacity and cost controls.
- Hosted relay remains disabled until privacy, data residency, retention and incident-response policies are approved.

## 11. Non-goals for first release

- No public inbound desktop port or router port forwarding.
- No generic VPN replacement or arbitrary TCP tunnel.
- No custom application-level UDP hole-punching implementation outside the standards-based WebRTC ICE stack.
- No command queueing while desktop is offline.
- No cloud execution, cloud conversation storage or relay-side AI processing.
- No change to desktop RBAC, approval policy or execution engine.

## 12. Open implementation decisions

These are implementation choices, not blockers to the architecture:
- Exact signaling envelope format and signature algorithm, including desktop identity key provisioning and rotation.
- Whether signaling is implemented inside the existing FastAPI service or as a separately deployable component.
- coturn deployment topology, TURN REST secret rotation, UDP relay port range and TLS fallback configuration.
- Whether hosted relay is a later commercial service; it is not required for self-hosted functionality.


## 13. Transport and library evaluation (2026-11-25)

### Decision: prefer WebRTC DataChannels + STUN/TURN over a bespoke Noise-over-WebSocket tunnel

The initial design left the application cryptographic handshake and relay framing open. A current library review found a better fit: WebRTC already standardizes ICE/NAT traversal, DTLS-protected DataChannels and TURN packet relaying. This avoids inventing a new cross-platform cryptographic handshake and a proprietary tunnel protocol.

Evidence reviewed:
- The official Flutter WebRTC package documents DataChannel and End-to-End Encryption support on Android, iOS, Windows, macOS and Linux. Current published package is 1.6.3. See https://pub.dev/packages/flutter_webrtc and https://pub.dev/packages/flutter_webrtc/changelog.
- aiortc is a production/stable Python WebRTC implementation. Current published release 1.16.0 requires Python 3.11+, aligning with the repository CI Python 3.11 baseline. Its changelog documents DataChannel fixes and TURN support; its current ICE transport implementation parses `turns:` URLs and configures TLS transport. See https://pypi.org/project/aiortc/ and https://aiortc.readthedocs.io/en/latest/changelog.html.
- WebRTC's security architecture requires DataChannels to be protected with DTLS. See RFC 8827: https://www.rfc-editor.org/rfc/rfc8827.html.
- TURN relays encrypted WebRTC packets; it does not terminate the peer DTLS channel. See https://developers.cloudflare.com/realtime/turn/faq/.
- coturn supports authenticated TURN operation and TLS listeners. Its configuration must be hardened and current source documentation must be used rather than the explicitly outdated wiki page. See https://github.com/coturn/coturn.

### Important caveats before implementation

- This is a preferred technical direction, not a security certification. aiortc and flutter_webrtc versions must be pinned and reviewed; run Windows 11, Android and iOS integration builds in CI.
- The aiortc adapter must be tested for TURN/UDP, TURN/TCP and TURN/TLS. In particular, validate the selected `turns:` URI, TLS certificate validation and restrictive-network behavior. Do not promise universal 443 traversal before testing.
- Signaling is a critical trust boundary. SDP/DTLS fingerprints must be cryptographically bound to the desktop identity and enrolled mobile identity. An authenticated signaling channel alone does not prevent a malicious signaling service from attempting a man-in-the-middle substitution.
- Provision and pin desktop identity during trusted desktop-approved pairing; add enrolled mobile public-key identity to the device registry. Define rotation, revocation and recovery before enabling remote access.
- aiortc currently supports a single TURN server in its ICE configuration path; the adapter must handle server selection/fallback deliberately and test it.
- TURN-only paths, relay-to-relay behavior, CGNAT, double NAT, UDP-blocked networks, mobile backgrounding and reconnect must be proven in an actual two-network test matrix.
- Do not add the low-adoption Dart Noise package or the older Python `noiseprotocol` dependency as a production cryptographic foundation without an independent audit. Do not write a custom handshake.

### Proposed dependency candidates (not yet added)

- Desktop: `aiortc==1.16.0` (Python >=3.11; confirm Windows wheel/dependency compatibility in CI before adoption).
- Flutter: `flutter_webrtc: ^1.6.3` (verify lockfile resolution and Android/iOS native builds).
- Relay: coturn, deployed and configured independently from the app process; signaling service can reuse the existing FastAPI stack if isolation and rate limiting are sufficient.

No runtime dependency has been added in this design-only PR. Implementation starts only after the identity-binding threat model and signaling protocol are reviewed.
