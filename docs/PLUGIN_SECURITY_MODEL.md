# Plugin security and execution model

## Package validation

Plugin ZIP installation is intentionally limited to one Python entry file and one JSON manifest. The installer rejects absolute paths, parent-directory traversal, Windows drive paths, symbolic links, encrypted members, unsupported compression, oversized archives, excessive member counts, and unexpected files. It validates the manifest schema, declared tool names, supported permission vocabulary, and SHA-256 format. It compares the entry file SHA-256 to the supplied manifest and rejects mismatches; it never silently rewrites a mismatching checksum. Existing plugins are not overwritten unless the desktop installer obtains explicit confirmation.

A checksum detects changes relative to the supplied manifest. It does not prove who authored the plugin: an attacker able to replace both the Python file and manifest can calculate a new checksum. The author field is a claim, not a cryptographic identity. Signed publisher manifests and a managed trust root remain future work.

## Permissions and policy

The runtime passes manifest-declared permissions to policy evaluation. Read-only roles also block common mutating tool-name verbs as a defence-in-depth fallback when an older manifest omitted permissions. Name heuristics are not a replacement for complete capability declarations.

Plugin registration is all-or-nothing: the exported BaseTool.name set must exactly match the manifest tool list, and name conflicts reject the plugin without partial registration.

## Important isolation limitation

Plugins are imported and execute inside the VoiceAssistant Python process. SHA-256 verification, permission metadata, and approval gates do not sandbox Python code and cannot prevent arbitrary code in a malicious plugin from using the process privileges. Install plugins only from sources you trust. A separate low-privilege worker process/container with a restricted IPC capability broker is required before claiming OS-level plugin sandboxing.
