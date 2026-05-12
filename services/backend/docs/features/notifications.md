# Notification Preferences

Per-user notification settings stored by the backend.

See also:

- [features/policies.md](policies.md) — other per-org policy settings

---

## What it does

Backend stores user-level notification preferences. These are consulted by outbound
notification systems (email workers, in-app notifications) to determine whether and when
to deliver a notification.

The backend stores the preferences; it does not implement the notification delivery itself
— that belongs in the deployment's email adapter (`identity/email_dispatcher.py` protocol).

---

## Key files

| File                                  | Role                                      |
| ------------------------------------- | ----------------------------------------- |
| `backend_app/notifications/store.py`  | `NotificationStore` — persistence         |
| `backend_app/routes/notifications.py` | `GET/PUT /internal/v1/auth/notifications` |

---

## Record shape

`NotificationPreferenceRecord` — one row per (org, user):

| Field                                  | Type              | Notes                                                    |
| -------------------------------------- | ----------------- | -------------------------------------------------------- |
| `user_id`, `org_id`                    | `str`             | Owner                                                    |
| `email_enabled`                        | `bool`            | Master email toggle; `False` suppresses all emails       |
| `quiet_hours_start`, `quiet_hours_end` | `time \| None`    | Local time-of-day range for suppression                  |
| `timezone`                             | `str`             | IANA timezone for quiet-hours evaluation                 |
| `channels`                             | `dict[str, bool]` | Per-channel overrides (e.g., `{"run_completed": false}`) |
| `updated_at`                           | `datetime`        | UTC                                                      |

---

## Routes

| Route                                 | Auth          | Notes                                                          |
| ------------------------------------- | ------------- | -------------------------------------------------------------- |
| `GET /internal/v1/auth/notifications` | Service token | Returns current preferences; returns defaults if no row exists |
| `PUT /internal/v1/auth/notifications` | Service token | Upsert; identity from service headers                          |

These routes use service-token auth (called by facade on behalf of the user). The facade
injects `x-enterprise-org-id` and `x-enterprise-user-id` so the backend can scope the row.

---

## Email dispatcher protocol

`backend_app/identity/email_dispatcher.py` — `EmailDispatcher` protocol (abstract).

The concrete implementation is injected at startup by the deployment's adapter. In local
dev, a console-print adapter is used. In production, the adapter submits to the
deployment's transactional email service (SES, SendGrid, etc.).

Callers use `EmailDispatcher.send_email(to, subject, body)` — they never reference the
delivery mechanism.
