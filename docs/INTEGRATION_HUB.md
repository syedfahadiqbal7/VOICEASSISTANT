# Integration Hub Foundation

This phase adds the encrypted local connection registry used by future provider adapters.

## Supported provider records

| Provider key | Capability | Accepted scope |
|---|---|---|
| `google` | Email | `https://www.googleapis.com/auth/gmail.readonly` |
| `google` | Calendar | `https://www.googleapis.com/auth/calendar.readonly` |
| `microsoft` | Email | `Mail.Read` |
| `microsoft` | Calendar | `Calendars.Read` |

Basic identity/offline scopes are allow-listed where needed for OAuth. Write scopes such as Gmail modify, Mail.ReadWrite, calendar write and send-mail permissions are intentionally not accepted in this phase.

## Storage and safety

- Connection records are stored in `history/integrations.json` using the existing authenticated local encryption and atomic-write path.
- An OS-backed sidecar lock serializes concurrent reads and updates across processes.
- The registry accepts one account record per provider.
- Access and refresh tokens are available only through the internal `get_connection` method for provider adapters. Status methods never return tokens.
- Account identifiers, allow-listed scopes, expiry and derived capabilities are safe status metadata; tokens are never written to logs by this module.
- Disconnect removes the provider's token record.
- The encrypted store is included in portable backups and registered for explicit VAENC2 key migration.
- Expired access tokens are marked `needs_refresh`; this registry does not refresh tokens itself.

## Important implementation boundary

This is storage and capability-mapping infrastructure only. It does not yet implement OAuth authorization, token exchange/refresh, account selection, provider API calls, a desktop connection wizard, or a connected-account UI. No account is connected by default. A provider must not be reported as connected until a real OAuth flow has validated the account and saved the returned token set.

Email and calendar capabilities are derived only from the exact read-only scopes above. The email declutter workflow must not delete or archive mail until a later implementation adds appropriate write scopes, explicit per-action approval and end-to-end provider tests.
