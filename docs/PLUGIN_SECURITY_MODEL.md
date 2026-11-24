# Plugin security and execution model

## Package validation

Plugin ZIP installation is intentionally limited to one Python entry file and one JSON manifest. The installer rejects absolute paths, parent-directory traversal, Windows drive paths, symbolic links, encrypted members, unsupported compression, oversized archives, excessive member counts, and unexpected files. It validates the manifest schema, declared tool names, supported permission vocabulary, and SHA-256 format. It compares the entry file SHA-256 to the supplied manifest and rejects mismatches; it never silently rewrites a mismatching checksum. Existing plugins are not overwritten unless the desktop installer obtains explicit confirmation.

A checksum detects changes relative to the supplied manifest. It does not prove who authored the plugin: an attacker able to replace both the Python file and manifest can calculate a new checksum. The author field is a claim, not a cryptographic identity; the runtime never treats that field as verified publisher trust. Signed publisher manifests and a managed trust root remain future work.

## Permissions and policy

The runtime passes manifest-declared permissions to policy evaluation. Read-only roles also block common mutating tool-name verbs as a defence-in-depth fallback when an older manifest omitted permissions. Name heuristics are not a replacement for complete capability declarations.

Plugin registration is all-or-nothing: the exported BaseTool.name set must exactly match the manifest tool list, and name conflicts reject the plugin without partial registration. Legacy or third-party plugins with an empty permission list are assigned system:admin at load time, except for two built-in pure utility tools (say_hello and json_formatter). Manifest regeneration preserves existing permissions, derives actual BaseTool.name values without importing the plugin, and assigns system:admin when no capability declaration exists.

## Important isolation limitation

Plugins are imported and execute inside the VoiceAssistant Python process. SHA-256 verification, permission metadata, and approval gates do not sandbox Python code and cannot prevent arbitrary code in a malicious plugin from using the process privileges. Install plugins only from sources you trust. A separate low-privilege worker process/container with a restricted IPC capability broker is required before claiming OS-level plugin sandboxing.


## Planned isolation work

The detailed target architecture, IPC contract, Windows containment requirements, rollout plan and release gates are documented in [Plugin Execution Isolation Design](PLUGIN_EXECUTION_ISOLATION_DESIGN.md). This is a design decision, not an implemented sandbox. Until the Windows restricted-worker and broker controls pass their negative security tests, all plugins must continue to be treated as trusted in-process code.


## Current implementation status — 2026-11-24

Plugin inspection and execution have been moved to a one-shot child process (PR #32, merged). This provides crash containment and a JSON boundary, with source digest revalidation and execution timeout. It is **not** a sandbox: the worker retains the desktop user's OS privileges and can directly access resources available to that user. Continue treating all plugins as trusted code. Restricted-token/AppContainer, network/filesystem isolation, process/resource quotas and a host-only capability broker remain unimplemented.
