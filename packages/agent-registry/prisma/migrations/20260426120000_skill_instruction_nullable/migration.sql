-- Make instruction nullable — orchestrator skills define behaviour via flow, not a prompt
ALTER TABLE "Skill" ALTER COLUMN "instruction" DROP NOT NULL;
