# Plugin authoring contract (Phase 0)

## Location
Place one tool per file under `plugins/*.py`. Files starting with `__` are ignored.

## Required shape
```python
from tools.base import BaseTool
from typing import Any, Dict

class MyTool(BaseTool):
    @property
    def name(self) -> str:
        return "my_tool"  # unique, snake_case

    @property
    def description(self) -> str:
        return "One-line description for the LLM."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "..."},
            },
            "required": ["query"],
        }

    def execute(self, query: str = "") -> Dict[str, Any]:
        try:
            return {"content": "result text", "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}
```

## Required manifest
Plugins without a valid sidecar manifest are not loaded. After the Python file is ready, create its manifest using the helper:

```python
from pathlib import Path
from agent.plugin_marketplace import write_manifest_beside

write_manifest_beside(
    Path("plugins/my_tool.py"),
    name="my_tool",
    version="1.0.0",
    author="local",
    tools=["my_actual_tool_name"],
    permissions=["filesystem:read"],
)
```

The helper calculates the SHA-256 checksum. The manifest `tools` list must contain the exact `BaseTool.name` values exported by the plugin. If the Python source changes, regenerate the manifest checksum.

The optional manifest field `execution_sandbox` defaults to `worker`. Set it to `appcontainer` only for a reviewed Windows-compatible plugin that meets the current pilot restrictions. The helper supports this field directly:

```python
write_manifest_beside(
    Path("plugins/my_tool.py"),
    name="my_tool",
    version="1.0.0",
    author="local",
    tools=["my_actual_tool_name"],
    permissions=[],
    execution_sandbox="appcontainer",
)
```

AppContainer metadata discovery and tool execution both run inside the sandbox. If sandbox creation, plugin inspection or execution fails, the plugin returns an error; it is never retried in the same-user worker. The current pilot supports standard-library plus the staged `tools` contract and only the `filesystem:read` broker permission. Unsupported imports, dynamic/native APIs, undeclared capabilities and other permissions are rejected. The JSON formatter is the first opted-in example. Do not mark other plugins AppContainer-compatible without adding and passing Windows integration tests for their dependency and capability requirements.

Permission examples:
- `filesystem:read`, `filesystem:write`
- `network:read`, `network:write`
- `process:read`, `process:execute`, `process:terminate`
- `screen:read`, `screen:control`
- `clipboard:read`, `clipboard:write`
- `system:environment:write`, `service:control`, `docker:control`
- `email:send`, `messaging:send`

## Rules
1. `execute` **must** return `{"content": str, "is_error": bool}`.
2. Do **not** register a tool named `task_scheduler` (disabled; use agent Schedules / `list_windows_tasks`).
3. Declare every capability in `permissions`; missing permissions can cause a read-only role to under-enforce access.
4. Mutating actions (files, shell, network side-effects) should be obvious in `description` and require explicit approval.
5. Avoid blocking UI for more than a few seconds; long work must have a timeout.
6. No secrets in plugin source or manifests; read secrets from environment variables / OS credential storage.
7. Tool names must be unique across the application. Duplicate names are rejected and the existing tool is preserved.

## Disable a plugin
- Rename to `something.py.disabled`, **or**
- Add filename to `DISABLED_PLUGIN_FILES` in `agent/tool_registry.py`.


## Supported permission identifiers

Manifests must use only these exact identifiers:

- filesystem:read, filesystem:write, filesystem:delete
- network:read, network:write
- process:read, process:execute, process:terminate
- screen:read, screen:control
- clipboard:read, clipboard:write
- system:environment:write, system:admin
- service:control, docker:control, registry:read, registry:write
- email:read, email:send, messaging:read, messaging:send

Unknown or misspelled permissions are rejected by manifest validation. Permission declarations are enforced by the application policy at tool invocation; for the default `worker` mode they are not an operating-system sandbox. The default worker is a child process but retains the desktop user's OS privileges. Only a plugin explicitly marked `execution_sandbox: appcontainer` receives the Windows AppContainer boundary. Plugins without that declaration must still be treated as trusted code.


## Legacy plugin defaults

A plugin with no permission metadata is treated as privileged (system:admin) at runtime, so it cannot be auto-approved as a read-only tool. Only the built-in pure utilities say_hello and json_formatter are allowlisted to have no permissions. The manifest generator derives actual BaseTool.name values from source without importing the module and preserves existing permissions; a new plugin without reviewed permissions receives system:admin. Replace that default only after reviewing the code and documenting its capabilities.
