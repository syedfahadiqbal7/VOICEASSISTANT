# Plugin security and execution model

## Package validation

Plugin ZIP installation is intentionally limited to one Python entry file and one JSON manifest. The installer rejects absolute paths, parent-directory traversal, Windows drive paths, symbolic links, encrypted members, unsupported compression, oversized archives, excessive member counts, and unexpected files. It validates the manifest schema, declared tool names, supported permission vocabulary, execution sandbox declaration and SHA-256 format. It compares the entry file SHA-256 to the supplied manifest and rejects mismatches; it never silently rewrites a mismatching checksum. Existing plugins are not overwritten unless the desktop installer obtains explicit confirmation.

A checksum detects changes relative to the supplied manifest. It does not prove who authored the plugin: an attacker able to replace both the Python file and manifest can calculate a new checksum. The author field is a claim, not a cryptographic identity; the runtime never treats that field as verified publisher trust. Signed publisher manifests and a managed trust root remain future work.

## Permissions and policy

The runtime passes manifest-declared permissions to host policy evaluation. Read-only roles also block common mutating tool-name verbs as a defence-in-depth fallback when an older manifest omitted permissions. Name heuristics are not a replacement for complete capability declarations.

Plugin registration is all-or-nothing: the exported BaseTool.name set must exactly match the manifest tool list, and name conflicts reject the plugin without partial registration. Legacy or third-party plugins with an empty permission list are assigned `system:admin` at load time, except for the two built-in pure utility tools (`say_hello` and `json_formatter`). Manifest regeneration preserves existing permissions, derives actual BaseTool.name values without importing the plugin, and assigns `system:admin` when no capability declaration exists.

## Execution boundaries

The manifest field `execution_sandbox` accepts:

- `worker` (default): imports and executes the plugin in a one-shot child process. This provides crash containment, bounded JSON IPC, a timeout and Windows Job Object process/resource limits. The child still runs with the desktop user's OS privileges. It is not an OS security sandbox.
- `appcontainer`: on Windows, imports plugin metadata and executes each invocation inside a no-network Windows AppContainer. The host stages the plugin and minimal worker contract, grants scoped runtime/staging/IPC ACLs to a per-invocation AppContainer SID, and communicates with the host-owned capability broker through a named pipe whose DACL includes that SID. Python starts with isolated flags (`-I -S`) so user site packages, `.pth` files and `sitecustomize` do not run; the trusted worker clears inherited host environment variables before importing plugin code. The worker also uses bounded file IPC, a timeout and Job Object limits. Failure to launch, inspect or execute in AppContainer is returned as an error; there is no fallback to `worker`.

The initial AppContainer pilot is intentionally narrow. The JSON formatter is the first manifest-opted-in plugin. The broker currently supports policy-approved filesystem reads only. Plugins with unsupported imports, dynamic/native APIs, undeclared capabilities or permissions other than `filesystem:read` are rejected from AppContainer execution. This is a staged migration, not a blanket claim that every plugin is sandboxed.

AppContainer does not prove that the Python interpreter or every Windows dependency is vulnerability-free. It is an OS-enforced access boundary for the configured process token and ACLs; it does not replace code review, signed publisher trust, host policy, approval or defense-in-depth. Keep plugins trusted until their manifest explicitly selects AppContainer and the target Windows environment has passed the integration checks.

## Capability broker

The AppContainer worker has no network capability and cannot use loopback TCP. Its host broker uses a local named pipe with a package-SID ACL, bounded newline-delimited UTF-8 JSON, per-invocation random authentication, request replay protection and host-side policy evaluation. Plugin IPC cannot assert user approval. Unsupported broker operations are denied. The current production pilot exposes only read operations; mutating or network capabilities must not be inferred from a manifest declaration.

## Validation and rollout

Windows CI exercises filesystem denial, loopback denial, Python runtime startup, SID-ACL named-pipe communication, AppContainer plugin discovery/execution and a policy-approved host-broker file read. The AppContainer runner is opt-in per plugin. Plugins that do not declare `execution_sandbox: appcontainer` remain on the same-user worker and retain that worker's privileges.

See [Plugin Execution Isolation Design](PLUGIN_EXECUTION_ISOLATION_DESIGN.md) for the broader target architecture and remaining migration work.
