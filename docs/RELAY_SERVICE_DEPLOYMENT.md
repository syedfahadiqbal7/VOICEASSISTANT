# Self-hosted rendezvous relay (prototype)

This service is a separate, optional signaling component. It is not the desktop
agent, command API, device registry, approval service, or TURN server.

## Current scope

- Both endpoints initiate outbound WebSocket connections to a public TLS/WSS URL.
- A short-lived desktop-signed grant binds the session ID, relay audience, desktop
  identity, enrolled mobile identity, public keys and expiry.
- Each endpoint proves possession of its corresponding Ed25519 private key against
  a fresh relay challenge.
- Both peers must present the exact same signed grant. A session cannot be joined
  using a second grant that reuses its session ID.
- The relay holds only in-memory peer routing state and forwards bounded text
  frames. Restart clears all sessions.
- The relay does not persist SDP, ICE candidates, credentials or application data.
  It does not inspect or authorize desktop commands.

## Important security boundary

The relay verifies that a grant is signed by the desktop public key embedded in
that grant. This is a self-certifying proof, not a trust decision. The mobile
client must independently compare the grant's desktop public key/fingerprint
against the desktop identity pinned during trusted pairing. The desktop must
independently verify that the mobile public key in the grant is the currently
enrolled key for that device. Neither endpoint may trust the relay's identity
claims or relay.peer_ready notification.

Every SDP/ICE signaling envelope must still be verified by the receiving peer
against its pinned identity before applying it. Bind the negotiated DTLS
fingerprint to the signed SDP. Keep all application commands and authorization
on the desktop's existing authenticated command channel/DataChannel handler.
Do not treat successful relay authentication as permission to execute a command.

This prototype does not yet provide NAT traversal for media. It only
provides signaling rendezvous. WebRTC peer connections, STUN/TURN configuration,
short-lived TURN credentials, mobile/desktop transport adapters and real
cross-network acceptance tests are not included. Do not advertise remote
connectivity as complete until those phases pass.

## Self-host deployment

Prerequisites:

- A Linux host or VM with Docker Engine and the Docker Compose plugin.
- A DNS A/AAAA record for the relay hostname pointing to that host.
- Inbound TCP 80 and 443 available for Caddy certificate issuance and WSS.
- Outbound HTTPS access for certificate issuance/renewal.

Deployment commands:

    cd deploy/relay
    cp .env.example .env
    # Edit .env and set RELAY_DOMAIN to your DNS hostname.
    docker compose --env-file .env -f compose.yaml up -d --build
    docker compose --env-file .env -f compose.yaml ps

The relay container is not published directly to the host. Caddy terminates TLS
and reverse-proxies WebSocket traffic to the private Docker network. The
container runs as a non-root user, read-only filesystem, dropped Linux
capabilities, no-new-privileges, bounded memory/CPU/PIDs, and no persistent
application-data volume.

The Compose network reserves 172.30.240.0/24 and pins Caddy to 172.30.240.2.
Uvicorn trusts forwarded client-address headers only from that exact Caddy IP;
it must not use a wildcard trusted-proxy setting. If this subnet conflicts with
the host's VPN or Docker network ranges, change the subnet and Caddy static IP
in compose.yaml together with the trusted proxy IP in relay_service/Dockerfile.

The default audience is voiceassistant-relay; keep it identical in the
desktop-issued grant and mobile verification configuration. Change it only as
part of a coordinated client configuration.

## Operational notes

- The health endpoint returns aggregate service health only.
- Session routing is process-local and disappears on restart. Run one replica
  until a separately reviewed distributed rendezvous design exists.
- Do not put a load balancer in front of multiple replicas without sticky
  routing and a reviewed shared-session design.
- Configure host firewall, OS patching, Docker updates, DNS, TLS renewal,
  monitoring and log retention according to the deployment's security policy.
- Uvicorn access logging is disabled in the supplied container command to avoid
  recording session IDs from WebSocket paths. Do not enable Caddy access logs or
  any proxy/application logs that capture session identifiers, authorization
  headers or message frames.
- Do not expose the container's port 8080 publicly.
- This prototype has in-memory global connection/session caps, per-IP active
  connection and handshake-attempt limits, and per-connection frame rate limits.
  These limits reset on process restart and are not a distributed abuse-control
  system. Production deployment still requires abuse/load testing, independent
  security review and operational alerting.

## Next implementation gates

1. Desktop grant creation using the existing encrypted desktop identity store.
2. Mobile grant verification against the pinned desktop key and enrolled mobile key.
3. Desktop outbound WSS client and Flutter outbound WSS client.
4. WebRTC DataChannels with signed SDP/DTLS fingerprint binding.
5. STUN/TURN integration and short-lived TURN credentials; test UDP, TCP and TLS.
6. Revocation, reconnect, mobile backgrounding, restart and resource-exhaustion tests.
7. Real two-network tests including cellular, CGNAT and restrictive outbound networks.
