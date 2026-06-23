# Brand icon

This mirrors the layout expected by the official
[home-assistant/brands](https://github.com/home-assistant/brands) repository,
which is what actually serves icons to the Home Assistant frontend (HACS
does not host or serve icons itself — it just links to the same brands CDN
core integrations use).

To make the icon show up inside Home Assistant:

1. Fork `https://github.com/home-assistant/brands`.
2. Copy this `custom_integrations/conversation_router/` folder into the fork
   at the same path.
3. Open a pull request. Once merged, `brands.home-assistant.io` serves the
   icon automatically for any HA instance with this integration installed —
   no further action needed on the user's end.

Until that PR is merged, Home Assistant will show a generic placeholder
("icon not available") for this integration, which is purely cosmetic and
doesn't affect functionality.
