# Plugin Execution Isolation — Architecture and Delivery Plan

**Status:** Architecture decision with an opt-in Windows AppContainer pilot  
**Review date:** 2026-11-24  
**Applies to:** Windows desktop agent, plugin registry, policy engine and future plugin marketplace

## 1. Current security position

The current plugin pipeline performs useful package-level checks:

- A plugin requires a manifest and matching SHA-256 checksum.
- Archive extraction rejects traversal, symlinks, unexpected members and oversized input.
- Manifest permissions are validated against a supported capability vocabulary.
- Exported tool names must exactly match manifest declarations.
- Registration is all-or-nothing and duplicate names are rejected.
- Missing capability declarations fail closed to `system:admin`, except for two built-in pure utilities.

These controls protect package integrity and reduce accidental privilege, but they do not make every plugin safe. The default `execution_sandbox: worker` mode imports and executes plugin code in a one-shot child process with the desktop user's OS privileges; this is crash containment, not a security sandbox.

An opt-in Windows AppContainer path is now implemented for plugins whose verified manifest sets `execution_sandbox: appcontainer`. Both metadata discovery and each invocation use a no-network AppContainer process, scoped ACL grants and a package-SID ACL named-pipe broker. The JSON formatter is the first opted-in plugin. The pilot currently supports only standard-library plus staged `tools` imports and policy-approved `filesystem:read`; unsupported capabilities fail closed without falling back to the same-user worker.

**Decision:** Describe only explicitly opted-in plugins as AppContainer-isolated. Continue treating every default-worker plugin as trusted code. The pilot is not blanket sandboxing or independent security certification; publisher signing, broader capability migration, target-machine validation and independent review remain outstanding.

## 2. Goals

1. Keep the desktop process, encryption keys, conversation state and approval UI outside plugin-controlled code.
2. Preserve the existing tool contract at the host boundary: name, description, JSON schema, help metadata and structured result.
3. Make every plugin request pass through a host-owned broker that revalidates identity, capability, arguments, policy and user approval.
4. Bound plugin CPU time, memory, process count, output size and lifetime.
5. Deny network and arbitrary filesystem access by default in the worker; grant narrow brokered operations only when required.
6. Ensure worker crashes, hangs, malformed IPC and oversized responses fail closed without crashing the desktop.
7. Preserve existing plugin package verification and never treat a checksum or manifest author field as publisher authentication.

## 3. Non-goals

- A Python subprocess by itself is not a security sandbox.
- A Windows Job Object alone is not a security boundary; it primarily constrains process lifetime and resource usage.
- A worker running under the same unrestricted Windows token can still access the user's files, credentials, network and processes.
- The host must not expose data-encryption keys, DPAPI operations, raw conversation stores, approval callbacks or unrestricted Python objects to a plugin worker.
- This design does not promise exactly-once external side effects. Plugin integrations must define idempotency and reconciliation semantics.

## 4. Target architecture

```text
Desktop UI / Conversation Manager
              |
       Planner / Plan Executor
              |
      Host Tool Registry
              |
    Host Policy + Approval Gate
              |
       Plugin Broker (host)
        /             \
   schema validation  capability mapping
        \             /
        Authenticated, bounded IPC
              |
       Restricted Plugin Worker
              |
     One verified plugin package
```

The host remains authoritative for:
- Plugin identity and verified entry-file digest
- Manifest-to-export parity
- User approval and role/policy decisions
- Capability authorization for each individual request
- File roots, process targets, network destinations and output limits
- Cancellation, timeout, worker termination and audit events

The worker receives only the arguments required for one tool call and returns a bounded JSON result. It never receives a live host object, callback, file handle, credential, encryption key or arbitrary capability token.

## 5. IPC contract

Use a versioned JSON message envelope with a strict maximum byte length. Do not use pickle or Python object serialization across the trust boundary.

Request fields:
- `protocol_version`
- `request_id` (random, unique per call)
- `plugin_id` (host-resolved identity, not accepted as authority from the worker)
- `tool_name`
- `arguments` (JSON object validated against the registered schema)
- `deadline_utc` or bounded timeout duration
- `correlation_id` (opaque; must not contain prompt text or secrets)

Response fields:
- `protocol_version`
- `request_id`
- `ok`
- `result` (bounded JSON object)
- `error_code` (categorical, allowlisted)
- `duration_ms`

Rules:
- Reject unknown fields, invalid types, duplicate request IDs, malformed JSON and oversized frames.
- Bind each worker channel to one plugin identity and one installed content digest.
- A worker cannot ask the host to execute a different plugin or tool.
- Every broker operation is separately authorized; the plugin's manifest is a requested capability declaration, not a grant.
- Never serialize traceback text, environment variables, raw prompts, tokens or key material to the worker response.
- A timeout or worker crash returns a typed tool error. Do not silently rerun a side-effecting request.
- Cancellation is cooperative first; the host may terminate the worker process tree after the configured grace period.

## 6. OS-enforced containment

### Windows primary target

The Windows implementation must use a restricted security principal/token or AppContainer-style isolation with explicit ACLs. It must not rely on the default inherited desktop user token.

Required controls before calling the worker sandboxed:
- Dedicated restricted identity with no administrator membership or debug privilege.
- No access to the desktop profile, DPAPI key files, runtime history, browser profiles, SSH material or credential stores.
- Private worker directory containing only the verified plugin package and explicitly approved temporary files.
- Network denied by default. Any allowed network request is brokered and destination constrained.
- Process creation denied by default. If a plugin requires a child process, the broker must expose a narrow, typed operation instead.
- Job Object limits for active process count, memory/CPU policy where supported, and kill-on-close lifecycle.
- Explicit handle inheritance list; no inherited handles other than the IPC channel and required standard streams.
- Safe DLL/Python import search paths and no writable import directory preceding trusted runtime libraries.
- Worker startup handshake confirms the expected protocol, plugin identity and content digest before tool calls are accepted.

The AppContainer pilot has passed Windows CI integration checks for filesystem denial, loopback denial, Python startup, SID-ACL named-pipe IPC, plugin metadata discovery and plugin execution. This proves the tested CI image path, not every Windows 10/11 edition or the user's target machine. If AppContainer setup cannot be established for a selected plugin, that plugin must fail closed and must not fall back to the same-user worker.

### Other platforms

Do not silently fall back to an unrestricted subprocess and claim equivalent security. Linux namespace/seccomp and macOS sandbox controls require separate platform implementations and test suites. Until those exist, third-party plugin execution must be disabled or explicitly labelled unsupported on those platforms.

## 7. Capability broker

Map the current manifest vocabulary to host-owned broker operations. Examples:

| Declared capability | Brokered operation | Default |
|---|---|---|
| `filesystem:read` | Read an approved path under an allowlisted root | Deny |
| `filesystem:write` | Atomic write under an approved root, with diff/undo policy | Deny |
| `filesystem:delete` | Delete a specific approved target after policy/approval | Deny |
| `network:read` | HTTP request to an allowlisted destination | Deny |
| `process:read` | Query an allowlisted process property | Deny |
| `process:execute` | Launch a named, allowlisted executable with structured arguments | Deny |
| `process:terminate` | Terminate a specific process after approval | Deny |
| `clipboard:read/write` | Read/write clipboard through desktop broker | Deny |
| `screen:read/control` | Screenshot or UI action through desktop broker | Deny |
| `system:admin` | No generic RPC; requires a dedicated reviewed host operation | Deny |

Do not implement a generic `run_powershell`, `run_shell`, arbitrary file path or arbitrary URL broker endpoint. Such endpoints would collapse the boundary and reintroduce unrestricted host authority.

## 8. Rollout and compatibility

1. **Inventory:** classify every active plugin by imports, external dependencies, required capabilities, GUI/COM usage, network use, filesystem roots and long-running behavior.
2. **Contract extraction:** separate tool metadata/schema from executable plugin code. Validate schemas in the host without importing the plugin.
3. **Worker prototype:** add versioned JSON IPC, per-plugin worker lifecycle, timeout/output limits and crash recovery. Mark this stage as crash containment only.
4. **Broker implementation:** add narrowly typed broker operations with host-side policy and approval enforcement.
5. **Windows containment:** launch workers with a restricted identity, ACL isolation and process/resource limits. Fail closed when containment cannot be established.
6. **Pilot:** migrate pure, stateless plugins first (formatters/read-only helpers), then selected read-only integrations. Keep GUI/COM and high-impact plugins disabled until explicitly supported.
7. **Enforcement:** make sandboxed workers the only supported path for third-party plugins. Any temporary legacy mode must be explicit, visible, owner-controlled and unavailable to untrusted packages.
8. **Retirement:** remove in-process third-party imports after parity, recovery and compatibility tests pass.

## 9. Required security and reliability tests

- Worker import cannot execute in the desktop process.
- Worker cannot read a sentinel file outside its assigned directory.
- Worker cannot read the desktop environment, DPAPI key, runtime stores or credential material.
- Worker cannot access network unless a broker policy explicitly permits the destination.
- Worker cannot launch an arbitrary child process or escape its process/job boundary.
- Malformed, oversized, replayed or cross-plugin IPC requests are rejected.
- A worker cannot spoof plugin identity, capability, tool name or request ID.
- Policy denial and approval rejection prevent the broker operation, even if the worker requests it repeatedly.
- Worker crash, hang, timeout, memory exhaustion and forced termination do not corrupt host state.
- A timed-out side-effecting request is marked uncertain and is not automatically replayed.
- Every broker action is audited with a categorical result and redacted metadata.
- Existing plugin manifest/export, checksum, permission and registration regressions remain green.
- Full Windows integration tests run under a non-administrator test identity; unit tests alone are not sufficient evidence of OS containment.

## 10. Release gate

Do not claim plugin sandboxing or permit untrusted third-party plugins until all of the following are true:

- Windows OS containment is implemented and independently validated.
- The host broker is the only route to protected resources.
- The negative security tests above pass on supported Windows versions.
- A malicious-plugin review and independent penetration test have been completed.
- Plugin compatibility and rollback behavior are documented.
- The UI clearly distinguishes verified package integrity, publisher trust, worker crash isolation and OS-enforced sandbox status.

Until then, the current documented warning remains in force: plugins execute with the desktop process privileges and must be installed only from trusted sources.


---

## Implementation checkpoint — 2026-11-24

**Implemented:** initial one-shot worker process and host-side proxy (PR #32, merged as `8383c0dba99a35d5a11046fa0a136d3371867d63`).

The current worker:
- Imports plugin code and evaluates tool metadata in a child process, not the desktop process.
- Revalidates the source SHA-256 digest immediately before import.
- Uses a versioned JSON stdin/stdout protocol, 1 MiB request/response limits, sanitized categorical errors and a bounded execution timeout.
- Preserves host-side manifest verification, tool-name parity, permission metadata and the existing `BaseTool` contract.

**Not implemented:** restricted token/AppContainer, Job Object resource enforcement, network/filesystem isolation, broker-only resource access, process-tree containment, publisher identity or an OS sandbox. The worker has the desktop user's privileges and is only a crash-containment boundary. Host protocol byte limits do not prevent a hostile plugin from consuming worker memory/disk or using direct OS APIs.

CI run [#45333862364](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/45333862364) passed 311 Python regressions, 16 Phase 0 smoke checks, Flutter analysis/tests, Android build and unsigned iOS build.

The next implementation stage is the host-owned capability broker and Windows OS-enforced worker containment. The release gates in this design remain mandatory; this checkpoint does not authorize untrusted plugins.


## Windows worker process containment — implemented checkpoint

PR #34 adds a Windows Job Object to each worker. The parent starts the worker and keeps it blocked on stdin, attaches it to the Job Object, then sends the JSON request. Current limits are:
- Kill-on-job-close.
- 16 active processes per job.
- 2 GiB per-process memory.
- 3 GiB aggregate job memory.
- One-megabyte maximum worker stdout response, enforced while draining the pipe.

On POSIX, the worker starts in a new process group and the group is terminated on timeout. A regression test verifies that a plugin-spawned child process does not outlive a timed-out worker.

**Verified:** CI run [#45335729490](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/45335729490), 312 Python tests and 16 smoke tests, plus Flutter and mobile builds.

These controls improve crash containment and resource governance but do not restrict the worker's access to the user's files, credentials or network. They do not constitute an OS sandbox. Restricted-token/AppContainer and host-broker enforcement remain the next required implementation stage.

## Current implementation checkpoint — 2026-11-24 (registry integration)

PR #46 wires the existing AppContainer runner into the verified plugin manifest and ToolRegistry lifecycle:

- `execution_sandbox` is validated and defaults to `worker`; only an explicit `appcontainer` declaration selects the OS boundary.
- Metadata discovery and tool execution both occur inside AppContainer for opted-in plugins.
- AppContainer startup uses isolated Python flags (`-I -S`) so user site packages, `.pth` files and `sitecustomize` do not run before the trusted worker clears inherited environment variables and loads plugin code.
- The runner rejects dynamic/native imports, undeclared capabilities and permissions beyond the current `filesystem:read` broker operation.
- The JSON formatter is the first opted-in plugin. Other plugins remain on the same-user worker until individually reviewed and migrated.
- A failure in AppContainer metadata discovery or execution is returned as a failure; there is no same-user fallback.

**Verified CI:** run [#45396454585](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/45396454585) passed 342 Python regressions, 16 Phase 0 smoke checks, Flutter analysis/tests, Android APK build and unsigned iOS build.

**Remaining gates:** independent security review, target Windows machine validation, resource/disk-exhaustion controls, cleanup verification, publisher signing, and migration of additional plugin dependencies/capabilities. This remains a narrow opt-in pilot, not authorization for untrusted third-party plugins.

