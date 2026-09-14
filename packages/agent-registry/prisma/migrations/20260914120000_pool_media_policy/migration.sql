-- `pools.media_policy` — mídias que o pool OFERECE no canal WebRTC, por direção (VOZ-10).
--
-- { "customer_publish": ["audio","video"], "agent_publish": ["audio"] }
--
-- POR QUE UMA COLUNA NO POOL
-- ==========================
-- É o TETO do modelo de mídia por participante (VOZ-09). Até 2026-09-14 o gateway
-- lia dois campos de pool que não existiam em lugar nenhum (`webrtc_media_fallback_order`,
-- `webrtc_recording`) e uma capacidade de agente sem produtor (`media_capabilities`),
-- então a negociação era inerte e todo contato WebRTC sairia em texto. A política é
-- config de NEGÓCIO e o store dela é o agent-registry, editável na tela — nunca env,
-- nunca tabela no código do gateway.
--
-- Aditiva e anulável, mas NÃO opcional para quem usa: a rota recusa pool de contato
-- com `webrtc` em `channel_types` sem esta coluna. Medido antes: 45 pools, nenhum com
-- `webrtc`, então nenhum pool existente fica inválido.

ALTER TABLE "pools" ADD COLUMN IF NOT EXISTS "media_policy" JSONB;
