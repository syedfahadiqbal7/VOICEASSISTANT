# Application diagnostics and log privacy

The desktop launcher configures structured JSON Lines logging before importing the UI. Logs are written to:

`history/logs/voiceassistant.jsonl`

The log sink rotates at approximately 2 MiB and retains up to five prior files. Each record includes a UTC timestamp, severity, logger, event name and message. Exception details are included for failures. The formatter applies the existing secret-redaction rules to both messages and tracebacks before writing.

Logs are local runtime data and are excluded from Git by the existing `history/*` ignore rule. They can contain operational metadata, paths and exception context, so protect the history directory with the Windows account ACL and include it in retention/backup decisions only when appropriate.

This is not a replacement for audit history: application logs are diagnostic and best-effort; the audit store remains the authoritative record for governed actions. Log rotation is size-based, not a secure-erasure guarantee.


The structured logger is also used by the scheduler, mobile connection manager/bridge and selected desktop UI/shutdown paths. Exception tracebacks are redacted before they reach the JSONL sink. Other legacy modules still contain console output or broad exception handlers; this migration is incremental and does not claim every exception path is centrally logged.
