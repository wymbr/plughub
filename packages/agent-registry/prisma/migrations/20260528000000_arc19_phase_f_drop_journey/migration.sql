-- Arc 19 Fase F: drop Journey entity and related Pool columns
-- Journey is superseded by the unified session model (channel_type=webhook sessions)

-- Drop Pool journey columns
ALTER TABLE pools DROP COLUMN IF EXISTS mentionable_journeys;
ALTER TABLE pools DROP COLUMN IF EXISTS authorized_journey_types;
ALTER TABLE pools DROP COLUMN IF EXISTS inbound_journey_resume;

-- Drop JourneyType table (Arc 17)
DROP TABLE IF EXISTS journey_types;
