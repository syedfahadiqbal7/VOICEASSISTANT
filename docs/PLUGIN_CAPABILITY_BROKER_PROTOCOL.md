# Plugin Capability Broker — Initial IPC Contract

**Status:** Initial host broker transport implemented; filesystem read-only operations are the only active handlers.  
**Security classification:** Compatibility/policy migration layer only. This is not an OS sandbox.

## Purpose

Plugin workers need a narrow API for requesting host-mediated operations so that direct filesystem, network, process, clipboard, registry and desktop APIs can be migrated away from plugin code. The broker runs in the desktop process and evaluates each request against the active `PolicyEngine`, the plugin's manifest-declared permissions and the configured resource roots.

## Per-invocation lifecycle

1. The host creates one broker instance for the exact plugin tool invocation.
2. The broker binds to IPv4 loopback on an ephemeral port and creates a cryptographically random per-invocation token.
3. The host passes the loopback endpoint and token to the worker through the worker's child-process environment.
4. The worker client sends one bounded JSON request per TCP connection.
5. The host validates the protocol version, exact field set, request ID, token, plugin-bound tool identity, declared capability and policy decision.
6. The broker returns a bounded JSON response and records request IDs to reject replay during that invocation.
7. The broker is shut down when the worker exits, times out or fails.

## Protocol v1

Request fields are exact; unknown or missing fields are rejected:

- `protocol_version`: integer `1`
- `request_id`: unique opaque string (maximum 128 characters)
- `token`: per-invocation secret
- `capability`: capability declared in the plugin manifest
- `operation`: exact typed operation name
- `arguments`: JSON object

Limits:
- Request: 64 KiB
- Response: 1 MiB
- File read: 512 KiB
- Socket read/write timeout: 5 seconds
- Maximum unique request IDs per invocation: 4096

The worker cannot provide an `approved` flag. Approval is host-owned and the broker currently passes `approved=False` to policy evaluation for every request.

## Implemented operations

| Operation | Capability | Behavior |
|---|---|---|
| `filesystem.read_text` | `filesystem:read` | Read UTF-8 text from an existing regular file under configured `allowed_read_roots` |
| `filesystem.read_bytes` | `filesystem:read` | Read bounded bytes from an existing regular file under configured `allowed_read_roots`; return base64 |

All other operations are denied or return an unsupported-operation result. Filesystem writes/deletes, network, process execution/termination, clipboard, screen, registry, email, messaging, Docker and service operations do not have broker handlers yet.

Worker-side helper module: `tools.plugin_capability_client`. Plugins can import `read_text(path)` or `read_bytes(path)` to use the host broker.

## Mandatory security limitation

A loopback endpoint and random token do not restrict the worker's OS privileges. Plugin code can read the token from its own environment and can still call Python, Win32, filesystem, network and native APIs directly under the desktop user's token. The broker therefore does not prevent bypass or prove isolation.

The current worker remains **crash/resource containment only**. The next security gate is Windows restricted-token/AppContainer enforcement with explicit ACLs, default-deny networking, controlled handle inheritance and negative tests proving the worker cannot access protected files or network directly. Until that passes, plugins must remain trusted code and this broker must not be described as a sandbox.
