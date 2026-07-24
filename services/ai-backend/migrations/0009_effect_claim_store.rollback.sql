-- Rollback 0009_effect_claim_store: remove durable A5 execution claims.

DROP TABLE IF EXISTS runtime_effect_claims;
