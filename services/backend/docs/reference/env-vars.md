# Environment Variables — backend

All env vars read by `services/backend`. No defaults means the var is required in production.

---

## Core / auth

| Variable                   | Default       | Notes                                                                              |
| -------------------------- | ------------- | ---------------------------------------------------------------------------------- |
| `ENTERPRISE_AUTH_SECRET`   | —             | **Required.** HMAC key for signing/verifying session bearer tokens                 |
| `ENTERPRISE_SERVICE_TOKEN` | —             | **Required in prod.** Shared secret; callers must set `X-Enterprise-Service-Token` |
| `BACKEND_ENVIRONMENT`      | `development` | `development` enables dev IdP routes; `production` enables hard safety checks      |
| `REQUIRE_SESSION_BINDING`  | `false`       | When `true`, bearers without `sid` claim are rejected (A2 hardening)               |
| `BOOTSTRAP_ADMIN_TOKEN`    | —             | One-time setup token for the first admin account                                   |

---

## Database

| Variable                                  | Default | Notes                                                             |
| ----------------------------------------- | ------- | ----------------------------------------------------------------- |
| `DATABASE_URL`                            | —       | PostgreSQL asyncpg connection string; omit for in-memory dev mode |
| `BACKEND_AUTO_MIGRATE`                    | `false` | When `true`, runs yoyo migrations at startup                      |
| `BACKEND_DB_POOL_MIN_SIZE`                | 5       | asyncpg min pool size                                             |
| `BACKEND_DB_POOL_MAX_SIZE`                | 50      | asyncpg max pool size                                             |
| `BACKEND_DB_POOL_ACQUIRE_TIMEOUT_SECONDS` | 5.0     | Time before pool acquire fails                                    |
| `BACKEND_DB_STATEMENT_TIMEOUT_MS`         | 10000   | Per-statement timeout                                             |
| `BACKEND_DB_LOCK_TIMEOUT_MS`              | 3000    | Lock wait timeout                                                 |
| `BACKEND_DB_IDLE_IN_TXN_TIMEOUT_MS`       | 30000   | Idle-in-transaction timeout                                       |

---

## Token vault (MCP OAuth secrets)

| Variable                  | Default     | Notes                                                       |
| ------------------------- | ----------- | ----------------------------------------------------------- |
| `MCP_TOKEN_VAULT_BACKEND` | `local`     | `local` = Fernet (dev only); `aws_kms` = production         |
| `MCP_TOKEN_VAULT_SECRET`  | —           | **Required when `local`.** Fernet key for encrypting tokens |
| `MCP_KMS_KEY_ID`          | —           | **Required when `aws_kms`.** AWS KMS key ARN                |
| `MCP_KMS_REGION`          | `us-east-1` | AWS region for KMS                                          |

---

## RBAC

| Variable    | Default   | Notes                                                           |
| ----------- | --------- | --------------------------------------------------------------- |
| `RBAC_MODE` | `enforce` | `audit` = log denials, continue; `enforce` = return 403 on deny |

---

## Deployment profile

| Variable                        | Default       | Notes                                       |
| ------------------------------- | ------------- | ------------------------------------------- |
| `ENTERPRISE_DEPLOYMENT_PROFILE` | `development` | `development`, `saas`, `bank`, `government` |

---

## Session lifecycle

| Variable                         | Default | Notes                              |
| -------------------------------- | ------- | ---------------------------------- |
| `SESSION_DEFAULT_TTL_SECONDS`    | 86400   | Default session TTL (24 hours)     |
| `SESSION_SWEEP_INTERVAL_SECONDS` | 60      | How often the session sweeper runs |

---

## MFA

| Variable           | Default     | Notes                                                        |
| ------------------ | ----------- | ------------------------------------------------------------ |
| `TOTP_ISSUER`      | `0xCopilot` | Display name in authenticator apps                           |
| `WEBAUTHN_RP_ID`   | —           | Relying party ID for WebAuthn (must match the origin domain) |
| `WEBAUTHN_RP_NAME` | `0xCopilot` | Relying party display name                                   |

---

## API keys

| Variable         | Default | Notes                                                                |
| ---------------- | ------- | -------------------------------------------------------------------- |
| `API_KEY_PEPPER` | —       | **Required in prod.** Per-deployment pepper for argon2id key hashing |

---

## SIEM export

| Variable                | Default            | Notes                                         |
| ----------------------- | ------------------ | --------------------------------------------- |
| `SIEM_EXPORTER`         | `null`             | `null`, `elastic`, `splunk`, `syslog`, `file` |
| `SIEM_ELASTIC_URL`      | —                  | Elasticsearch endpoint                        |
| `SIEM_ELASTIC_INDEX`    | `enterprise-audit` | Elasticsearch index                           |
| `SIEM_ELASTIC_API_KEY`  | —                  | Elasticsearch API key                         |
| `SIEM_SPLUNK_HEC_URL`   | —                  | Splunk HTTP Event Collector URL               |
| `SIEM_SPLUNK_HEC_TOKEN` | —                  | Splunk HEC token                              |
| `SIEM_SPLUNK_INDEX`     | `main`             | Splunk index                                  |
| `SIEM_SYSLOG_HOST`      | —                  | Syslog receiver hostname                      |
| `SIEM_SYSLOG_PORT`      | 514                | Syslog port                                   |
| `SIEM_SYSLOG_PROTO`     | `udp`              | `udp` or `tcp`                                |
| `SIEM_FILE_PATH`        | —                  | Path for file exporter (dev/test only)        |

---

## Observability

| Variable                      | Default   | Notes                            |
| ----------------------------- | --------- | -------------------------------- |
| `LOG_LEVEL`                   | `INFO`    | Python log level                 |
| `OTEL_SERVICE_NAME`           | `backend` | OpenTelemetry service name       |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | —         | OTLP endpoint for traces/metrics |

---

## Email (notification dispatch)

| Variable           | Default               | Notes                              |
| ------------------ | --------------------- | ---------------------------------- |
| `EMAIL_ADAPTER`    | `console`             | `console` (dev), `ses`, `sendgrid` |
| `EMAIL_FROM`       | `noreply@example.com` | Sender address                     |
| `SES_REGION`       | `us-east-1`           | AWS SES region                     |
| `SENDGRID_API_KEY` | —                     | SendGrid API key                   |

---

## Row-level security

| Variable      | Default | Notes                                                                          |
| ------------- | ------- | ------------------------------------------------------------------------------ |
| `ENFORCE_RLS` | `false` | When `true`, PostgreSQL RLS is applied; production profiles set this to `true` |
