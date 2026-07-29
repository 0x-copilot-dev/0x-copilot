-- 0025_artifact_accent — the identity hue an author chose for a surface.
--
-- Display metadata only, in the same class as `title`: a bounded name from a
-- closed vocabulary, carrying no payload, no path, and no credential. It is
-- persisted rather than left on the ledger event because a tab reopened from an
-- earlier turn reads this row, not that run's event stream — an accent that
-- lived only on the event would disappear on reload.
--
-- Nullable, and permanently so. Every artifact created before this column
-- exists has no chosen accent, and NULL is the honest record of that: the
-- client derives a hue from `kind` instead. A default would invent a decision
-- nobody made.
--
-- The CHECK is what stops the column from becoming a style channel. The value
-- reaches a `data-surface-hue` attribute, so the set is fixed here, in the
-- Pydantic contract, and in the stylesheet — three gates that must agree before
-- a hue can render.

ALTER TABLE runtime_artifacts
    ADD COLUMN IF NOT EXISTS accent text;

ALTER TABLE runtime_artifacts
    ADD CONSTRAINT runtime_artifacts_accent_check
        CHECK (accent IS NULL OR accent IN (
            'jade', 'sky', 'indigo', 'ember', 'violet', 'plum', 'amber', 'none'
        ));
