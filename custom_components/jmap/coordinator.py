"""Data coordinator for JMAP, supporting push (EventSource) and poll fallback."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_POLL_INTERVAL,
    CONF_USE_PUSH,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_USE_PUSH,
    DOMAIN,
    EVENT_EMAIL_DELETED,
    EVENT_MAILBOX_CHANGED,
    EVENT_NEW_EMAIL,
)
from .jmap_client import (
    Email,
    JMAPAuthError,
    JMAPClient,
    JMAPError,
    JMAPMethodError,
    Mailbox,
)

_LOGGER = logging.getLogger(__name__)


class JMAPCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that tracks mailbox state and emits new-email events."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: JMAPClient,
    ) -> None:
        self.entry = entry
        self.client = client
        options = {**entry.data, **entry.options}
        poll_seconds = int(options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
        self._use_push = bool(options.get(CONF_USE_PUSH, DEFAULT_USE_PUSH))
        self._email_state: str | None = None
        self._push_task: asyncio.Task[None] | None = None
        self._push_wake: asyncio.Event = asyncio.Event()
        self._known_email_ids: set[str] = set()
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=poll_seconds),
        )

    async def async_setup(self) -> None:
        """Authenticate, fetch initial mailbox state, prime the email-id baseline."""
        try:
            await self.client.connect()
            mailboxes = await self.client.list_mailboxes(force_refresh=True)
            self._email_state = await self.client.get_state()
            inbox = next(
                (mb for mb in mailboxes.values() if mb.role == "inbox"), None
            )
            if inbox is not None:
                latest = await self.client.query_emails(
                    mailbox_id=inbox.id, limit=50
                )
                self._known_email_ids = {e.id for e in latest}
        except JMAPAuthError as err:
            raise ConfigEntryAuthFailedShim(str(err)) from err
        except JMAPError as err:
            raise UpdateFailed(str(err)) from err
        if self._use_push:
            self._push_task = self.hass.loop.create_task(self._run_push_loop())

    async def async_shutdown(self) -> None:
        if self._push_task is not None:
            self._push_task.cancel()
            try:
                await self._push_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._push_task = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Single tick: refresh mailbox counts and detect new emails."""
        try:
            mailboxes = await self.client.list_mailboxes(force_refresh=True)
            new_emails: list[Email] = []
            if self._email_state is not None:
                try:
                    new_state, created, _updated = await self.client.email_changes(
                        self._email_state
                    )
                    self._email_state = new_state
                    if created:
                        fetched = await self.client.get_emails(created)
                        for email in fetched:
                            if email.id in self._known_email_ids:
                                continue
                            self._known_email_ids.add(email.id)
                            new_emails.append(email)
                except JMAPMethodError as err:
                    if err.error_type == "cannotCalculateChanges":
                        _LOGGER.debug(
                            "JMAP state expired, resyncing from latest emails"
                        )
                        self._email_state = await self.client.get_state()
                    else:
                        raise
            else:
                self._email_state = await self.client.get_state()

            self._fire_events(new_emails, mailboxes)

            latest_unread: list[Email] = []
            inbox = next(
                (mb for mb in mailboxes.values() if mb.role == "inbox"), None
            )
            if inbox is not None:
                latest_unread = await self.client.query_emails(
                    mailbox_id=inbox.id, unread_only=True, limit=10
                )

            return {
                "mailboxes": mailboxes,
                "latest_unread": latest_unread,
                "state": self._email_state,
            }
        except JMAPAuthError as err:
            raise ConfigEntryAuthFailedShim(str(err)) from err
        except JMAPError as err:
            raise UpdateFailed(str(err)) from err

    def _fire_events(
        self, new_emails: list[Email], mailboxes: dict[str, Mailbox]
    ) -> None:
        for email in new_emails:
            payload = {
                "account": self.entry.title,
                "entry_id": self.entry.entry_id,
                **email.to_event_payload(),
            }
            mailbox_names: list[str] = []
            for mb_id in email.mailbox_ids:
                mb = mailboxes.get(mb_id)
                if mb is not None:
                    mailbox_names.append(mb.name)
            payload["mailbox_names"] = mailbox_names
            self.hass.bus.async_fire(EVENT_NEW_EMAIL, payload)

        if self.data is not None:
            prev_boxes: dict[str, Mailbox] = self.data.get("mailboxes", {}) or {}
            for mb_id, mb in mailboxes.items():
                prev = prev_boxes.get(mb_id)
                if prev is None:
                    continue
                if (
                    prev.unread_emails != mb.unread_emails
                    or prev.total_emails != mb.total_emails
                ):
                    self.hass.bus.async_fire(
                        EVENT_MAILBOX_CHANGED,
                        {
                            "account": self.entry.title,
                            "entry_id": self.entry.entry_id,
                            "mailbox_id": mb.id,
                            "mailbox_name": mb.name,
                            "role": mb.role,
                            "unread": mb.unread_emails,
                            "total": mb.total_emails,
                            "previous_unread": prev.unread_emails,
                            "previous_total": prev.total_emails,
                        },
                    )

    async def _run_push_loop(self) -> None:
        """Drive an EventSource subscription that nudges the coordinator on change."""
        try:
            async for payload in self.client.event_source():
                changed = payload.get("changed") or {}
                if not changed:
                    continue
                _LOGGER.debug("JMAP push event: %s", changed)
                await self.async_request_refresh()
        except asyncio.CancelledError:
            raise
        except JMAPAuthError as err:
            _LOGGER.error("JMAP push auth failed, falling back to polling: %s", err)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("JMAP push loop terminated: %s", err)


class ConfigEntryAuthFailedShim(UpdateFailed):
    """Marker subclass so we can detect auth failures without importing from HA core in tests."""
