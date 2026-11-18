"""The conversation entity that picks an underlying agent per request."""
from __future__ import annotations

import logging
import re
from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.template import Template

from .const import (
    CONF_AGENT,
    CONF_CONDITION,
    CONF_DEFAULT_AGENT,
    CONF_FALLBACK_AGENT,
    CONF_FALLBACK_ON_ERROR,
    CONF_FALLBACK_PATTERNS,
    CONF_RULES,
    DEFAULT_FALLBACK_PATTERNS,
)

_LOGGER = logging.getLogger(__name__)


def compile_patterns(text: str | None) -> list[re.Pattern]:
    """One case-insensitive regex per non-blank line. Raises re.error on a bad line."""
    return [
        re.compile(line.strip(), re.IGNORECASE)
        for line in (text or "").splitlines()
        if line.strip()
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([ConversationRouterEntity(entry)])


class ConversationRouterEntity(conversation.ConversationEntity, conversation.AbstractConversationAgent):
    """Forwards each request to whichever agent's condition currently matches.

    With a fallback agent configured, a reply from that agent which signals
    "not understood" (an error response, or speech matching one of the
    fallback patterns) is discarded and the same input goes to the fallback
    agent instead.
    """

    _attr_has_entity_name = True
    _attr_name = "Conversation Router"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = entry.entry_id
        options = entry.options
        self._fallback_agent: str | None = options.get(CONF_FALLBACK_AGENT) or None
        self._fallback_on_error: bool = options.get(CONF_FALLBACK_ON_ERROR, True)
        try:
            self._fallback_patterns = compile_patterns(
                options.get(CONF_FALLBACK_PATTERNS, DEFAULT_FALLBACK_PATTERNS)
            )
        except re.error as err:
            # The options flow validates patterns, so this only happens with a
            # hand-edited entry; fall back to the shipped defaults.
            _LOGGER.warning(
                "Conversation Router: invalid fallback pattern (%s), using defaults", err
            )
            self._fallback_patterns = compile_patterns(DEFAULT_FALLBACK_PATTERNS)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        agent_id = self._resolve_agent_id()

        if not agent_id:
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_speech(
                "Conversation Router has no default agent configured yet."
            )
            return conversation.ConversationResult(
                response=intent_response, conversation_id=user_input.conversation_id
            )

        _LOGGER.debug("Conversation Router routing to %s", agent_id)
        result = await self._async_forward(agent_id, user_input, user_input.conversation_id)

        fallback = self._fallback_agent
        # Never fall back to the agent that just failed, or to this router (a loop).
        if not fallback or fallback in (agent_id, self.entity_id):
            return result

        reason = self._unrecognized_reason(result)
        if reason is None:
            return result

        _LOGGER.info(
            "Conversation Router: %s did not handle %r (%s), falling back to %s",
            agent_id,
            user_input.text,
            reason,
            fallback,
        )
        # Keep the same conversation so the fallback agent's chat history
        # continues; if the caller sent none, reuse the id the first agent made.
        return await self._async_forward(
            fallback, user_input, user_input.conversation_id or result.conversation_id
        )

    async def _async_forward(
        self,
        agent_id: str,
        user_input: conversation.ConversationInput,
        conversation_id: str | None,
    ) -> conversation.ConversationResult:
        return await conversation.async_converse(
            self.hass,
            text=user_input.text,
            conversation_id=conversation_id,
            context=user_input.context,
            language=user_input.language,
            agent_id=agent_id,
            device_id=user_input.device_id,
            satellite_id=user_input.satellite_id,
            extra_system_prompt=user_input.extra_system_prompt,
        )

    def _unrecognized_reason(self, result: conversation.ConversationResult) -> str | None:
        """Why the routed agent's reply counts as "not understood", or None if it handled it."""
        response = result.response
        if self._fallback_on_error and response.response_type == intent.IntentResponseType.ERROR:
            code = response.error_code.value if response.error_code else "unknown"
            return f"error response: {code}"

        speech = (response.speech.get("plain") or {}).get("speech") or ""
        for pattern in self._fallback_patterns:
            if pattern.search(speech):
                return f"reply {speech!r} matched {pattern.pattern!r}"
        return None

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
