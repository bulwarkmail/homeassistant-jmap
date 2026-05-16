"""Config flow for JMAP."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_FROM_NAME,
    CONF_IDENTITY_ID,
    CONF_MONITORED_MAILBOXES,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_SERVER_URL,
    CONF_TOKEN,
    CONF_USE_PUSH,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_USE_PUSH,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .jmap_client import JMAPAuthError, JMAPClient, JMAPError

_LOGGER = logging.getLogger(__name__)


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_SERVER_URL,
                default=defaults.get(CONF_SERVER_URL, "https://mail.example.com"),
            ): str,
            vol.Optional(CONF_TOKEN, default=defaults.get(CONF_TOKEN, "")): str,
            vol.Optional(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            vol.Optional(CONF_PASSWORD, default=""): str,
            vol.Optional(
                CONF_VERIFY_SSL,
                default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ): bool,
        }
    )


class JMAPConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for JMAP."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: config_entries.ConfigEntry | None = None
        self._discovered: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            normalized = _normalize_credentials(user_input)
            try:
                session_info = await _probe(self.hass, normalized)
            except JMAPAuthError:
                errors["base"] = "invalid_auth"
            except JMAPError as err:
                _LOGGER.warning("JMAP probe failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                username = session_info["username"]
                await self.async_set_unique_id(
                    f"{normalized[CONF_SERVER_URL]}::{username}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=username or normalized[CONF_SERVER_URL],
                    data={
                        CONF_SERVER_URL: normalized[CONF_SERVER_URL],
                        CONF_TOKEN: normalized.get(CONF_TOKEN),
                        CONF_USERNAME: normalized.get(CONF_USERNAME),
                        CONF_PASSWORD: normalized.get(CONF_PASSWORD),
                        CONF_VERIFY_SSL: normalized[CONF_VERIFY_SSL],
                        CONF_IDENTITY_ID: session_info.get("identity_id"),
                    },
                    options={
                        CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
                        CONF_USE_PUSH: DEFAULT_USE_PUSH,
                    },
                )

        form_defaults = None
        if user_input is not None:
            form_defaults = {k: v for k, v in user_input.items() if k != CONF_PASSWORD}
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(form_defaults),
            errors=errors,
            description_placeholders={
                "supported": "Stalwart, Fastmail (via app password), Cyrus, Apache James"
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._reauth_entry is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            merged = {**self._reauth_entry.data, **user_input}
            try:
                await _probe(self.hass, merged)
            except JMAPAuthError:
                errors["base"] = "invalid_auth"
            except JMAPError:
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry, data=merged
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_TOKEN): str,
                    vol.Optional(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlow:
        return JMAPOptionsFlow(config_entry)


class JMAPOptionsFlow(OptionsFlow):
    """Options flow: poll interval, push toggle, monitored mailboxes, identity, from-name."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._mailboxes: list[tuple[str, str]] = []
        self._identities: list[tuple[str, str]] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Best-effort: enumerate mailboxes + identities for nicer selectors.
        try:
            http = async_get_clientsession(self.hass)
            client = JMAPClient(
                http,
                server_url=self._entry.data[CONF_SERVER_URL],
                token=self._entry.data.get(CONF_TOKEN),
                username=self._entry.data.get(CONF_USERNAME),
                password=self._entry.data.get(CONF_PASSWORD),
                verify_ssl=self._entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            )
            await client.connect()
            mailboxes = await client.list_mailboxes()
            self._mailboxes = sorted(
                [(mb.id, mb.name) for mb in mailboxes.values()], key=lambda x: x[1]
            )
            identities = await client.list_identities()
            self._identities = [(i["id"], f"{i.get('name','')} <{i['email']}>") for i in identities]
        except JMAPError:
            self._mailboxes = []
            self._identities = []

        opts = {**self._entry.options}
        schema_dict: dict[Any, Any] = {
            vol.Optional(
                CONF_POLL_INTERVAL,
                default=opts.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=15, max=3600)),
            vol.Optional(
                CONF_USE_PUSH, default=opts.get(CONF_USE_PUSH, DEFAULT_USE_PUSH)
            ): bool,
            vol.Optional(
                CONF_FROM_NAME, default=opts.get(CONF_FROM_NAME, "")
            ): str,
        }
        if self._identities:
            schema_dict[
                vol.Optional(
                    CONF_IDENTITY_ID,
                    default=opts.get(CONF_IDENTITY_ID) or self._identities[0][0],
                )
            ] = vol.In({i[0]: i[1] for i in self._identities})
        if self._mailboxes:
            schema_dict[
                vol.Optional(
                    CONF_MONITORED_MAILBOXES,
                    default=opts.get(CONF_MONITORED_MAILBOXES, []),
                )
            ] = vol.All(
                cv_ensure_list,
                [vol.In({m[0]: m[1] for m in self._mailboxes})],
            )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))


def cv_ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_credentials(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    if not out.get(CONF_TOKEN):
        out[CONF_TOKEN] = None
    if not out.get(CONF_USERNAME):
        out[CONF_USERNAME] = None
        out[CONF_PASSWORD] = None
    out.setdefault(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    return out


async def _probe(hass, creds: dict[str, Any]) -> dict[str, Any]:
    """Validate credentials by connecting to the server. Returns session metadata."""
    http = async_get_clientsession(hass, verify_ssl=creds[CONF_VERIFY_SSL])
    client = JMAPClient(
        http,
        server_url=creds[CONF_SERVER_URL],
        token=creds.get(CONF_TOKEN),
        username=creds.get(CONF_USERNAME),
        password=creds.get(CONF_PASSWORD),
        verify_ssl=creds[CONF_VERIFY_SSL],
    )
    session = await client.connect()
    identities = await client.list_identities()
    return {
        "username": session.username,
        "primary_account_id": session.primary_account_id,
        "identity_id": identities[0]["id"] if identities else None,
    }
