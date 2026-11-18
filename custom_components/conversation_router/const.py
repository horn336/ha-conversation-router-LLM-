"""Constants for Conversation Router."""

DOMAIN = "conversation_router"

CONF_DEFAULT_AGENT = "default_agent"
CONF_RULES = "rules"
CONF_CONDITION = "condition"
CONF_AGENT = "agent"

MAX_RULES = 3

# Fallback-on-unrecognized mode: when the routed agent answers with an error
# response or with a "didn't understand" phrase, the same input is handed to
# the fallback agent instead.
CONF_FALLBACK_AGENT = "fallback_agent"
CONF_FALLBACK_ON_ERROR = "fallback_on_error"
CONF_FALLBACK_PATTERNS = "fallback_patterns"

# One regular expression per line, matched case-insensitively anywhere in the
# routed agent's spoken reply. These cover every "no match" reply observed
# from Sophia NLU, including its device-not-found replies (its replies always
# carry response_type action_done, so the wording is the only signal), plus
# Sophia's own connection/timeout errors.
DEFAULT_FALLBACK_PATTERNS = "\n".join(
    [
        r"didn'?t understand",
        r"don'?t understand",
        r"didn'?t get that",
        r"could you rephrase",
        r"say that differently",
        r"not sure what you mean",
        r"didn'?t make sense",
        r"couldn'?t recognize",
        r"don'?t see that (entity|device)",
        r"unable to find the (entity|device)",
        r"took too long to respond",
        r"could not connect to the nlu",
        r"error communicating with nlu",
    ]
)
