-- 0026_context_occupancy_ledger — what occupied the model's window, and who put it there.
--
-- The third observation relation alongside `runtime_run_usage` (what a run
-- cost) and `runtime_model_call_usage` (what a call cost). This one answers a
-- different question: the decomposition of a single call's INPUT scalar into
-- the segments that produced it. Deliberately a separate table, never columns
-- on `runtime_model_call_usage` — that relation is the money tracker, with its
-- own lifecycle (cost backfill rewrites it), its own read pattern (daily
-- rollups), and a single-tracker invariant that must not acquire a second
-- source of billing truth. `provider_input_tokens` here is a READ-SIDE COPY
-- of the same normalized usage the meter consumes, present only so the
-- reconciliation below can be computed without a join.
--
-- Additive, no backfill. Every model call that happened before this migration
-- reports NULL occupancy, which is the truth; a zero row would assert that a
-- window was measured and found empty.
--
-- Identity is (model_call_id, attempt_ordinal), not `id`. A retried call is a
-- second materialized request against a second window state and earns a second
-- row rather than overwriting the first; rollups deduplicate on model_call_id
-- and take the last attempt for utilization.
--
-- Retention: both parent keys cascade, and that is the whole of it. The
-- composite run key takes the row out with `agent_runs`, and the conversation
-- key takes it out with `agent_conversations`, so a row can never outlive the
-- run or the conversation it describes.
--
-- Be precise about what that does and does not buy, because the design's §5
-- claim that occupancy therefore "needs no new retention class" is stronger
-- than this schema delivers, and a compliance reviewer must not read it as a
-- retention control:
--
--   * On Postgres, NOTHING currently hard-deletes an `agent_conversations` or
--     an `agent_runs` row. Conversations are soft-deleted (`deleted_at`), and
--     `agent_messages`, `agent_runs`, `runtime_events`, `runtime_run_usage` and
--     `runtime_model_call_usage` all hold NO ACTION references to
--     `agent_conversations (id)` — so a hard conversation delete would raise a
--     foreign-key violation long before reaching this cascade. These cascades
--     are therefore correct but currently unreachable.
--   * `runtime_events` is not erased by a conversation cascade either. It is
--     swept by an explicit `RetentionKind.EVENTS` policy, which occupancy does
--     not join, and the application role below holds no DELETE privilege here.
--
-- Net: on Postgres an occupancy row is retained until a parent-deleting path
-- exists or a retention kind is added. That is a deliberate, documented state,
-- not an inherited control. The file-native (desktop) store DOES have a real
-- hard-purge path, and its adapter erases these rows explicitly there
-- (`FileRuntimeApiStore._erase_context_occupancy`).
--
-- The composite (org_id, run_id) foreign key also carries ON UPDATE CASCADE,
-- so an approved account re-key moves these rows with `agent_runs` and no
-- second mutable write path is needed — the same arrangement
-- `runtime_usage_attribution_edges` uses, and the reason this table is
-- deliberately absent from the account-merge simple-table list.
--
-- `agent_runs_org_id_id_key` is created by 0014 and is NOT re-created or
-- dropped here.

CREATE TABLE runtime_context_occupancy (
    id                          text NOT NULL,
    org_id                      text NOT NULL,
    run_id                      text NOT NULL,
    conversation_id             text NOT NULL,
    model_call_id               text NOT NULL,
    attempt_ordinal             integer DEFAULT 1 NOT NULL,
    -- Links to the F2 `prompt.assembled.v1` observation the way
    -- `PromptCacheObservationInput` already does. Nullable: occupancy is
    -- measured on the materialized provider request, which exists on calls
    -- where typed prompt assembly did not run.
    assembly_record_id          text,
    graph_scope                 text DEFAULT 'root' NOT NULL,
    provider                    text NOT NULL,
    model_family                text NOT NULL,
    -- NULL when the model is absent from the pricing catalog. `free_tokens` is
    -- then unknowable and is reported as NULL rather than invented.
    context_window_tokens       integer,
    estimated_input_tokens      integer DEFAULT 0 NOT NULL,
    -- NULL when the provider never reported usage for this attempt (a
    -- fail-open partial snapshot still persists what it did measure).
    provider_input_tokens       integer,
    cached_input_tokens         integer DEFAULT 0 NOT NULL,
    cache_creation_input_tokens integer DEFAULT 0 NOT NULL,
    -- Expected 0. Above 0 means a first-party contributor put text in front of
    -- the model without declaring an origin: a contract defect, not drift.
    undeclared_tokens           integer DEFAULT 0 NOT NULL,
    -- SIGNED, and intentionally so: negative means our tokenizer over-counted.
    -- Clamping this at zero would hide the drift it exists to expose, which is
    -- why it carries no non-negative CHECK.
    unattributed_delta          integer DEFAULT 0 NOT NULL,
    -- Read whole, never queried per segment. A fan-out table would add 30+
    -- rows per model call for no query benefit.
    segments_json               jsonb DEFAULT '{}'::jsonb NOT NULL,
    schema_version              integer DEFAULT 1 NOT NULL,
    created_at                  timestamptz NOT NULL,
    CONSTRAINT runtime_context_occupancy_pkey PRIMARY KEY (id),
    -- The durable identity. Makes an at-least-once writer idempotent through
    -- ON CONFLICT DO NOTHING while keeping each retry attempt distinct.
    CONSTRAINT runtime_context_occupancy_attempt_key
        UNIQUE (model_call_id, attempt_ordinal),
    CONSTRAINT runtime_context_occupancy_run_fk
        FOREIGN KEY (org_id, run_id)
        REFERENCES agent_runs (org_id, id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT runtime_context_occupancy_conversation_fk
        FOREIGN KEY (conversation_id)
        REFERENCES agent_conversations (id)
        ON DELETE CASCADE,
    CONSTRAINT runtime_context_occupancy_identifiers_check CHECK (
        id <> '' AND model_call_id <> '' AND provider <> '' AND model_family <> ''
        AND (assembly_record_id IS NULL OR assembly_record_id <> '')
    ),
    CONSTRAINT runtime_context_occupancy_graph_scope_check CHECK (
        graph_scope IN ('root', 'subagent')
    ),
    CONSTRAINT runtime_context_occupancy_attempt_check CHECK (attempt_ordinal >= 1),
    CONSTRAINT runtime_context_occupancy_counts_check CHECK (
        estimated_input_tokens >= 0
        AND cached_input_tokens >= 0
        AND cache_creation_input_tokens >= 0
        AND undeclared_tokens >= 0
        AND (context_window_tokens IS NULL OR context_window_tokens >= 0)
        AND (provider_input_tokens IS NULL OR provider_input_tokens >= 0)
    ),
    CONSTRAINT runtime_context_occupancy_segments_object_check CHECK (
        jsonb_typeof(segments_json) = 'object'
    )
);

-- The read API is per-run and filters by scope; ordering by created_at is the
-- per-turn series. (org_id, run_id) alone is the prefix of this index.
CREATE INDEX idx_runtime_context_occupancy_org_run
    ON runtime_context_occupancy (org_id, run_id, created_at);

-- Two jobs, one index. "What is in context right now" for a conversation reads
-- the latest snapshot (both leading columns are equality predicates), and
-- `conversation_id` leads so the ON DELETE CASCADE above can find referencing
-- rows by index instead of sequentially scanning a table that grows by one row
-- per model call.
CREATE INDEX idx_runtime_context_occupancy_conversation
    ON runtime_context_occupancy (conversation_id, org_id, created_at DESC);

ALTER TABLE runtime_context_occupancy ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_context_occupancy FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime_context_occupancy
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

-- No UPDATE and no DELETE privilege: an occupancy measurement is a fact about
-- a request that has already been sent, so there is no later information that
-- could legitimately correct it. Cascading deletes still work — referential
-- actions run as the table owner and bypass both RLS and column privileges.
GRANT SELECT, INSERT ON runtime_context_occupancy TO enterprise_app;
