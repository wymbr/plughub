-- AlterTable: add media_capabilities to agent_types (Arc 15 Phase B)
-- Stores the list of WebRTC media types the agent supports: video, voice, text
-- Empty array = text-only (universal fallback)

ALTER TABLE "agent_types"
    ADD COLUMN "media_capabilities" TEXT[] DEFAULT ARRAY[]::TEXT[];
