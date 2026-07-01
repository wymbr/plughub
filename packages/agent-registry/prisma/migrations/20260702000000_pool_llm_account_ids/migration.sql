-- LLM Accounts — Pool-level preferred account association
-- Adds llm_account_ids to the pools table.
--
-- llm_account_ids: ordered preference list of LLM Account ids (config-api
-- namespace `llm_accounts`). Empty (default) = unrestricted, preserves
-- current behaviour for every existing pool. See
-- PoolRegistrationSchema.llm_account_ids (@plughub/schemas) for full semantics.

ALTER TABLE "pools"
  ADD COLUMN "llm_account_ids" TEXT[] NOT NULL DEFAULT '{}';
