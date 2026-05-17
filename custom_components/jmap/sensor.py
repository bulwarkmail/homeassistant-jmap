"""Sensor entities for JMAP: unread/total per mailbox + latest-email summary."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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

    entities: list[SensorEntity] = [
        LatestSenderSensor(coordinator),
        LatestSubjectSensor(coordinator),
    ]
    for mb in coordinator.data["mailboxes"].values():
        if mb.role == ROLE_INBOX or mb.id in monitored:
            entities.append(MailboxUnreadSensor(coordinator, mb.id))
            entities.append(MailboxTotalSensor(coordinator, mb.id))

    async_add_entities(entities)

    @callback
    def _maybe_add_new_mailboxes() -> None:
        existing_ids = {
            e.unique_id for e in entities if hasattr(e, "unique_id")
        }
        new: list[SensorEntity] = []
        for mb in coordinator.data["mailboxes"].values():
            if mb.role != ROLE_INBOX and mb.id not in monitored:
                continue
            unique_unread = f"{entry.entry_id}_{mb.id}_unread"
            unique_total = f"{entry.entry_id}_{mb.id}_total"
            if unique_unread not in existing_ids:
                new.append(MailboxUnreadSensor(coordinator, mb.id))
            if unique_total not in existing_ids:
                new.append(MailboxTotalSensor(coordinator, mb.id))
        if new:
            async_add_entities(new)
            entities.extend(new)

    entry.async_on_unload(coordinator.async_add_listener(_maybe_add_new_mailboxes))


class _JMAPSensorBase(CoordinatorEntity[JMAPCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: JMAPCoordinator) -> None:
        super().__init__(coordinator)
        self._entry_id = coordinator.entry.entry_id


class _MailboxSensorBase(_JMAPSensorBase):
    def __init__(self, coordinator: JMAPCoordinator, mailbox_id: str) -> None:
        super().__init__(coordinator)
        self._mailbox_id = mailbox_id

    @property
    def _mailbox(self) -> Mailbox | None:
        return (self.coordinator.data or {}).get("mailboxes", {}).get(self._mailbox_id)

    @property
    def _mailbox_path(self) -> str | None:
        mb = self._mailbox
        if mb is None:
            return None
        return mailbox_path(mb, (self.coordinator.data or {}).get("mailboxes", {}))

    @property
    def available(self) -> bool:
        return super().available and self._mailbox is not None

    @property
    def device_info(self) -> dict[str, Any]:
        return {"identifiers": {(DOMAIN, f"{self._entry_id}:{self._mailbox_id}")}}

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        mb = self._mailbox
        if mb is None:
            return None
        return {
            "mailbox_id": mb.id,
            "mailbox_name": mb.name,
            "mailbox_path": self._mailbox_path,
            "role": mb.role,
            "total_threads": mb.total_threads,
            "unread_threads": mb.unread_threads,
        }


class MailboxUnreadSensor(_MailboxSensorBase):
    _attr_icon = "mdi:email-mark-as-unread"
    _attr_native_unit_of_measurement = "messages"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: JMAPCoordinator, mailbox_id: str) -> None:
        super().__init__(coordinator, mailbox_id)
        self._attr_unique_id = f"{self._entry_id}_{mailbox_id}_unread"
        self._attr_translation_key = "mailbox_unread"

    @property
    def name(self) -> str:
        path = self._mailbox_path
        return f"{path} unread" if path else "Unread"

    @property
    def native_value(self) -> int | None:
        mb = self._mailbox
        return mb.unread_emails if mb is not None else None


class MailboxTotalSensor(_MailboxSensorBase):
    _attr_icon = "mdi:email-multiple-outline"
    _attr_native_unit_of_measurement = "messages"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: JMAPCoordinator, mailbox_id: str) -> None:
        super().__init__(coordinator, mailbox_id)
        self._attr_unique_id = f"{self._entry_id}_{mailbox_id}_total"
        self._attr_translation_key = "mailbox_total"

    @property
    def name(self) -> str:
        path = self._mailbox_path
        return f"{path} total" if path else "Total"

    @property
    def native_value(self) -> int | None:
        mb = self._mailbox
        return mb.total_emails if mb is not None else None


class LatestSenderSensor(_JMAPSensorBase):
    _attr_icon = "mdi:account-arrow-left"
    _attr_translation_key = "latest_sender"

    def __init__(self, coordinator: JMAPCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._entry_id}_latest_sender"
        self._attr_name = "Latest sender"

    @property
    def device_info(self) -> dict[str, Any]:
        return {"identifiers": {(DOMAIN, self._entry_id)}}

    @property
    def native_value(self) -> str | None:
        latest = (self.coordinator.data or {}).get("latest_unread") or []
        if not latest:
            return None
        email = latest[0]
        if email.from_:
            return email.from_[0].name or email.from_[0].email
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        latest = (self.coordinator.data or {}).get("latest_unread") or []
        if not latest:
            return None
        return latest[0].to_event_payload()


class LatestSubjectSensor(_JMAPSensorBase):
    _attr_icon = "mdi:email-outline"
    _attr_translation_key = "latest_subject"

    def __init__(self, coordinator: JMAPCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._entry_id}_latest_subject"
        self._attr_name = "Latest subject"

    @property
    def device_info(self) -> dict[str, Any]:
        return {"identifiers": {(DOMAIN, self._entry_id)}}

    @property
    def native_value(self) -> str | None:
        latest = (self.coordinator.data or {}).get("latest_unread") or []
        return latest[0].subject if latest else None
