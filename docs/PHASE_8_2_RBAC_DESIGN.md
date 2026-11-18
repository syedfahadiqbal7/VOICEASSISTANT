# Phase 8.2 — Mobile Device RBAC Design

**Status:** Architecture decision recorded; implementation not yet started.  
**Scope:** Authorization for authenticated, enrolled mobile devices. This is not a multi-user account system.

## Context and constraints

VoiceAssistant is currently a single-user Windows desktop agent with a mobile companion. Phase 8.1 establishes per-device authentication and revocation. Authentication answers *which enrolled device connected*; RBAC must separately answer *which operations that device may request*.

The desktop remains the local trust anchor. A mobile client must never be able to assign or elevate its own role. Role changes are performed only through the trusted desktop CLI. The legacy shared-token compatibility mode must not gain administrator privileges.

## Roles

| Role | Intended use |
|---|---|
| `admin` | Owner-controlled device with all mobile capabilities, including security approval |
| `operator` | Submit assistant requests and manage permitted operational actions, but cannot approve security gates or administer devices |
| `approver` | Read session state and respond to approval challenges; cannot submit assistant requests or administer devices |
| `viewer` | Read-only session and schedule visibility |

## Initial permission matrix

| WebSocket message | admin | operator | approver | viewer |
|---|:---:|:---:|:---:|:---:|
| `ping` | yes | yes | yes | yes |
| `get_session_state` | yes | yes | yes | yes |
| `list_schedules` | yes | yes | yes | yes |
| `user_message` | yes | yes | no | no |
| `cancel_schedule` | yes | yes | no | no |
| `undo_last` | yes | yes | no | no |
| `approval_response` | yes | no | yes | no |
| Device enrollment / role change / revocation | local CLI only | no | no | no |

Unknown message types and unknown roles are denied. Authorization must be checked server-side for every message, after authentication and before any bridge method is called. Hiding a mobile UI control is not an authorization boundary.

## Security decisions

1. Device role is stored in the encrypted device registry alongside device metadata; raw device secrets remain absent.
2. The client never sends a role claim. The server derives the role from the authenticated registry record.
3. Role resolution and credential verification should occur in one registry read/lock operation, returning a minimal authenticated principal.
4. New enrollments default to `operator`; granting `admin` or `approver` requires an explicit local CLI argument.
5. Existing registry entries without a role migrate to `operator` (least privilege). They must not silently become administrators.
6. Legacy shared-token and unauthenticated loopback compatibility sessions receive only `operator` permissions. In particular, they cannot approve security challenges.
7. Approval routing must target an authenticated `admin` or `approver` connection, not simply the first connected mobile client. If no eligible approver exists, the desktop approval UI remains the fallback; if no approval UI exists, the action is denied.
8. Role changes and revocation take effect on the next per-message revalidation cycle (currently at most approximately 10 seconds while idle). No long-lived role cache may outlive that boundary.
9. A corrupt or unreadable registry remains fail-closed. No fallback to shared-token authentication is allowed once the registry exists.
10. RBAC does not replace the existing policy engine, tool-level permission checks, approval gates, or local Windows security boundary.

## Implementation sequence

- **8.2.1:** Add role constants, permission matrix, and unit tests for allow/deny behavior.
- **8.2.2:** Add role metadata and atomic authenticated-principal lookup to the encrypted device registry; migrate missing roles to `operator`.
- **8.2.3:** Add explicit local CLI role assignment and safe role-change command; never print stored credentials during list/update.
- **8.2.4:** Bind connection identity/role to each WebSocket and enforce authorization before dispatch.
- **8.2.5:** Route approval challenges only to eligible approvers and test mixed-role/multiple-client behavior.
- **8.2.6:** Add regression tests for role tampering, privilege escalation, revoked sessions, legacy mode, corrupt registry, and every message type; update security documentation and CI.

## Acceptance criteria

- Every supported mobile message has an explicit permission decision.
- No mobile-supplied field can elevate a role.
- Missing/unknown roles fail closed (legacy and pre-RBAC registry migration is explicitly least-privilege `operator`).
- Operator/viewer devices cannot approve security challenges.
- Approval cannot be approved by a different client from the one challenged.
- Role changes and revocations are observed by active sessions within the documented revalidation interval.
- Existing desktop-only behavior and Phase 8.1 credential behavior remain regression-tested.
- CI is green on the exact resulting commit before Phase 8.2 is marked complete.

## Out of scope

Multi-user accounts, external identity providers, tenant isolation, remote role administration, device certificates, and per-human attribution are not part of this phase. Those require a separate identity and threat-model design.
