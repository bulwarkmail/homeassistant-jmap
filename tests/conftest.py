"""Shared pytest fixtures.

These tests run against the JMAP client in isolation — they do not need
pytest-homeassistant-custom-component. For full HA integration tests install
that package and follow Home Assistant's testing guide.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
import pytest
from aiohttp import web


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def fake_jmap_server(aiohttp_server):
    """A tiny in-process JMAP server that satisfies our client."""

    state = {
        "emails": {
            "e1": {
                "id": "e1",
                "blobId": "b1",
                "threadId": "t1",
                "mailboxIds": {"inbox": True},
                "keywords": {},
                "from": [{"email": "alice@example.com", "name": "Alice"}],
                "to": [{"email": "you@example.com"}],
                "cc": [],
                "subject": "Hello",
                "receivedAt": "2026-01-01T00:00:00Z",
                "sentAt": "2026-01-01T00:00:00Z",
                "size": 1024,
                "preview": "Hi there",
                "hasAttachment": False,
                "messageId": ["<m1@example.com>"],
            }
        },
        "email_state": "s0",
        "mailboxes": {
            "inbox": {
                "id": "inbox",
                "name": "Inbox",
                "role": "inbox",
                "parentId": None,
                "totalEmails": 1,
                "unreadEmails": 1,
                "totalThreads": 1,
                "unreadThreads": 1,
                "sortOrder": 0,
            },
            "drafts": {
                "id": "drafts",
                "name": "Drafts",
                "role": "drafts",
                "parentId": None,
                "totalEmails": 0,
                "unreadEmails": 0,
                "totalThreads": 0,
                "unreadThreads": 0,
                "sortOrder": 1,
            },
            "sent": {
                "id": "sent",
                "name": "Sent",
                "role": "sent",
                "parentId": None,
                "totalEmails": 0,
                "unreadEmails": 0,
                "totalThreads": 0,
                "unreadThreads": 0,
                "sortOrder": 2,
            },
            "archive": {
                "id": "archive",
                "name": "Archive",
                "role": "archive",
                "parentId": None,
                "totalEmails": 0,
                "unreadEmails": 0,
                "totalThreads": 0,
                "unreadThreads": 0,
                "sortOrder": 3,
            },
        },
        "identities": [
            {"id": "id1", "name": "You", "email": "you@example.com"},
        ],
    }

    async def session_handler(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") not in (
            "Bearer test-token",
            "Basic eW91OnNlY3JldA==",
        ):
            return web.Response(status=401)
        base = str(request.url.with_path(""))
        return web.json_response(
            {
                "username": "you@example.com",
                "apiUrl": f"{base}/jmap",
                "downloadUrl": f"{base}/dl/{{blobId}}",
                "uploadUrl": f"{base}/upload/{{accountId}}",
                "eventSourceUrl": f"{base}/sse",
                "primaryAccounts": {
                    "urn:ietf:params:jmap:mail": "acct1",
                    "urn:ietf:params:jmap:submission": "acct1",
                },
                "accounts": {
                    "acct1": {
                        "name": "you@example.com",
                        "accountCapabilities": {
                            "urn:ietf:params:jmap:mail": {},
                        },
                    }
                },
                "capabilities": {
                    "urn:ietf:params:jmap:core": {},
                    "urn:ietf:params:jmap:mail": {},
                    "urn:ietf:params:jmap:submission": {},
                },
            }
        )

    async def api_handler(request: web.Request) -> web.Response:
        body = await request.json()
        method_responses: list[Any] = []
        for call in body.get("methodCalls", []):
            method, args, rid = call
            if method == "Mailbox/get":
                method_responses.append(
                    [
                        "Mailbox/get",
                        {
                            "accountId": "acct1",
                            "state": "m0",
                            "list": list(state["mailboxes"].values()),
                        },
                        rid,
                    ]
                )
            elif method == "Identity/get":
                method_responses.append(
                    [
                        "Identity/get",
                        {"accountId": "acct1", "list": state["identities"]},
                        rid,
                    ]
                )
            elif method == "Email/query":
                method_responses.append(
                    [
                        "Email/query",
                        {
                            "accountId": "acct1",
                            "queryState": "q0",
                            "ids": list(state["emails"].keys()),
                            "total": len(state["emails"]),
                        },
                        rid,
                    ]
                )
            elif method == "Email/get":
                ids = args.get("ids") or list(state["emails"].keys())
                method_responses.append(
                    [
                        "Email/get",
                        {
                            "accountId": "acct1",
                            "state": state["email_state"],
                            "list": [
                                state["emails"][i]
                                for i in ids
                                if i in state["emails"]
                            ],
                            "notFound": [
                                i for i in ids if i not in state["emails"]
                            ],
                        },
                        rid,
                    ]
                )
            elif method == "Email/set":
                updated: dict[str, Any] = {}
                for eid, patch in (args.get("update") or {}).items():
                    email = state["emails"].get(eid)
                    if email is None:
                        continue
                    for key, value in patch.items():
                        if key.startswith("keywords/"):
                            kw = key.split("/", 1)[1]
                            if value is None:
                                email["keywords"].pop(kw, None)
                            else:
                                email["keywords"][kw] = True
                        elif key == "mailboxIds":
                            email["mailboxIds"] = value
                    updated[eid] = email
                created: dict[str, Any] = {}
                for create_id, draft in (args.get("create") or {}).items():
                    new_id = f"e{len(state['emails'])+1}"
                    draft["id"] = new_id
                    draft.setdefault("blobId", f"b{new_id}")
                    draft.setdefault("threadId", f"t{new_id}")
                    state["emails"][new_id] = draft
                    created[create_id] = {"id": new_id, "blobId": draft["blobId"]}
                for eid in args.get("destroy") or []:
                    state["emails"].pop(eid, None)
                method_responses.append(
                    [
                        "Email/set",
                        {
                            "accountId": "acct1",
                            "oldState": state["email_state"],
                            "newState": "s1",
                            "created": created,
                            "updated": updated,
                            "destroyed": args.get("destroy") or [],
                        },
                        rid,
                    ]
                )
                state["email_state"] = "s1"
            elif method == "EmailSubmission/set":
                created: dict[str, Any] = {}
                for create_id, _ in (args.get("create") or {}).items():
                    created[create_id] = {"id": f"sub-{create_id}"}
                method_responses.append(
                    [
                        "EmailSubmission/set",
                        {"accountId": "acct1", "created": created},
                        rid,
                    ]
                )
            elif method == "Email/changes":
                method_responses.append(
                    [
                        "Email/changes",
                        {
                            "accountId": "acct1",
                            "oldState": args["sinceState"],
                            "newState": state["email_state"],
                            "created": [],
                            "updated": [],
                            "destroyed": [],
                            "hasMoreChanges": False,
                        },
                        rid,
                    ]
                )
            else:
                method_responses.append(
                    [
                        "error",
                        {"type": "unknownMethod", "description": method},
                        rid,
                    ]
                )
        return web.json_response(
            {"methodResponses": method_responses, "sessionState": "z0"}
        )

    async def upload_handler(request: web.Request) -> web.Response:
        body = await request.read()
        return web.json_response(
            {
                "accountId": "acct1",
                "blobId": f"blob-{len(body)}",
                "type": request.headers.get("Content-Type", "application/octet-stream"),
                "size": len(body),
            }
        )

    app = web.Application()
    app.router.add_get("/.well-known/jmap", session_handler)
    app.router.add_post("/jmap", api_handler)
    app.router.add_post("/upload/{accountId}", upload_handler)
    server = await aiohttp_server(app)
    server.state = state  # expose for assertions
    return server


@pytest.fixture
async def http_session():
    async with aiohttp.ClientSession() as session:
        yield session
