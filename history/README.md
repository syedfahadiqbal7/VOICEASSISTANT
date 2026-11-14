# Runtime data directory

The application creates its local JSON stores here on first run. Conversation history,
memory, audit events, policy, scheduled jobs, skill packs, and undo backups are private
runtime data and are intentionally excluded from Git.

If cloud LLM failover is used, copy `llm_failover.example.json` to `llm_failover.json`.
Never put API keys in that file; configure `VOICEASSISTANT_CLOUD_API_KEY` as an environment
variable instead.
