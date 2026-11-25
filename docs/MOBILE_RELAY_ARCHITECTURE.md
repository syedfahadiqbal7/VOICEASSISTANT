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

Use an outbound-only persistent connection from the desktop to a relay. The mobile app opens an outbound TLS/WSS connection to the same relay. The relay associates the two live connections and forwards bounded opaque application frames.

- Desktop remains the trusted execution and authorization authority.
- Mobile continues to authenticate as its enrolled device.
- Desktop initiates the relay connection; no inbound port on the PC is required.
- Relay is optional and must be independently deployable by the user or organization.
- Direct LAN/VPN/WSS remains preferred when available.
- Do not implement UDP hole punching or peer-to-peer fallback in the first release; those paths complicate firewall behavior and security review.

## 4. Trust boundaries and security invariants

1. **No relay authority:** the relay cannot approve plans, authorize tools, elevate roles, mint device credentials, or decide whether an action is safe.
2. **Desktop policy remains authoritative:** every command and approval is re-authorized by the desktop using its existing device registry, RBAC, rate limits and approval flow.
3. **End-to-end protection:** use an authenticated, forward-secret desktop↔mobile session above the relay transport. The relay forwards ciphertext and routing metadata only; it must not receive plaintext prompts, transcripts, tool arguments, files, approvals or model output.
4. **No permanent QR secret:** QR pairing remains the current short-lived, one-time challenge flow. Relay enrollment is a separate authenticated operation after device pairing.
5. **Short-lived relay tickets:** desktop-issued, audience-bound, expiring, single-session tickets; bind them to desktop identity, device identity, role, session ID and protocol version. Relay tickets never replace the desktop's own authorization checks.
6. **No public desktop listener:** desktop connects outbound to relay over TLS. Do not require router port forwarding or a public inbound desktop port.
7. **Fail closed:** invalid signatures, expired/replayed tickets, unknown devices, protocol mismatch, oversized frames, rate-limit violations or failed cryptographic handshakes terminate the session.
8. **Least data:** relay stores no conversation history, files, prompts or durable message payloads. In-memory queues are bounded and cleared on disconnect.
9. **Revocation:** device revocation at desktop invalidates its relay sessions promptly; desktop disconnect or lease expiry tears down the paired route.
10. **No silent downgrade:** relay transport must never fall back to plaintext or disable certificate validation.

## 5. Session establishment (proposed)

1. User selects **Remote connection (Relay)** in the desktop/mobile connection settings. Direct connection remains unchanged.
2. Desktop authenticates to the configured relay using a relay-specific credential or mutually authenticated TLS identity. This credential grants transport registration only.
3. Desktop registers a random, high-entropy, short-lived rendezvous identifier and publishes a signed, expiring session descriptor. Do not expose a stable desktop identifier in public discovery.
4. The already-paired mobile authenticates to the relay using a short-lived desktop-issued ticket bound to its device ID and current role.
5. Mobile and desktop perform an authenticated ephemeral key agreement. The desktop proves possession of its enrolled desktop identity; the mobile proves possession of its device identity. Derive directional AEAD keys and sequence spaces.
6. Relay binds the two live sockets to the rendezvous session and forwards only bounded encrypted frames.
7. Desktop validates every decrypted application message against the existing WebSocket command schema, device role, approval policy, rate limiter and active-session state.
8. On disconnect, revoke the rendezvous ticket and destroy ephemeral keys. Reconnection creates a new ticket and fresh ephemeral keys; never resume an old cryptographic session by replaying prior frames.

The exact cryptographic handshake must use a reviewed, maintained protocol/library (for example, a Noise protocol pattern with pinned identity keys), not bespoke cryptography. Select the concrete pattern and key lifecycle during implementation review.

## 6. Relay protocol requirements

- TLS 1.2+ (prefer TLS 1.3), hostname verification, certificate validation and optional mTLS for desktop-to-relay registration.
- Explicit protocol version and message type allowlist.
- Strict maximum frame size, bounded per-session queue, bounded concurrent sessions, connection quotas and idle timeout.
- Per-connection and per-device rate limits; abuse controls must not rely only on IP addresses.
- Backpressure: if either peer cannot keep up, stop reading or close the session; never accumulate unbounded memory.
- No payload logging. Operational logs may include random session IDs, event codes, byte counts, duration and failure category, but no tokens, keys or ciphertext-derived content.
- Relay health endpoints expose only service health and aggregate capacity metrics.
- Relay has no route to the desktop's LAN, filesystem, process table or local API. Do not implement a generic TCP proxy or arbitrary URL forwarding.

## 7. Desktop and mobile integration

### Desktop
- Add a transport abstraction so current direct WebSocket transport remains intact.
- Add a relay client that dials outbound and reconnects with bounded exponential backoff and jitter.
- Keep device registry, RBAC, approvals, audit and command handling in the desktop process.
- Surface connection mode, relay health, peer presence and reconnect status in the UI.
- Allow owner to disable remote access and revoke all relay sessions.
- Never start remote access automatically without explicit owner configuration.

### Flutter
- Add a separate Relay connection mode and relay URL / organization configuration.
- Use platform secure storage for relay credentials and identity private keys.
- Keep existing QR pairing and direct connection paths.
- Display desktop identity, connection mode and role; provide disconnect and forget-device controls.
- Reconnect only with fresh session tickets and fresh cryptographic handshakes.

### Relay service
- Prefer a small separately deployable service with a documented container deployment, health checks, resource limits and TLS termination.
- Support self-hosted operation without a vendor account.
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

### R1 — Relay service transport prototype
- Implement bounded rendezvous and opaque frame forwarding only.
- Add unit, protocol, fuzz, load, resource-exhaustion and disconnect tests.
- Prove the relay cannot decode payloads and cannot reach desktop LAN resources.

### R2 — Desktop outbound client
- Add opt-in configuration, authenticated outbound connection, bounded reconnect, session registration and shutdown cleanup.
- Verify no inbound firewall rule or port-forwarding is required.

### R3 — Flutter relay mode
- Add relay connection UI, secure identity storage, session ticket exchange and end-to-end handshake.
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
- No peer-to-peer UDP hole punching.
- No command queueing while desktop is offline.
- No cloud execution, cloud conversation storage or relay-side AI processing.
- No change to desktop RBAC, approval policy or execution engine.

## 12. Open implementation decisions

These are implementation choices, not blockers to the architecture:
- Exact reviewed Noise pattern / library and desktop identity key storage.
- Relay authentication model for self-hosted deployments (mTLS versus signed bootstrap credential).
- Relay deployment runtime and packaging aligned with the existing Python project.
- Whether hosted relay is a later commercial service; it is not required for self-hosted functionality.
