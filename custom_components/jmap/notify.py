"""Notify platform: one entity per JMAP account, plus the legacy notify service."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.notify import (
    ATTR_DATA,
    ATTR_TARGET,
    ATTR_TITLE,
    BaseNotificationService,
    NotifyEntity,
    NotifyEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_ATTACHMENTS,
    ATTR_BCC,
    ATTR_CC,
    ATTR_FROM,
    ATTR_HEADERS,
    ATTR_HTML,
    ATTR_REPLY_TO,
    CONF_FROM_NAME,
    CONF_IDENTITY_ID,
    DOMAIN,
)
from .coordinator import JMAPCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JMAPCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([JMAPNotifyEntity(coordinator)])


async def async_get_service(
    hass: HomeAssistant,
    config: dict[str, Any],
    discovery_info: dict[str, Any] | None = None,
) -> BaseNotificationService | None:
    """Legacy YAML notify service shim — discouraged but supported for back-compat."""
    accounts = hass.data.get(DOMAIN, {})
    if not accounts:
        return None
    coordinator = next(iter(accounts.values()))
    return JMAPLegacyNotifyService(coordinator)


class JMAPNotifyEntity(NotifyEntity):
    """Modern notify entity (per-account)."""

    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(self, coordinator: JMAPCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_name = f"{coordinator.entry.title} email"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_notify"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.entry.entry_id)},
        }

    async def async_send_message(
        self, message: str, title: str | None = None, **kwargs: Any
    ) -> None:
        data = kwargs.get(ATTR_DATA) or {}
        targets = kwargs.get(ATTR_TARGET) or data.get("to")
        if isinstance(targets, str):
            targets = [targets]
        if not targets:
            raise ValueError(
                "JMAP notify requires at least one recipient (set 'target' or data.to)"
            )

        entry = self._coordinator.entry
        identities = await self._coordinator.client.list_identities()
        identity_id = (
            data.get(CONF_IDENTITY_ID)
            or entry.options.get(CONF_IDENTITY_ID)
            or entry.data.get(CONF_IDENTITY_ID)
            or (identities[0]["id"] if identities else None)
        )
        if identity_id is None:
            raise ValueError("Account has no identities for sending")

        from_name = entry.options.get(CONF_FROM_NAME) or entry.data.get(CONF_FROM_NAME)
        from_addr = None
        if data.get(ATTR_FROM) or from_name:
            ident = next(
                (i for i in identities if i["id"] == identity_id),
                identities[0] if identities else None,
            )
            if ident is not None:
                from_addr = {
                    "email": data.get(ATTR_FROM) or ident["email"],
                    "name": from_name or ident.get("name") or "",
                }

        await self._coordinator.client.send_email(
            identity_id=identity_id,
            to=targets,
            subject=title or kwargs.get(ATTR_TITLE) or data.get("subject") or "(no subject)",
            text=message,
            html=data.get(ATTR_HTML),
            cc=data.get(ATTR_CC),
            bcc=data.get(ATTR_BCC),
            reply_to=data.get(ATTR_REPLY_TO),
            from_address=from_addr,
            attachments=data.get(ATTR_ATTACHMENTS),
            headers=data.get(ATTR_HEADERS),
        )


class JMAPLegacyNotifyService(BaseNotificationService):
    """Old-style notify service kept around for users with existing YAML."""

    def __init__(self, coordinator: JMAPCoordinator) -> None:
        self._coordinator = coordinator

    async def async_send_message(self, message: str = "", **kwargs: Any) -> None:
        entity = JMAPNotifyEntity(self._coordinator)
        entity.hass = self._coordinator.hass
        await entity.async_send_message(message, **kwargs)
