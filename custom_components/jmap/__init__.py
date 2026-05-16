"""The JMAP Mail integration."""
from __future__ import annotations

import logging
import os
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntry, DeviceEntryType, async_get as async_get_device_registry

from .const import (
    ATTR_ATTACHMENTS,
    ATTR_BCC,
    ATTR_BODY,
    ATTR_CC,
    ATTR_EMAIL_ID,
    ATTR_FROM,
    ATTR_HEADERS,
    ATTR_HTML,
    ATTR_LIMIT,
    ATTR_MAILBOX_ID,
    ATTR_MAILBOX_NAME,
    ATTR_QUERY,
    ATTR_REPLY_TO,
    ATTR_SUBJECT,
    ATTR_TO,
    CONF_FROM_NAME,
    CONF_IDENTITY_ID,
    CONF_PASSWORD,
    CONF_SERVER_URL,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    SERVICE_ARCHIVE,
    SERVICE_DELETE,
    SERVICE_FLAG,
    SERVICE_MARK_READ,
    SERVICE_MARK_UNREAD,
    SERVICE_MOVE,
    SERVICE_SEARCH,
    SERVICE_SEND_EMAIL,
    SERVICE_UNFLAG,
)
from .coordinator import ConfigEntryAuthFailedShim, JMAPCoordinator
from .jmap_client import JMAPAuthError, JMAPClient, JMAPError

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NOTIFY]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up JMAP from a config entry."""
    http = async_get_clientsession(
        hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    )
    client = JMAPClient(
        http,
        server_url=entry.data[CONF_SERVER_URL],
        token=entry.data.get(CONF_TOKEN),
        username=entry.data.get(CONF_USERNAME),
        password=entry.data.get(CONF_PASSWORD),
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )
    coordinator = JMAPCoordinator(hass, entry, client)

    try:
        await coordinator.async_setup()
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailedShim as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except JMAPAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except JMAPError as err:
        raise ConfigEntryNotReady(str(err)) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await _register_devices(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator: JMAPCoordinator | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id, None
    )
    if coordinator is not None:
        await coordinator.async_shutdown()
    if not hass.data.get(DOMAIN):
        for service in (
            SERVICE_SEND_EMAIL,
            SERVICE_MARK_READ,
            SERVICE_MARK_UNREAD,
            SERVICE_ARCHIVE,
            SERVICE_DELETE,
            SERVICE_MOVE,
            SERVICE_FLAG,
            SERVICE_UNFLAG,
            SERVICE_SEARCH,
        ):
            hass.services.async_remove(DOMAIN, service)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def _register_devices(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: JMAPCoordinator
) -> None:
    """Create one HA device per JMAP mailbox so they group naturally in the UI."""
    registry = async_get_device_registry(hass)
    server_url = entry.data[CONF_SERVER_URL]
    account_device: DeviceEntry = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="JMAP",
        model="Mail account",
        configuration_url=server_url,
        entry_type=DeviceEntryType.SERVICE,
    )
    for mb in coordinator.data["mailboxes"].values():
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{entry.entry_id}:{mb.id}")},
            via_device=(DOMAIN, entry.entry_id),
            name=f"{entry.title} · {mb.name}",
            manufacturer="JMAP",
            model=f"Mailbox ({mb.role or 'folder'})",
            entry_type=DeviceEntryType.SERVICE,
        )


def _resolve_coordinator(hass: HomeAssistant, account: str | None) -> JMAPCoordinator:
    entries: dict[str, JMAPCoordinator] = hass.data.get(DOMAIN, {})
    if not entries:
        raise vol.Invalid("No JMAP accounts configured")
    if account is None:
        if len(entries) == 1:
            return next(iter(entries.values()))
        raise vol.Invalid(
            "Multiple JMAP accounts configured; pass 'account' to disambiguate"
        )
    for coord in entries.values():
        if coord.entry.title == account or coord.entry.entry_id == account:
            return coord
    raise vol.Invalid(f"No JMAP account matching '{account}'")


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's services; idempotent across entries."""

    def coordinator_for(call: ServiceCall) -> JMAPCoordinator:
        return _resolve_coordinator(hass, call.data.get("account"))

    async def send_email(call: ServiceCall) -> ServiceResponse:
        coord = coordinator_for(call)
        identities = await coord.client.list_identities()
        if not identities:
            raise vol.Invalid("Server returned no identities")
        identity_id = (
            call.data.get(CONF_IDENTITY_ID)
            or coord.entry.options.get(CONF_IDENTITY_ID)
            or coord.entry.data.get(CONF_IDENTITY_ID)
            or identities[0]["id"]
        )
        from_addr = None
        from_name = coord.entry.options.get(CONF_FROM_NAME) or coord.entry.data.get(
            CONF_FROM_NAME
        )
        if call.data.get(ATTR_FROM) or from_name:
            ident = next((i for i in identities if i["id"] == identity_id), identities[0])
            from_addr = {
                "email": call.data.get(ATTR_FROM) or ident["email"],
                "name": from_name or ident.get("name") or "",
            }
        attachments = call.data.get(ATTR_ATTACHMENTS) or []
        attachments = [
            p for p in attachments if _attachment_path_is_allowed(hass, p)
        ]
        result = await coord.client.send_email(
            identity_id=identity_id,
            to=call.data[ATTR_TO],
            subject=call.data[ATTR_SUBJECT],
            text=call.data.get(ATTR_BODY),
            html=call.data.get(ATTR_HTML),
            cc=call.data.get(ATTR_CC),
            bcc=call.data.get(ATTR_BCC),
            reply_to=call.data.get(ATTR_REPLY_TO),
            from_address=from_addr,
            attachments=attachments,
            headers=call.data.get(ATTR_HEADERS),
        )
        return {"result": result}

    async def mark_read(call: ServiceCall) -> None:
        coord = coordinator_for(call)
        await coord.client.mark_read(call.data[ATTR_EMAIL_ID], True)
        await coord.async_request_refresh()

    async def mark_unread(call: ServiceCall) -> None:
        coord = coordinator_for(call)
        await coord.client.mark_read(call.data[ATTR_EMAIL_ID], False)
        await coord.async_request_refresh()

    async def archive(call: ServiceCall) -> None:
        coord = coordinator_for(call)
        await coord.client.archive(call.data[ATTR_EMAIL_ID])
        await coord.async_request_refresh()

    async def delete(call: ServiceCall) -> None:
        coord = coordinator_for(call)
        await coord.client.delete(call.data[ATTR_EMAIL_ID])
        await coord.async_request_refresh()

    async def move(call: ServiceCall) -> None:
        coord = coordinator_for(call)
        mb_id = call.data.get(ATTR_MAILBOX_ID)
        if mb_id is None:
            name = call.data.get(ATTR_MAILBOX_NAME)
            if name is None:
                raise vol.Invalid("mailbox_id or mailbox_name is required")
            mb = await coord.client.find_mailbox_by_name(name)
            if mb is None:
                raise vol.Invalid(f"No mailbox named '{name}'")
            mb_id = mb.id
        await coord.client.move(call.data[ATTR_EMAIL_ID], mb_id)
        await coord.async_request_refresh()

    async def flag(call: ServiceCall) -> None:
        coord = coordinator_for(call)
        await coord.client.flag(call.data[ATTR_EMAIL_ID], True)
        await coord.async_request_refresh()

    async def unflag(call: ServiceCall) -> None:
        coord = coordinator_for(call)
        await coord.client.flag(call.data[ATTR_EMAIL_ID], False)
        await coord.async_request_refresh()

    async def search(call: ServiceCall) -> ServiceResponse:
        coord = coordinator_for(call)
        emails = await coord.client.query_emails(
            text=call.data.get(ATTR_QUERY),
            from_address=call.data.get(ATTR_FROM),
            mailbox_id=call.data.get(ATTR_MAILBOX_ID),
            limit=int(call.data.get(ATTR_LIMIT, 20)),
        )
        return {"emails": [e.to_event_payload() for e in emails]}

    base = {vol.Optional("account"): cv.string}

    schemas: dict[str, tuple[Any, Any, SupportsResponse]] = {
        SERVICE_SEND_EMAIL: (
            vol.Schema(
                {
                    **base,
                    vol.Required(ATTR_TO): vol.All(cv.ensure_list, [cv.string]),
                    vol.Required(ATTR_SUBJECT): cv.string,
                    vol.Optional(ATTR_BODY): cv.string,
                    vol.Optional(ATTR_HTML): cv.string,
                    vol.Optional(ATTR_CC): vol.All(cv.ensure_list, [cv.string]),
                    vol.Optional(ATTR_BCC): vol.All(cv.ensure_list, [cv.string]),
                    vol.Optional(ATTR_REPLY_TO): vol.All(cv.ensure_list, [cv.string]),
                    vol.Optional(ATTR_FROM): cv.string,
                    vol.Optional(ATTR_ATTACHMENTS): vol.All(cv.ensure_list, [cv.string]),
                    vol.Optional(ATTR_HEADERS): {cv.string: cv.string},
                    vol.Optional(CONF_IDENTITY_ID): cv.string,
                }
            ),
            send_email,
            SupportsResponse.OPTIONAL,
        ),
        SERVICE_MARK_READ: (
            vol.Schema({**base, vol.Required(ATTR_EMAIL_ID): cv.string}),
            mark_read,
            SupportsResponse.NONE,
        ),
        SERVICE_MARK_UNREAD: (
            vol.Schema({**base, vol.Required(ATTR_EMAIL_ID): cv.string}),
            mark_unread,
            SupportsResponse.NONE,
        ),
        SERVICE_ARCHIVE: (
            vol.Schema({**base, vol.Required(ATTR_EMAIL_ID): cv.string}),
            archive,
            SupportsResponse.NONE,
        ),
        SERVICE_DELETE: (
            vol.Schema({**base, vol.Required(ATTR_EMAIL_ID): cv.string}),
            delete,
            SupportsResponse.NONE,
        ),
        SERVICE_MOVE: (
            vol.Schema(
                {
                    **base,
                    vol.Required(ATTR_EMAIL_ID): cv.string,
                    vol.Optional(ATTR_MAILBOX_ID): cv.string,
                    vol.Optional(ATTR_MAILBOX_NAME): cv.string,
                }
            ),
            move,
            SupportsResponse.NONE,
        ),
        SERVICE_FLAG: (
            vol.Schema({**base, vol.Required(ATTR_EMAIL_ID): cv.string}),
            flag,
            SupportsResponse.NONE,
        ),
        SERVICE_UNFLAG: (
            vol.Schema({**base, vol.Required(ATTR_EMAIL_ID): cv.string}),
            unflag,
            SupportsResponse.NONE,
        ),
        SERVICE_SEARCH: (
            vol.Schema(
                {
                    **base,
                    vol.Optional(ATTR_QUERY): cv.string,
                    vol.Optional(ATTR_FROM): cv.string,
                    vol.Optional(ATTR_MAILBOX_ID): cv.string,
                    vol.Optional(ATTR_LIMIT, default=20): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=200)
                    ),
                }
            ),
            search,
            SupportsResponse.ONLY,
        ),
    }

    for name, (schema, handler, supports) in schemas.items():
        if hass.services.has_service(DOMAIN, name):
            continue
        hass.services.async_register(
            DOMAIN, name, handler, schema=schema, supports_response=supports
        )


def _attachment_path_is_allowed(hass: HomeAssistant, path: str) -> bool:
    """Only allow attachments inside the HA config dir or explicitly allowed dirs."""
    real = os.path.realpath(path)
    if real.startswith(os.path.realpath(hass.config.path())):
        return True
    return any(real.startswith(os.path.realpath(p)) for p in hass.config.allowlist_external_dirs)
