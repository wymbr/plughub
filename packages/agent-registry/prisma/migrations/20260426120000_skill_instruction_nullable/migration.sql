-- Make instruction nullable — orchestrator skills define behaviour via flow, not a prompt
ALTER TABLE "skills" ALTER COLUMN "instruction" DROP NOT NULL;
