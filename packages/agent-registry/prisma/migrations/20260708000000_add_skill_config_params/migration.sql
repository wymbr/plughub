-- Skill deploy config params (fatia 2)
-- Adds config_params to the skills table.
--
-- config_params: SkillConfigParam[] (@plughub/schemas) — deploy-time parameters
-- declared at the skill top-level. Rendered in the Flow › Deploy form and stored
-- into PoolSkillSlot.config_json at deploy; exposed to the runtime as $.config.*.
-- Nullable, no default — absent = skill has no deploy params (legacy behaviour).

ALTER TABLE "skills"
  ADD COLUMN "config_params" JSONB;
