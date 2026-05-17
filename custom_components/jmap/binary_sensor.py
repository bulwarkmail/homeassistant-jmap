"""Binary sensor: 'has unread' per account, plus per-mailbox-has-unread."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MONITORED_MAILBOXES, DOMAIN, ROLE_INBOX
from .coordinator import JMAPCoordinator
from .jmap_client import Mailbox, mailbox_path


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JMAPCoordinator = hass.data[DOMAIN][entry.entry_id]
    monitored = set(entry.options.get(CONF_MONITORED_MAILBOXES) or [])
    entities: list[BinarySensorEntity] = [AccountHasUnreadBinary(coordinator)]
    for mb in coordinator.data["mailboxes"].values():
        if mb.role == ROLE_INBOX or mb.id in monitored:
            entities.append(MailboxHasUnreadBinary(coordinator, mb.id))
    async_add_entities(entities)


class _Base(CoordinatorEntity[JMAPCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:email-mark-as-unread"

    def __init__(self, coordinator: JMAPCoordinator) -> None:
        super().__init__(coordinator)
        self._entry_id = coordinator.entry.entry_id


class AccountHasUnreadBinary(_Base):
    def __init__(self, coordinator: JMAPCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._entry_id}_account_unread"
        self._attr_translation_key = "account_unread"
        self._attr_name = "Has unread"

    @property
    def device_info(self) -> dict[str, Any]:
        return {"identifiers": {(DOMAIN, self._entry_id)}}

    @property
    def is_on(self) -> bool:
        boxes: dict[str, Mailbox] = (self.coordinator.data or {}).get("mailboxes", {})
        return any(mb.unread_emails > 0 for mb in boxes.values())


class MailboxHasUnreadBinary(_Base):
    def __init__(self, coordinator: JMAPCoordinator, mailbox_id: str) -> None:
        super().__init__(coordinator)
        self._mailbox_id = mailbox_id
        self._attr_unique_id = f"{self._entry_id}_{mailbox_id}_has_unread"
        self._attr_translation_key = "mailbox_has_unread"

    @property
    def _mailbox(self) -> Mailbox | None:
        return (self.coordinator.data or {}).get("mailboxes", {}).get(self._mailbox_id)

    @property
    def device_info(self) -> dict[str, Any]:
        return {"identifiers": {(DOMAIN, f"{self._entry_id}:{self._mailbox_id}")}}

    @property
    def name(self) -> str:
        mb = self._mailbox
        if mb is None:
            return "Has unread"
        path = mailbox_path(mb, (self.coordinator.data or {}).get("mailboxes", {}))
        return f"{path} has unread"

    @property
    def is_on(self) -> bool:
        mb = self._mailbox
        return bool(mb and mb.unread_emails > 0)
