"""Unit tests for the JMAP client against an in-process fake server."""
from __future__ import annotations

import pytest

from custom_components.jmap.jmap_client import (
    JMAPAuthError,
    JMAPClient,
)


@pytest.mark.asyncio
async def test_connect_with_bearer_token(fake_jmap_server, http_session):
    client = JMAPClient(
        http_session,
        server_url=str(fake_jmap_server.make_url("")),
        token="test-token",
    )
    session = await client.connect()
    assert session.username == "you@example.com"
    assert session.primary_account_id == "acct1"
    assert session.event_source_url.endswith("/sse")


@pytest.mark.asyncio
async def test_connect_with_basic_auth(fake_jmap_server, http_session):
    client = JMAPClient(
        http_session,
        server_url=str(fake_jmap_server.make_url("")),
        username="you",
        password="secret",
    )
    session = await client.connect()
    assert session.username == "you@example.com"


@pytest.mark.asyncio
async def test_invalid_auth_raises(fake_jmap_server, http_session):
    client = JMAPClient(
        http_session,
        server_url=str(fake_jmap_server.make_url("")),
        token="wrong",
    )
    with pytest.raises(JMAPAuthError):
        await client.connect()


@pytest.mark.asyncio
async def test_list_mailboxes(fake_jmap_server, http_session):
    client = JMAPClient(
        http_session,
        server_url=str(fake_jmap_server.make_url("")),
        token="test-token",
    )
    await client.connect()
    boxes = await client.list_mailboxes()
    assert "inbox" in boxes
    assert boxes["inbox"].unread_emails == 1
    assert boxes["archive"].role == "archive"


@pytest.mark.asyncio
async def test_query_and_get_emails(fake_jmap_server, http_session):
    client = JMAPClient(
        http_session,
        server_url=str(fake_jmap_server.make_url("")),
        token="test-token",
    )
    await client.connect()
    emails = await client.query_emails(mailbox_id="inbox", limit=5)
    assert len(emails) == 1
    assert emails[0].subject == "Hello"
    assert emails[0].is_unread is True


@pytest.mark.asyncio
async def test_mark_read_and_flag(fake_jmap_server, http_session):
    client = JMAPClient(
        http_session,
        server_url=str(fake_jmap_server.make_url("")),
        token="test-token",
    )
    await client.connect()
    await client.mark_read("e1", True)
    await client.flag("e1", True)
    server_email = fake_jmap_server.state["emails"]["e1"]
    assert server_email["keywords"].get("$seen") is True
    assert server_email["keywords"].get("$flagged") is True


@pytest.mark.asyncio
async def test_archive_moves_to_archive_mailbox(fake_jmap_server, http_session):
    client = JMAPClient(
        http_session,
        server_url=str(fake_jmap_server.make_url("")),
        token="test-token",
    )
    await client.connect()
    await client.archive("e1")
    assert fake_jmap_server.state["emails"]["e1"]["mailboxIds"] == {"archive": True}


@pytest.mark.asyncio
async def test_send_email_round_trip(fake_jmap_server, http_session):
    client = JMAPClient(
        http_session,
        server_url=str(fake_jmap_server.make_url("")),
        token="test-token",
    )
    await client.connect()
    identities = await client.list_identities()
    result = await client.send_email(
        identity_id=identities[0]["id"],
        to=["alice@example.com"],
        subject="HA says hi",
        text="leak in laundry",
    )
    assert "created" in result
