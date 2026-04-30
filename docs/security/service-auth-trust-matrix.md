# Service Auth Trust Matrix

This matrix documents the current trust behavior for product and internal
service routes. It is intentionally explicit so future hardening can change one
cell at a time with tests.

| Surface | Development: missing client bearer | Development: missing service token | Production: missing service token | Invalid service token |
| --- | --- | --- | --- | --- |
| `backend-facade` product routes | Uses configured development identity. | Sends a local development service token upstream. | Fails with `503` when upstream headers are required and token is unset. | Not applicable for client bearer validation. |
| `backend` public `/v1/*` routes | Receives calls from facade; direct callers may use query/body scope only when service token is absent. | Falls back to query/body `org_id` and `user_id`. | Rejects missing service identity with `401` or missing token config with `503`. | Rejects with `401`. |
| `backend` internal `/internal/v1/*` routes | Not product-facing. | Allows local internal calls with query/body scope when token is unset. | Rejects missing token config with `503`. | Rejects with `401`. |
| `ai-backend` runtime `/v1/*` routes | Receives calls from facade; direct callers may use query/body scope only when service token is absent. | Allows direct local calls with query/body scope when token is unset. | Rejects missing token config with `503`. | Rejects with `401`. |

Production deployments must keep `backend` and `ai-backend` reachable only from
trusted service networks. The shared service token authenticates the caller, and
the identity headers bind the caller to an org/user scope.
