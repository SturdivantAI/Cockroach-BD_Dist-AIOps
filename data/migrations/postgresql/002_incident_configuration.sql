-- Preserve incident scope for ledger fingerprints and retrieval.
-- Existing rows remain unknown; do not infer versions from verifier receipts.
BEGIN;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS configuration_version TEXT;
COMMIT;
