"""Diagnostics support for JMAP — redacts credentials."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
    DOMAIN,
)
from .coordinator import JMAPCoordinator

REDACT = {CONF_TOKEN, CONF_PASSWORD, CONF_USERNAME, "from", "to", "cc", "bcc", "preview", "subject"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: JMAPCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    out: dict[str, Any] = {
        "entry": {
            "data": async_redact_data(entry.data, REDACT),
            "options": entry.options,
            "title": entry.title,
        }
    }
    if coordinator is not None:
        sess = coordinator.client.session
        out["session"] = {
            "primary_account_id": sess.primary_account_id,
            "has_submission": sess.submission_account_id is not None,
            "event_source_url": bool(sess.event_source_url),
            "capabilities": list(sess.capabilities.keys()),
        }
        data = coordinator.data or {}
        out["mailboxes"] = [
            {
                "id": mb.id,
                "name": mb.name,
                "role": mb.role,
                "total": mb.total_emails,
                "unread": mb.unread_emails,
            }
            for mb in (data.get("mailboxes") or {}).values()
        ]
        out["latest_unread_count"] = len(data.get("latest_unread") or [])
        out["state"] = data.get("state")
    return out
