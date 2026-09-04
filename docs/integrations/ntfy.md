# ntfy on the AIMDS-Suite (AIS-232)

The Suite runs an ntfy server behind `<suite root>/ntfy` and authenticates it with the
LiteLLM VirtualKey (`Authorization: Bearer sk-…`); the Suite's LiteLLM MCP gateway also
exposes `go-mcp-ntfy` tools (`ntfy_send_notification`, `ntfy_list_topics`, `ntfy_get_messages`)
through AIMDSSuiteMCP when enabled for the tenant. Topics: `private-<user_id>` per user plus
team topics per profile (`general/*`, `alerts/*`, `leads/*`, `finance/*`, …).

Hermes configures its own ntfy pieces zero-touch (`hermes_cli/iamds_suite.py::resolve_suite_ntfy`):

| Piece | What is derived | Where |
|---|---|---|
| Gateway platform adapter (`plugins/platforms/ntfy`) — subscribes to the private topic, OWU ↔ Hermes P2P, cron delivery | server `<root>/ntfy`, token = VirtualKey, topic `private-<user_id>` | `suite_auto_config()` in the adapter; used by `check_requirements` / `is_connected` / `_env_enablement` / `NtfyAdapter` / standalone send |
| Catalog `NtfyMCP` (`ntfy_publish_message`, `ntfy_poll_topic`) | `NTFY_SERVER_URL`, `NTFY_AUTH_TOKEN`, `NTFY_DEFAULT_TOPIC` injected into the server env | `tools/mcp_tool.py::_inject_suite_ntfy_env` |

The user id comes from LiteLLM `GET <root>/litellm/key/info` (`info.user_id`), cached 24 h per key
fingerprint in `HERMES_HOME/state/iamds_suite_ntfy.json`; the primary Suite environment is
`model.provider` when it is a Suite slug, otherwise the first configured environment with a key.
Explicit settings (`NTFY_TOPIC`, `NTFY_SERVER_URL`, `NTFY_TOKEN`, `platforms.ntfy.extra`,
`mcp_servers.NtfyMCP.env`) always win; `NTFY_AUTO_SUITE=0` disables the fallback. Without a
user id the adapter has server and token but no topic and stays disabled (set `NTFY_TOPIC`).

Skill: `skills/aimds_custom/ntfy-notifications`.
