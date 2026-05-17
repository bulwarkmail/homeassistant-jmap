# JMAP Mail for Home Assistant

A first-class Home Assistant integration for any [JMAP](https://jmap.io) server
(Stalwart, Fastmail, Cyrus, Apache James, …). JMAP's native push channel means
new-mail events arrive in HA within a second or two — no IMAP IDLE polling, no
SMTP relay required.

## What you get

- **Push** via JMAP EventSource. Falls back to polling automatically.
- **Send mail** through HA's `notify` entity or the `jmap.send_email` service,
  including HTML, CC/BCC, custom headers, and attachments.
- **Sensors** for unread/total per mailbox, latest sender, latest subject. Names
  reflect the full mailbox path (e.g. `Inbox > Promotions > Travel unread`).
- **Binary sensors** for "has unread" per account and per mailbox.
- **Triggers** via the `jmap_new_email`, `jmap_email_deleted`, and
  `jmap_mailbox_changed` events.
- **Services** to mark read/unread, flag, archive, move, delete, search.
- **Multi-account** — add more than one entry, each is its own device.
- **Reverse-proxy friendly** — session URLs are re-anchored to the origin you
  actually connected to, so a server that advertises an internal `:8080` API
  URL still works when you reach it via `https://mail.example.com`.
- **Diagnostics** with credentials redacted.

## Install

### HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories.
2. Add `https://github.com/bulwarkmail/homeassistant-jmap` as type *Integration*.
3. Install **JMAP Mail**, restart Home Assistant.
4. Settings → Devices & Services → *Add Integration* → **JMAP Mail**.

### Manual

Copy `custom_components/jmap` into your HA `config/custom_components/` directory
and restart.

## Configure

You'll need:

- **Server URL** — usually the base URL of your mail server. The integration
  will probe `/.well-known/jmap` if you give it a bare hostname. You can also
  paste the full session URL directly (e.g.
  `https://mail.example.com/.well-known/jmap`).
- **Bearer token** *or* **username + password**. Bearer tokens are preferred
  where supported; for Fastmail use an *app password*.

Once added, open **Configure** on the integration to:

- Change the poll interval and toggle push.
- Set a default From name and identity.
- Pick **additional mailboxes** to expose as devices/sensors. Only the inbox
  and the mailboxes you select here will get unread/total/has-unread entities;
  everything else stays out of the UI until you opt in.

## Use it

### Trigger an automation on new mail

```yaml
automation:
  - alias: "Ring doorbell email → porch lights on"
    trigger:
      - platform: event
        event_type: jmap_new_email
        event_data:
          # event_data fields: account, from, from_name, subject, preview,
          # mailbox_names, has_attachment, is_unread, ...
    condition:
      - condition: template
        value_template: >-
          {{ 'no-reply@ring.com' in trigger.event.data.from
             and 'motion' in trigger.event.data.subject | lower }}
    action:
      - service: light.turn_on
        target: { entity_id: light.porch }
        data: { brightness_pct: 100, color_temp: 250 }
      - service: notify.mobile_app_pixel
        data:
          message: "Ring: {{ trigger.event.data.subject }}"
```

### Send a notification *as* email

```yaml
script:
  email_household:
    sequence:
      - service: notify.send_message
        target:
          entity_id: notify.you_at_example_com_email
        data:
          message: "Water leak detected in the laundry."
          title: "🚨 Leak alert"
          data:
            to: ["alice@example.com", "bob@example.com"]
            html: "<h1>Leak alert</h1><p>Sensor: {{ states('sensor.laundry_leak') }}</p>"
```

Or the explicit service:

```yaml
service: jmap.send_email
data:
  to: ["family@example.com"]
  subject: "Solar production today"
  body: "Generated {{ states('sensor.solar_today') }} kWh"
  attachments:
    - /config/www/solar-chart.png
```

### Daily 8am digest

```yaml
automation:
  - alias: "Morning inbox briefing"
    trigger: { platform: time, at: "08:00:00" }
    action:
      - service: jmap.search
        data: { query: "is:unread", limit: 10 }
        response_variable: result
      - service: tts.cloud_say
        data:
          entity_id: media_player.kitchen
          message: >-
            You have {{ result.emails | count }} unread emails.
            Most recent from {{ result.emails[0].from_name or result.emails[0].from[0] }}:
            {{ result.emails[0].subject }}.
```

### Mark mail as read from a dashboard

Bind a button to the service:

```yaml
service: jmap.mark_read
data:
  email_id: "{{ state_attr('sensor.you_at_example_com_latest_sender', 'email_id') }}"
```

## Events

### `jmap_new_email`

```yaml
account: "you@example.com"
entry_id: "01HX..."
email_id: "M..."
thread_id: "T..."
mailbox_ids: ["mb-inbox-id"]
mailbox_names: ["Inbox"]
from: ["alice@example.com"]
from_name: "Alice"
to: ["you@example.com"]
subject: "Pull request review request"
preview: "Hi, could you take a look at..."
received_at: "2026-05-17T08:14:02Z"
has_attachment: false
is_unread: true
is_flagged: false
```

### `jmap_email_deleted`

```yaml
account: "you@example.com"
entry_id: "01HX..."
email_id: "M..."
```

### `jmap_mailbox_changed`

Fired whenever an unread or total count changes on a known mailbox. Useful for
"mailbox emptied" automations.

## Blueprints

Three drop-in blueprints ship in [`blueprints/`](./blueprints):

- `package_delivery_alert.yaml` — flash a light when a parcel-tracking email
  lands.
- `ring_email_lights.yaml` — turn lights on when a Ring motion email arrives.
- `daily_inbox_summary.yaml` — daily digest via TTS or mobile push.

## Permissions

Attachments are restricted to paths under `config/` or any directory you've
added to `homeassistant.allowlist_external_dirs` — the same rules HA uses for
`shell_command` and `notify.smtp`.

## Limitations

- The integration uses a single Email state token per account. After very long
  outages (state expiry), the next refresh resyncs from the latest inbox rather
  than reconstructing every missed message.
- JMAP push pings every 5 minutes by default; if you sit behind a proxy that
  closes idle connections, the integration auto-reconnects with backoff.
- Calendar/contacts (JSCalendar/JSContact) are out of scope for v1.

## License

MIT. See [LICENSE](./LICENSE).
