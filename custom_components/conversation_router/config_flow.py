"""Config flow for Conversation Router."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import CONF_AGENT, CONF_CONDITION, CONF_DEFAULT_AGENT, CONF_RULES, DOMAIN, MAX_RULES


def _agent_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="conversation"))


def _template_selector() -> selector.TemplateSelector:
    return selector.TemplateSelector()


class ConversationRouterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial (one-time) setup. All real config lives in options."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="Conversation Router", data={})

        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ConversationRouterOptionsFlow()


class ConversationRouterOptionsFlow(config_entries.OptionsFlow):
    """Lets the user pick which agent to use, and the conditions that select it."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            rules = []
            for i in range(1, MAX_RULES + 1):
                condition = (user_input.get(f"rule{i}_condition") or "").strip()
                agent = user_input.get(f"rule{i}_agent")
                if condition and agent:
                    rules.append({CONF_CONDITION: condition, CONF_AGENT: agent})

            return self.async_create_entry(
                data={
                    CONF_DEFAULT_AGENT: user_input[CONF_DEFAULT_AGENT],
                    CONF_RULES: rules,
                }
            )

        current = self.config_entry.options
        current_rules = current.get(CONF_RULES, [])

        def rule_condition(i: int) -> str:
            return current_rules[i - 1][CONF_CONDITION] if len(current_rules) >= i else ""

        def rule_agent(i: int) -> str | None:
            return current_rules[i - 1][CONF_AGENT] if len(current_rules) >= i else None

        schema_dict: dict = {
            vol.Required(
                CONF_DEFAULT_AGENT,
                description={"suggested_value": current.get(CONF_DEFAULT_AGENT)},
            ): _agent_selector(),
        }

        for i in range(1, MAX_RULES + 1):
            schema_dict[
                vol.Optional(
                    f"rule{i}_condition",
                    description={"suggested_value": rule_condition(i)},
                )
            ] = _template_selector()
            schema_dict[
                vol.Optional(
                    f"rule{i}_agent",
                    description={"suggested_value": rule_agent(i)},
                )
            ] = _agent_selector()

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))
