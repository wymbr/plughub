/**
 * speech-metrics.ts
 * Telemetria passiva da fala — camada A da recalibragem de STT por instalação (VOZ-22).
 *
 * Tópico Kafka: speech.metrics — produtor channel-gateway (`speech_metrics.py`), consumidor
 * analytics-api. Chave de partição = `session_id`: os eventos de uma chamada ficam em ordem.
 *
 *   stt_stream_summary  fim do fluxo de fala do CLIENTE: chão de ruído, falas, descartes,
 *                       confiança e a segmentação em vigor (valor + escopo de cada parâmetro)
 *   collect_outcome     fim de cada menu com coleta por VOZ: desfecho, tentativas, recusas
 *                       por confiança, tecla depois de fala
 *
 * ⚠️ **Só números.** Nenhuma transcrição, valor coletado ou opção escolhida — é o que dispensa
 * consentimento de tratamento de voz (ADR `adr-voice-media-plane.md` V13). Por isso os objetos são
 * `.strict()`: um campo de texto novo REPROVA a validação em vez de passar calado.
 *
 * Percentil ausente é `null` (sem amostra), nunca 0 — 0 é chão de ruído legítimo (microfone digital).
 *
 * VOZ-25: os dois eventos levam o `speech_profile_id` EM VIGOR na chamada (null = sem perfil, ou
 * perfil que não pôde valer) — a recalibragem compara por perfil, não só por pool. O resumo leva
 * também o `stt_model` usado. Nome de modelo e id de perfil são config, não fala do cliente.
 */

import { z } from "zod"

const pct = z.number().nullable()

const Common = {
  event_id:   z.string().uuid(),
  tenant_id:  z.string().min(1),
  session_id: z.string().min(1),
  pool_id:    z.string().nullable(),
  channel:    z.literal("webrtc"),
  speech_profile_id: z.string().nullable(),
  timestamp:  z.string().datetime({ offset: true }),
}

export const SPEECH_SEGMENTATION_FIELDS = [
  "energy_threshold", "end_silence_ms", "gap_ms", "min_speech_ms", "max_speech_ms", "vad_filter",
] as const

export const SpeechSegmentationSnapshotSchema = z.object({
  energy_threshold: z.number(),
  end_silence_ms:   z.number().int(),
  gap_ms:           z.number().int(),
  min_speech_ms:    z.number().int(),
  max_speech_ms:    z.number().int(),
  vad_filter:       z.boolean(),
}).strict()

/** De onde veio cada parâmetro: perfil de fala da chamada, override do tenant, default da
 * plataforma, ou default de código. */
export const SpeechSegmentationScopeSchema = z.record(
  z.enum(SPEECH_SEGMENTATION_FIELDS),
  z.enum(["profile", "tenant", "global", "config", "default"]),
)

export const SttStreamSummaryEventSchema = z.object({
  ...Common,
  event_type:             z.literal("stt_stream_summary"),
  speaker:                z.literal("customer"),
  stt_provider:           z.string(),
  stt_model:              z.string().nullable(),
  audio_ms:               z.number().int().nonnegative(),
  frames:                 z.number().int().nonnegative(),
  voiced_frames:          z.number().int().nonnegative(),
  noise_rms_p10:          pct,
  noise_rms_p50:          pct,
  noise_rms_p90:          pct,
  utterances_sent:        z.number().int().nonnegative(),
  utterances_transcribed: z.number().int().nonnegative(),
  discarded_vad:          z.number().int().nonnegative(),
  discarded_short:        z.number().int().nonnegative(),
  cut_max_speech:         z.number().int().nonnegative(),
  stt_errors:             z.number().int().nonnegative(),
  confidence_count:       z.number().int().nonnegative(),
  confidence_p10:         pct,
  confidence_p50:         pct,
  confidence_p90:         pct,
  segmentation:           SpeechSegmentationSnapshotSchema,
  segmentation_scope:     SpeechSegmentationScopeSchema,
}).strict()
export type SttStreamSummaryEvent = z.infer<typeof SttStreamSummaryEventSchema>

export const CollectOutcomeEventSchema = z.object({
  ...Common,
  event_type:             z.literal("collect_outcome"),
  menu_id:                z.string(),
  interaction:            z.string(),
  inputs:                 z.array(z.enum(["dtmf", "voice", "text"])),
  /** `released` = o menu acabou sem desfecho da coleta (sessão fechou, menu novo, o motor desistiu). */
  outcome:                z.enum(["value", "invalid", "timeout", "released"]),
  via:                    z.enum(["dtmf", "voice", "screen"]).nullable(),
  release_reason:         z.enum(["session_closed", "replaced", "engine_released", "released"]).nullable(),
  speech_inputs:          z.number().int().nonnegative(),
  digit_inputs:           z.number().int().nonnegative(),
  invalid_attempts:       z.number().int().nonnegative(),
  invalid_low_confidence: z.number().int().nonnegative(),
  digit_after_speech:     z.boolean(),
  min_confidence:         z.number().nullable(),
  end_silence_ms:         z.number().int().nullable(),
  max_speech_ms:          z.number().int().nullable(),
  duration_ms:            z.number().int().nonnegative(),
}).strict()
export type CollectOutcomeEvent = z.infer<typeof CollectOutcomeEventSchema>

export const SpeechMetricsEventSchema = z.discriminatedUnion("event_type", [
  SttStreamSummaryEventSchema,
  CollectOutcomeEventSchema,
])
export type SpeechMetricsEvent = z.infer<typeof SpeechMetricsEventSchema>
