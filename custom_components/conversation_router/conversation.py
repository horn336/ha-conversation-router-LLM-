"""The conversation entity that picks an underlying agent per request."""
from __future__ import annotations

import logging
import re
from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
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
    CONF_GROUNDING_CHECK,
    CONF_GROUNDING_EXTRA_WORDS,
    CONF_RULES,
    DEFAULT_FALLBACK_PATTERNS,
)
from . import grounding

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
        self._grounding_check: bool = options.get(CONF_GROUNDING_CHECK, False)
        self._grounding_extra = grounding.words(options.get(CONF_GROUNDING_EXTRA_WORDS))

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

        fallback = self._fallback_agent
        # Never fall back to the agent that just failed, or to this router (a loop).
        can_fall_back = bool(fallback) and fallback not in (agent_id, self.entity_id)

        if can_fall_back and self._grounding_check:
            reason = await self._async_ungrounded_reason(agent_id, user_input)
            if reason is not None:
                _LOGGER.info(
                    "Conversation Router: %s would mis-handle %r (%s), sending to %s instead",
                    agent_id,
                    user_input.text,
                    reason,
                    fallback,
                )
                return await self._async_forward(
                    fallback, user_input, user_input.conversation_id
                )

        _LOGGER.debug("Conversation Router routing to %s", agent_id)
        result = await self._async_forward(agent_id, user_input, user_input.conversation_id)

        if not can_fall_back:
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

    async def _async_ungrounded_reason(
        self, agent_id: str, user_input: conversation.ConversationInput
    ) -> str | None:
        """Pre-check a Sophia NLU agent's match; a reason string means "don't send it there".

        Only applies when agent_id is a sophia_nlu conversation entity. Any
        failure of the check itself lets the request through unchanged.
        """
        ent_reg = er.async_get(self.hass)
        agent_entry = ent_reg.async_get(agent_id)
        if agent_entry is None or agent_entry.platform != "sophia_nlu":
            return None
        config_entry = self.hass.config_entries.async_get_entry(agent_entry.config_entry_id)
        if config_entry is None:
            return None
        conf = {**config_entry.data, **config_entry.options}
        host, port = conf.get("host"), conf.get("port")
        if not host or not port:
            return None

        # A reply inside an ongoing Sophia exchange ("which light?" -> "the
        # kitchen one") is meaningless out of context, so leave it alone.
        if user_input.conversation_id:
            component = self.hass.data.get("entity_components", {}).get("conversation")
            agent = component.get_entity(agent_id) if component else None
            is_reply = getattr(agent, "_is_reply", None)
            try:
                if callable(is_reply) and is_reply(user_input.conversation_id):
                    return None
            except Exception:  # noqa: BLE001 - private API; never block on it
                pass

        try:
            results = await grounding.async_recognize(
                host, int(port), user_input.text, user_input.language
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Conversation Router: grounding pre-check skipped (%s)", err)
            return None

        # Sophia already says she won't understand: go straight to the fallback.
        # Forwarding anyway would make her recognize the text a second time, and
        # her matching isn't deterministic (a re-run can bind a device).
        for text in grounding.respond_texts(results):
            for pattern in self._fallback_patterns:
                if pattern.search(text):
                    return f"pre-check reply {text!r} matched {pattern.pattern!r}"

        try:
            for intent_name, entity_id, slots, params in grounding.matched_targets(results):
                vocab = self._target_vocab(entity_id, slots)
                left = grounding.unexplained_words(
                    user_input.text, vocab, params, self._grounding_extra
                )
                if left:
                    target = entity_id or ", ".join(
                        f"{k}={v}" for k, v in slots.items() if k != "common_name"
                    )
                    return f"{intent_name} on {target} leaves {', '.join(left)} unexplained"
        except Exception:  # noqa: BLE001 - a broken check must not break the assistant
            _LOGGER.exception("Conversation Router: grounding check failed; not applied")
        return None

    def _target_vocab(self, entity_id: str | None, slots: dict[str, str]) -> set[str]:
        """Words that can refer to the match: the entity, else its name/domain/area/floor slots.

        common_name is never used: it is the phrase Sophia lifted from the
        sentence, so it would "explain" exactly the words being questioned.
        """
        if entity_id:
            vocab = self._entity_vocab(entity_id)
        else:
            vocab = grounding.words(slots.get("name"))
            if domain := slots.get("domain"):
                vocab |= {domain} | set(grounding.DOMAIN_WORDS.get(domain, ()))
        area_reg = ar.async_get(self.hass)
        if area_name := slots.get("area"):
            vocab |= grounding.words(area_name)
            if area := area_reg.async_get_area_by_name(area_name):
                for alias in area.aliases or ():
                    vocab |= grounding.words(alias)
        vocab |= grounding.words(slots.get("floor"))
        return vocab

    def _entity_vocab(self, entity_id: str) -> set[str]:
        """Every word that can legitimately refer to entity_id."""
        domain, object_id = entity_id.split(".", 1)
        vocab = grounding.words(object_id.replace("_", " ")) | {domain}
        vocab |= set(grounding.DOMAIN_WORDS.get(domain, ()))
        if (state := self.hass.states.get(entity_id)) is not None:
            vocab |= grounding.words(state.name)

        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is None:
            return vocab
        vocab |= grounding.words(entry.name) | grounding.words(entry.original_name)
        for alias in entry.aliases or ():
            vocab |= grounding.words(alias)

        area_ids = {entry.area_id}
        if entry.device_id and (device := dr.async_get(self.hass).async_get(entry.device_id)):
            vocab |= grounding.words(device.name_by_user) | grounding.words(device.name)
            area_ids.add(device.area_id)
        area_reg = ar.async_get(self.hass)
        for area_id in area_ids - {None}:
            if area := area_reg.async_get_area(area_id):
                vocab |= grounding.words(area.name)
                for alias in area.aliases or ():
                    vocab |= grounding.words(alias)
        return vocab

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
