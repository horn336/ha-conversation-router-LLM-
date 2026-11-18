# Conversation Router — Home Assistant integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Adds one conversation agent that forwards each Assist request to whichever
underlying conversation agent you configure — picked dynamically by
condition, not fixed in your pipeline. Assign it once as your Assist
pipeline's conversation agent, and you never have to touch pipeline
configuration again to change which "brain" handles a request.

The original use case: keep a cloud LLM (e.g. an Anthropic/Claude
conversation agent) as your normal Assist brain, but automatically fall back
to a locally-hosted LLM agent when your internet drops or fails over to a
4G/LTE backup — all driven off whatever connectivity sensor you already have,
no pipeline editing, no manual switching.

## How it works

- **Default agent** — used whenever no rule matches. This is your normal,
  everyday agent.
- **Rules** (up to 3, checked in order) — each rule pairs a **condition**
  (a Jinja template, e.g. checking a connectivity `binary_sensor`) with an
  **agent** to use when that condition evaluates true. The first matching
  rule wins; if none match, the default agent is used.

Every incoming request is evaluated against the rules at the moment it
arrives, so the routing can change request-to-request as your conditions
change — no restart, no manual pipeline swap.

This isn't limited to online/offline failover — any Jinja-expressible
condition works: time of day, an `input_boolean` toggle, occupancy, a
specific device's state, etc. Pair it with as many or as few rules as you
need.

## Fallback on "not understood"

Optionally set a **fallback agent**. Every request still goes to the agent
picked by your rules/default first; if that agent's reply means it did not
understand, the router throws the reply away and sends the same input
(text, conversation id, device, satellite, language) to the fallback agent,
whose answer is what you hear.

A reply counts as "not understood" when either:

- **it is an error response** (`response_type: error`, e.g. the built-in
  agent's `no_intent_match` / `no_valid_targets`) and *Fall back when the
  chosen agent returns an error response* is on (the default), or
- **its speech matches a "not understood" pattern** — one regular expression
  per line, case-insensitive, searched anywhere in the reply. This is needed
  for agents such as Sophia NLU that report every reply as `action_done`,
  so the wording is the only signal. The defaults cover every "no match",
  "device not found" and connection/timeout reply Sophia NLU gives. Clear the
  box to fall back on error responses only.

Typical setup: a fast local NLU (e.g. Sophia NLU) as the default agent and a
local LLM (e.g. Qwen via LM Studio/Ollama) as the fallback, so commands stay
instant and anything else is answered by the LLM. The fallback never fires
when the fallback agent is the one that just answered, or is the router itself.

**Limitation:** the router can only catch replies that *say* they failed. If
the first agent confidently matches the wrong intent (an NLU without a
confidence score fuzzy-matching a question onto some device), that reply looks
like success and is returned as-is — and any action it took has already run.

Each fallback is logged at `info` level by
`custom_components.conversation_router`.

## Installation (HACS)

1. HACS → **⋮** → **Custom repositories**.
2. Add `https://github.com/oooshk/ha-conversation-router` with category
   **Integration**.
3. Install **Conversation Router**, then **restart** Home Assistant.
4. **Settings → Devices & Services → Add Integration → Conversation Router**.
5. Open its **Configure** dialog to set the default agent and your rules.
6. **Settings → Voice assistants** → edit your pipeline → set **Conversation
   agent** to **Conversation Router**.

## Example: fall back to a local LLM when offline

Default agent: your cloud conversation agent (e.g. `conversation.claude_conversation`).

| Rule | Condition | Agent |
|---|---|---|
| 1 | `{% raw %}{{ is_state('binary_sensor.router_internet', 'off') }}{% endraw %}` | your local LLM agent |
| 2 | `{% raw %}{{ is_state('binary_sensor.router_dual_wan', 'on') }}{% endraw %}` | your local LLM agent |

Rule 1 catches a full outage; rule 2 catches a router that has failed over
to a 4G/LTE backup connection (still "online", but worth avoiding for a
cloud LLM call). Adjust the entity IDs to whatever your router/connectivity
integration exposes.

## Notes

- Conditions are evaluated with the same Jinja environment as the rest of
  Home Assistant — anything you can write in a template sensor works here.
- If a condition's template fails to render (bad entity ID, syntax error),
  that rule is skipped and logged as a warning rather than blocking the
  conversation.
- The agent fields only list entities in the `conversation` domain, so you
  always pick from agents Home Assistant already knows about.

## License

MIT — see [LICENSE](LICENSE).
