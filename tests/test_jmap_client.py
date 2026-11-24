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


def _body_email(eid: str, text_body, html_body, values) -> dict:
    return {
        "id": eid,
        "blobId": f"b-{eid}",
        "threadId": f"t-{eid}",
        "mailboxIds": {"inbox": True},
        "keywords": {},
        "from": [{"email": "track@ups.example", "name": "UPS"}],
        "to": [{"email": "you@example.com"}],
        "cc": [],
        "subject": "Your parcel is on its way",
        "receivedAt": "2026-01-02T00:00:00Z",
        "sentAt": "2026-01-02T00:00:00Z",
        "size": 4096,
        "preview": "Tracking number",
        "hasAttachment": False,
        "messageId": [f"<{eid}@example.com>"],
        "textBody": text_body,
        "htmlBody": html_body,
        "bodyValues": values,
    }


async def _client(fake_jmap_server, http_session) -> JMAPClient:
    client = JMAPClient(
        http_session,
        server_url=str(fake_jmap_server.make_url("")),
        token="test-token",
    )
    await client.connect()
    return client


@pytest.mark.asyncio
async def test_get_email_with_body_returns_text_and_html(fake_jmap_server, http_session):
    fake_jmap_server.state["emails"]["p1"] = _body_email(
        "p1",
        [{"partId": "1", "type": "text/plain"}],
        [{"partId": "2", "type": "text/html"}],
        {
            "1": "Tracking number: 1Z999AA10123456784",
            "2": "<p>Tracking number: <b>1Z999AA10123456784</b></p>",
        },
    )
    client = await _client(fake_jmap_server, http_session)
    email = await client.get_email_with_body("p1", 65536)
    assert email is not None
    assert email["email_id"] == "p1"
    assert email["subject"] == "Your parcel is on its way"
    assert email["from"] == ["track@ups.example"]
    assert email["text"] == "Tracking number: 1Z999AA10123456784"
    assert email["html"] == "<p>Tracking number: <b>1Z999AA10123456784</b></p>"
    assert email["truncated"] is False


@pytest.mark.asyncio
async def test_get_email_with_body_turns_html_only_mail_into_text(
    fake_jmap_server, http_session
):
    # Without a text/plain alternative, textBody points at the HTML part (RFC 8621).
    html = (
        "<html><head><style>p { color: red }</style></head><body>"
        "<p>Hello&nbsp;Anna,</p><p>Tracking:<br/>1Z999AA10123456784</p>"
        "<script>alert(1)</script></body></html>"
    )
    fake_jmap_server.state["emails"]["p2"] = _body_email(
        "p2",
        [{"partId": "1", "type": "text/html"}],
        [{"partId": "1", "type": "text/html"}],
        {"1": html},
    )
    client = await _client(fake_jmap_server, http_session)
    email = await client.get_email_with_body("p2", 65536)
    assert email is not None
    assert email["text"] == "Hello Anna,\n\nTracking:\n1Z999AA10123456784"
    assert email["html"] == html


@pytest.mark.asyncio
async def test_get_email_with_body_cuts_long_bodies(fake_jmap_server, http_session):
    fake_jmap_server.state["emails"]["p3"] = _body_email(
        "p3",
        [{"partId": "1", "type": "text/plain"}],
        [{"partId": "1", "type": "text/plain"}],
        {"1": "x" * 5000},
    )
    client = await _client(fake_jmap_server, http_session)
    email = await client.get_email_with_body("p3", 100)
    assert email is not None
    assert email["text"] == "x" * 100
    assert email["html"] == ""
    assert email["truncated"] is True


@pytest.mark.asyncio
async def test_get_email_with_body_unknown_id(fake_jmap_server, http_session):
    client = await _client(fake_jmap_server, http_session)
    assert await client.get_email_with_body("nope", 65536) is None

