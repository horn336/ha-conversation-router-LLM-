"""The conversation entity that picks an underlying agent per request."""
from __future__ import annotations

import logging
from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.template import Template

from .const import CONF_AGENT, CONF_CONDITION, CONF_DEFAULT_AGENT, CONF_RULES

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([ConversationRouterEntity(entry)])


class ConversationRouterEntity(conversation.ConversationEntity, conversation.AbstractConversationAgent):
    """Forwards each request to whichever agent's condition currently matches."""

    _attr_has_entity_name = True
    _attr_name = "Conversation Router"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = entry.entry_id

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        agent_id = self._resolve_agent_id()

        if not agent_id:
            intent_response = conversation.intent.IntentResponse(language=user_input.language)
            intent_response.async_set_speech(
                "Conversation Router has no default agent configured yet."
            )
            return conversation.ConversationResult(
                response=intent_response, conversation_id=user_input.conversation_id
            )

        _LOGGER.debug("Conversation Router routing to %s", agent_id)

        return await conversation.async_converse(
            self.hass,
            text=user_input.text,
            conversation_id=user_input.conversation_id,
            context=user_input.context,
            language=user_input.language,
            agent_id=agent_id,
            device_id=user_input.device_id,
        )

    def _resolve_agent_id(self) -> str | None:
        options = self._entry.options
        rules = options.get(CONF_RULES, [])

        for rule in rules:
            condition = rule.get(CONF_CONDITION)
            agent = rule.get(CONF_AGENT)
            if not condition or not agent:
                continue
            try:
                result = Template(condition, self.hass).async_render()
            except TemplateError as err:
                _LOGGER.warning(
                    "Conversation Router: error evaluating condition %r: %s", condition, err
                )
                continue
            if self._truthy(result):
                return agent

        return options.get(CONF_DEFAULT_AGENT)

    @staticmethod
    def _truthy(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        return bool(value)
