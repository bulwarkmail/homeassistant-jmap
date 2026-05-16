"""Async JMAP client for Home Assistant.

Implements the subset of JMAP (RFC 8620/8621) needed for Home Assistant:
session discovery, mailbox/email reads, sending via EmailSubmission, state
tracking for incremental sync, and EventSource push.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import aiohttp
from aiohttp import ClientError, ClientResponseError, ClientTimeout

from .const import (
    EMAIL_PROPERTIES,
    JMAP_CORE_CAPABILITY,
    JMAP_MAIL_CAPABILITY,
    JMAP_SUBMISSION_CAPABILITY,
    ROLE_ARCHIVE,
    ROLE_DRAFTS,
    ROLE_SENT,
    WELL_KNOWN_PATH,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = ClientTimeout(total=30)
PUSH_TIMEOUT = ClientTimeout(total=None, sock_read=None, sock_connect=30)


def _read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


class JMAPError(Exception):
    """Base class for JMAP client errors."""


class JMAPAuthError(JMAPError):
    """Authentication failed."""


class JMAPNotFound(JMAPError):
    """Resource not found."""


class JMAPMethodError(JMAPError):
    """A JMAP method call returned an error response."""

    def __init__(self, method: str, error_type: str, description: str | None = None):
        self.method = method
        self.error_type = error_type
        self.description = description
        super().__init__(f"{method}: {error_type} ({description})")


@dataclass
class JMAPSession:
    """Parsed session resource."""

    username: str
    api_url: str
    download_url: str
    upload_url: str
    event_source_url: str
    primary_account_id: str
    submission_account_id: str | None
    capabilities: dict[str, Any] = field(default_factory=dict)
    accounts: dict[str, Any] = field(default_factory=dict)
    state: str | None = None


@dataclass
class Mailbox:
    """JMAP Mailbox object."""

    id: str
    name: str
    role: str | None
    parent_id: str | None
    total_emails: int
    unread_emails: int
    total_threads: int
    unread_threads: int
    sort_order: int = 0


@dataclass
class EmailAddress:
    """JMAP EmailAddress."""

    email: str
    name: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EmailAddress":
        return cls(email=data.get("email", ""), name=data.get("name"))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"email": self.email}
        if self.name:
            out["name"] = self.name
        return out

    def __str__(self) -> str:
        if self.name:
            return f"{self.name} <{self.email}>"
        return self.email


@dataclass
class Email:
    """Subset of the JMAP Email object useful to HA consumers."""

    id: str
    thread_id: str | None
    blob_id: str | None
    mailbox_ids: list[str]
    keywords: dict[str, bool]
    from_: list[EmailAddress]
    to: list[EmailAddress]
    cc: list[EmailAddress]
    subject: str
    received_at: str | None
    sent_at: str | None
    size: int
    preview: str
    has_attachment: bool
    message_id: list[str]

    @property
    def is_unread(self) -> bool:
        return not self.keywords.get("$seen", False)

    @property
    def is_flagged(self) -> bool:
        return bool(self.keywords.get("$flagged", False))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Email":
        return cls(
            id=data["id"],
            thread_id=data.get("threadId"),
            blob_id=data.get("blobId"),
            mailbox_ids=list((data.get("mailboxIds") or {}).keys()),
            keywords=dict(data.get("keywords") or {}),
            from_=[EmailAddress.from_dict(a) for a in (data.get("from") or [])],
            to=[EmailAddress.from_dict(a) for a in (data.get("to") or [])],
            cc=[EmailAddress.from_dict(a) for a in (data.get("cc") or [])],
            subject=data.get("subject") or "",
            received_at=data.get("receivedAt"),
            sent_at=data.get("sentAt"),
            size=int(data.get("size") or 0),
            preview=data.get("preview") or "",
            has_attachment=bool(data.get("hasAttachment")),
            message_id=list(data.get("messageId") or []),
        )

    def to_event_payload(self) -> dict[str, Any]:
        return {
            "email_id": self.id,
            "thread_id": self.thread_id,
            "mailbox_ids": self.mailbox_ids,
            "from": [a.email for a in self.from_],
            "from_name": self.from_[0].name if self.from_ else None,
            "to": [a.email for a in self.to],
            "subject": self.subject,
            "preview": self.preview,
            "received_at": self.received_at,
            "has_attachment": self.has_attachment,
            "is_unread": self.is_unread,
            "is_flagged": self.is_flagged,
        }


class JMAPClient:
    """Async JMAP client bound to a single account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        server_url: str,
        *,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self._http = session
        self._server_url = server_url.rstrip("/")
        self._token = token
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._session: JMAPSession | None = None
        self._request_counter = 0
        self._mailbox_cache: dict[str, Mailbox] | None = None
        self._identity_cache: list[dict[str, Any]] | None = None

    @property
    def session(self) -> JMAPSession:
        if self._session is None:
            raise JMAPError("Client not authenticated; call connect() first")
        return self._session

    def _auth_header(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        if self._username and self._password is not None:
            creds = f"{self._username}:{self._password}".encode()
            return {"Authorization": "Basic " + base64.b64encode(creds).decode()}
        raise JMAPAuthError("No credentials configured")

    def _session_url(self) -> str:
        if self._server_url.endswith("/.well-known/jmap"):
            return self._server_url
        if self._server_url.endswith("/jmap/session"):
            return self._server_url
        return self._server_url + WELL_KNOWN_PATH

    async def connect(self) -> JMAPSession:
        """Fetch the JMAP session resource and cache it."""
        headers = self._auth_header()
        headers["Accept"] = "application/json"
        url = self._session_url()
        try:
            async with self._http.get(
                url,
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
                ssl=self._verify_ssl,
                allow_redirects=True,
            ) as resp:
                if resp.status in (401, 403):
                    raise JMAPAuthError(f"Session fetch returned {resp.status}")
                resp.raise_for_status()
                data = await resp.json()
        except ClientResponseError as err:
            if err.status in (401, 403):
                raise JMAPAuthError(str(err)) from err
            raise JMAPError(f"Session fetch failed: {err}") from err
        except ClientError as err:
            raise JMAPError(f"Session fetch failed: {err}") from err

        primary = (data.get("primaryAccounts") or {}).get(JMAP_MAIL_CAPABILITY)
        if not primary:
            raise JMAPError("Server does not advertise a primary mail account")

        self._session = JMAPSession(
            username=data.get("username", ""),
            api_url=urljoin(url, data["apiUrl"]),
            download_url=urljoin(url, data.get("downloadUrl", "")),
            upload_url=urljoin(url, data.get("uploadUrl", "")),
            event_source_url=urljoin(url, data.get("eventSourceUrl", "")),
            primary_account_id=primary,
            submission_account_id=(data.get("primaryAccounts") or {}).get(
                JMAP_SUBMISSION_CAPABILITY
            ),
            capabilities=data.get("capabilities") or {},
            accounts=data.get("accounts") or {},
            state=data.get("state"),
        )
        return self._session

    async def invoke(
        self,
        method_calls: list[list[Any]],
        *,
        using: list[str] | None = None,
    ) -> list[list[Any]]:
        """Make a raw JMAP request and return methodResponses."""
        sess = self.session
        body = {
            "using": using
            or [JMAP_CORE_CAPABILITY, JMAP_MAIL_CAPABILITY, JMAP_SUBMISSION_CAPABILITY],
            "methodCalls": method_calls,
        }
        headers = self._auth_header()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        try:
            async with self._http.post(
                sess.api_url,
                json=body,
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
                ssl=self._verify_ssl,
            ) as resp:
                if resp.status in (401, 403):
                    raise JMAPAuthError(f"API call returned {resp.status}")
                resp.raise_for_status()
                payload = await resp.json()
        except ClientResponseError as err:
            raise JMAPError(f"API call failed: {err}") from err
        except ClientError as err:
            raise JMAPError(f"API call failed: {err}") from err

        responses = payload.get("methodResponses") or []
        for entry in responses:
            if entry and entry[0] == "error":
                err_obj = entry[1] or {}
                raise JMAPMethodError(
                    method=entry[2] if len(entry) > 2 else "?",
                    error_type=err_obj.get("type", "unknown"),
                    description=err_obj.get("description"),
                )
        return responses

    def _next_id(self) -> str:
        self._request_counter += 1
        return f"c{self._request_counter}"

    async def list_mailboxes(self, *, force_refresh: bool = False) -> dict[str, Mailbox]:
        """Return mailboxes keyed by id."""
        if self._mailbox_cache is not None and not force_refresh:
            return self._mailbox_cache
        rid = self._next_id()
        responses = await self.invoke(
            [["Mailbox/get", {"accountId": self.session.primary_account_id}, rid]]
        )
        result = responses[0][1]
        mailboxes: dict[str, Mailbox] = {}
        for mb in result.get("list") or []:
            mailboxes[mb["id"]] = Mailbox(
                id=mb["id"],
                name=mb.get("name") or "",
                role=mb.get("role"),
                parent_id=mb.get("parentId"),
                total_emails=int(mb.get("totalEmails") or 0),
                unread_emails=int(mb.get("unreadEmails") or 0),
                total_threads=int(mb.get("totalThreads") or 0),
                unread_threads=int(mb.get("unreadThreads") or 0),
                sort_order=int(mb.get("sortOrder") or 0),
            )
        self._mailbox_cache = mailboxes
        return mailboxes

    def invalidate_mailbox_cache(self) -> None:
        self._mailbox_cache = None

    async def find_mailbox_by_role(self, role: str) -> Mailbox | None:
        for mb in (await self.list_mailboxes()).values():
            if mb.role == role:
                return mb
        return None

    async def find_mailbox_by_name(self, name: str) -> Mailbox | None:
        name_lc = name.lower()
        for mb in (await self.list_mailboxes()).values():
            if mb.name.lower() == name_lc:
                return mb
        return None

    async def query_emails(
        self,
        *,
        mailbox_id: str | None = None,
        unread_only: bool = False,
        text: str | None = None,
        from_address: str | None = None,
        limit: int = 20,
        properties: list[str] | None = None,
    ) -> list[Email]:
        """Query emails and return hydrated Email objects."""
        filter_: dict[str, Any] = {}
        if mailbox_id:
            filter_["inMailbox"] = mailbox_id
        if unread_only:
            filter_["notKeyword"] = "$seen"
        if text:
            filter_["text"] = text
        if from_address:
            filter_["from"] = from_address

        query_args: dict[str, Any] = {
            "accountId": self.session.primary_account_id,
            "sort": [{"property": "receivedAt", "isAscending": False}],
            "limit": limit,
        }
        if filter_:
            query_args["filter"] = filter_

        qid = self._next_id()
        gid = self._next_id()
        responses = await self.invoke(
            [
                ["Email/query", query_args, qid],
                [
                    "Email/get",
                    {
                        "accountId": self.session.primary_account_id,
                        "#ids": {
                            "resultOf": qid,
                            "name": "Email/query",
                            "path": "/ids",
                        },
                        "properties": properties or EMAIL_PROPERTIES,
                    },
                    gid,
                ],
            ]
        )
        get_response = next(r for r in responses if r[2] == gid)
        return [Email.from_dict(e) for e in get_response[1].get("list") or []]

    async def get_emails(
        self, ids: list[str], properties: list[str] | None = None
    ) -> list[Email]:
        if not ids:
            return []
        responses = await self.invoke(
            [
                [
                    "Email/get",
                    {
                        "accountId": self.session.primary_account_id,
                        "ids": ids,
                        "properties": properties or EMAIL_PROPERTIES,
                    },
                    self._next_id(),
                ]
            ]
        )
        return [Email.from_dict(e) for e in responses[0][1].get("list") or []]

    async def email_changes(
        self, since_state: str
    ) -> tuple[str, list[str], list[str], list[str]]:
        """Return (new_state, created_ids, updated_ids, destroyed_ids)."""
        responses = await self.invoke(
            [
                [
                    "Email/changes",
                    {
                        "accountId": self.session.primary_account_id,
                        "sinceState": since_state,
                        "maxChanges": 200,
                    },
                    self._next_id(),
                ]
            ]
        )
        body = responses[0][1]
        return (
            body.get("newState", since_state),
            body.get("created") or [],
            body.get("updated") or [],
            body.get("destroyed") or [],
        )

    async def get_state(self) -> str:
        """Return the current Email state token."""
        responses = await self.invoke(
            [
                [
                    "Email/get",
                    {
                        "accountId": self.session.primary_account_id,
                        "ids": [],
                        "properties": ["id"],
                    },
                    self._next_id(),
                ]
            ]
        )
        return responses[0][1].get("state", "")

    async def set_keyword(
        self, email_id: str, keyword: str, value: bool
    ) -> None:
        await self.invoke(
            [
                [
                    "Email/set",
                    {
                        "accountId": self.session.primary_account_id,
                        "update": {email_id: {f"keywords/{keyword}": True if value else None}},
                    },
                    self._next_id(),
                ]
            ]
        )

    async def mark_read(self, email_id: str, read: bool = True) -> None:
        await self.set_keyword(email_id, "$seen", read)

    async def flag(self, email_id: str, flagged: bool = True) -> None:
        await self.set_keyword(email_id, "$flagged", flagged)

    async def move(self, email_id: str, target_mailbox_id: str) -> None:
        """Replace mailboxIds with a single target mailbox."""
        await self.invoke(
            [
                [
                    "Email/set",
                    {
                        "accountId": self.session.primary_account_id,
                        "update": {
                            email_id: {"mailboxIds": {target_mailbox_id: True}}
                        },
                    },
                    self._next_id(),
                ]
            ]
        )

    async def archive(self, email_id: str) -> None:
        archive_mb = await self.find_mailbox_by_role(ROLE_ARCHIVE)
        if archive_mb is None:
            raise JMAPNotFound("No mailbox with role=archive on server")
        await self.move(email_id, archive_mb.id)

    async def delete(self, email_id: str) -> None:
        await self.invoke(
            [
                [
                    "Email/set",
                    {
                        "accountId": self.session.primary_account_id,
                        "destroy": [email_id],
                    },
                    self._next_id(),
                ]
            ]
        )

    async def list_identities(self) -> list[dict[str, Any]]:
        if self._identity_cache is not None:
            return self._identity_cache
        responses = await self.invoke(
            [
                [
                    "Identity/get",
                    {"accountId": self.session.submission_account_id or self.session.primary_account_id},
                    self._next_id(),
                ]
            ],
            using=[JMAP_CORE_CAPABILITY, JMAP_SUBMISSION_CAPABILITY],
        )
        self._identity_cache = responses[0][1].get("list") or []
        return self._identity_cache

    async def upload_blob(self, content: bytes, content_type: str) -> dict[str, Any]:
        """Upload a binary blob and return the JMAP blob info."""
        sess = self.session
        url = sess.upload_url.replace("{accountId}", sess.primary_account_id)
        headers = self._auth_header()
        headers["Content-Type"] = content_type
        async with self._http.post(
            url,
            data=content,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
            ssl=self._verify_ssl,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def send_email(
        self,
        *,
        identity_id: str,
        to: list[str | dict[str, str]],
        subject: str,
        text: str | None = None,
        html: str | None = None,
        cc: list[str | dict[str, str]] | None = None,
        bcc: list[str | dict[str, str]] | None = None,
        reply_to: list[str | dict[str, str]] | None = None,
        from_address: dict[str, str] | None = None,
        attachments: list[str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a draft and submit it. Returns the submission result."""
        identities = await self.list_identities()
        identity = next((i for i in identities if i["id"] == identity_id), None)
        if identity is None:
            raise JMAPNotFound(f"Identity {identity_id} not found")

        drafts = await self.find_mailbox_by_role(ROLE_DRAFTS)
        sent = await self.find_mailbox_by_role(ROLE_SENT)
        if drafts is None or sent is None:
            raise JMAPNotFound("Server must expose drafts and sent mailboxes")

        attachments_data: list[dict[str, Any]] = []
        body_attachments: list[dict[str, Any]] = []
        for path in attachments or []:
            content = await asyncio.to_thread(_read_file_bytes, path)
            ctype, _ = mimetypes.guess_type(path)
            blob = await self.upload_blob(content, ctype or "application/octet-stream")
            attachments_data.append(
                {
                    "blobId": blob["blobId"],
                    "type": blob.get("type", ctype),
                    "name": os.path.basename(path),
                    "size": blob.get("size", len(content)),
                    "disposition": "attachment",
                }
            )
            body_attachments.append({"partId": f"a{len(attachments_data)}"})

        body_values: dict[str, Any] = {}
        text_body: list[dict[str, str]] | None = None
        html_body: list[dict[str, str]] | None = None
        if text is not None:
            body_values["t"] = {"value": text, "charset": "utf-8"}
            text_body = [{"partId": "t", "type": "text/plain"}]
        if html is not None:
            body_values["h"] = {"value": html, "charset": "utf-8"}
            html_body = [{"partId": "h", "type": "text/html"}]

        from_addr = from_address or {
            "email": identity["email"],
            "name": identity.get("name") or "",
        }

        draft: dict[str, Any] = {
            "from": [from_addr],
            "to": [self._normalize_address(a) for a in to],
            "subject": subject,
            "keywords": {"$draft": True, "$seen": True},
            "mailboxIds": {drafts.id: True},
            "bodyValues": body_values,
            "receivedAt": None,
        }
        if cc:
            draft["cc"] = [self._normalize_address(a) for a in cc]
        if bcc:
            draft["bcc"] = [self._normalize_address(a) for a in bcc]
        if reply_to:
            draft["replyTo"] = [self._normalize_address(a) for a in reply_to]
        if text_body:
            draft["textBody"] = text_body
        if html_body:
            draft["htmlBody"] = html_body
        if attachments_data:
            draft["attachments"] = attachments_data
        if headers:
            draft["headers"] = [
                {"name": k, "value": v} for k, v in headers.items()
            ]

        create_id = "draft"
        submit_id = "submission"

        set_call = [
            "Email/set",
            {
                "accountId": self.session.primary_account_id,
                "create": {create_id: draft},
            },
            self._next_id(),
        ]
        submit_call = [
            "EmailSubmission/set",
            {
                "accountId": self.session.submission_account_id
                or self.session.primary_account_id,
                "create": {
                    submit_id: {
                        "identityId": identity_id,
                        "emailId": f"#{create_id}",
                    }
                },
                "onSuccessUpdateEmail": {
                    f"#{submit_id}": {
                        f"mailboxIds/{drafts.id}": None,
                        f"mailboxIds/{sent.id}": True,
                        "keywords/$draft": None,
                    }
                },
            },
            self._next_id(),
        ]
        responses = await self.invoke([set_call, submit_call])
        submission_resp = next(
            (r for r in responses if r[0] == "EmailSubmission/set"), None
        )
        if submission_resp is None:
            raise JMAPError("No EmailSubmission/set response")
        not_created = (submission_resp[1] or {}).get("notCreated") or {}
        if not_created:
            err = next(iter(not_created.values()))
            raise JMAPMethodError(
                method="EmailSubmission/set",
                error_type=err.get("type", "unknown"),
                description=err.get("description"),
            )
        return submission_resp[1]

    @staticmethod
    def _normalize_address(addr: str | Mapping[str, str]) -> dict[str, str]:
        if isinstance(addr, str):
            return {"email": addr}
        return {"email": addr["email"], **({"name": addr["name"]} if addr.get("name") else {})}

    async def event_source(self) -> AsyncIterator[dict[str, Any]]:
        """Yield decoded state-change payloads from the JMAP push channel."""
        sess = self.session
        if not sess.event_source_url:
            raise JMAPError("Server does not advertise an eventSourceUrl")
        url = sess.event_source_url
        if "?" in url:
            url += "&types=*&closeafter=no&ping=300"
        else:
            url += "?types=*&closeafter=no&ping=300"
        headers = self._auth_header()
        headers["Accept"] = "text/event-stream"

        backoff = 1.0
        while True:
            try:
                async with self._http.get(
                    url,
                    headers=headers,
                    timeout=PUSH_TIMEOUT,
                    ssl=self._verify_ssl,
                ) as resp:
                    if resp.status in (401, 403):
                        raise JMAPAuthError(f"Event source returned {resp.status}")
                    resp.raise_for_status()
                    backoff = 1.0
                    event_type: str | None = None
                    data_buf: list[str] = []
                    async for raw in resp.content:
                        line = raw.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
                        if not line:
                            if data_buf:
                                payload = "\n".join(data_buf)
                                data_buf = []
                                if event_type in (None, "state"):
                                    try:
                                        yield json.loads(payload)
                                    except json.JSONDecodeError:
                                        _LOGGER.debug("Bad SSE payload: %s", payload)
                                event_type = None
                            continue
                        if line.startswith(":"):
                            continue
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_buf.append(line[5:].lstrip())
            except (ClientError, asyncio.TimeoutError) as err:
                _LOGGER.warning(
                    "JMAP event source disconnected (%s); reconnecting in %.1fs",
                    err,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
