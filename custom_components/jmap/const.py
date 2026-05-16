"""Constants for the JMAP integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "jmap"

CONF_SERVER_URL: Final = "server_url"
CONF_TOKEN: Final = "token"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_ACCOUNT_ID: Final = "account_id"
CONF_IDENTITY_ID: Final = "identity_id"
CONF_MONITORED_MAILBOXES: Final = "monitored_mailboxes"
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_USE_PUSH: Final = "use_push"
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_FROM_NAME: Final = "from_name"

DEFAULT_POLL_INTERVAL: Final = 60
DEFAULT_USE_PUSH: Final = True
DEFAULT_VERIFY_SSL: Final = True

EVENT_NEW_EMAIL: Final = "jmap_new_email"
EVENT_EMAIL_DELETED: Final = "jmap_email_deleted"
EVENT_MAILBOX_CHANGED: Final = "jmap_mailbox_changed"

SERVICE_SEND_EMAIL: Final = "send_email"
SERVICE_MARK_READ: Final = "mark_read"
SERVICE_MARK_UNREAD: Final = "mark_unread"
SERVICE_ARCHIVE: Final = "archive"
SERVICE_DELETE: Final = "delete"
SERVICE_MOVE: Final = "move_to_mailbox"
SERVICE_SEARCH: Final = "search"
SERVICE_FLAG: Final = "flag"
SERVICE_UNFLAG: Final = "unflag"

ATTR_TO: Final = "to"
ATTR_CC: Final = "cc"
ATTR_BCC: Final = "bcc"
ATTR_SUBJECT: Final = "subject"
ATTR_BODY: Final = "body"
ATTR_HTML: Final = "html"
ATTR_ATTACHMENTS: Final = "attachments"
ATTR_EMAIL_ID: Final = "email_id"
ATTR_MAILBOX_ID: Final = "mailbox_id"
ATTR_MAILBOX_NAME: Final = "mailbox_name"
ATTR_QUERY: Final = "query"
ATTR_LIMIT: Final = "limit"
ATTR_FROM: Final = "from_address"
ATTR_REPLY_TO: Final = "reply_to"
ATTR_HEADERS: Final = "headers"

ROLE_INBOX: Final = "inbox"
ROLE_DRAFTS: Final = "drafts"
ROLE_SENT: Final = "sent"
ROLE_TRASH: Final = "trash"
ROLE_ARCHIVE: Final = "archive"
ROLE_JUNK: Final = "junk"

JMAP_CORE_CAPABILITY: Final = "urn:ietf:params:jmap:core"
JMAP_MAIL_CAPABILITY: Final = "urn:ietf:params:jmap:mail"
JMAP_SUBMISSION_CAPABILITY: Final = "urn:ietf:params:jmap:submission"

WELL_KNOWN_PATH: Final = "/.well-known/jmap"

EMAIL_PROPERTIES: Final = [
    "id",
    "blobId",
    "threadId",
    "mailboxIds",
    "keywords",
    "from",
    "to",
    "cc",
    "subject",
    "receivedAt",
    "sentAt",
    "size",
    "preview",
    "hasAttachment",
    "messageId",
    "inReplyTo",
]

PLATFORMS: Final = ["sensor", "binary_sensor", "notify"]
