import { describe, expect, it } from "vitest"
import { SpeechMetricsEventSchema } from "./speech-metrics"

// Formas copiadas de `channel-gateway/speech_metrics.py` (stream_summary / collect_outcome).
const resumo = {
  event_id: "4f1b1b2e-8f55-4c61-9a37-7d3c2a6f0b11", event_type: "stt_stream_summary",
  tenant_id: "tenant_demo", session_id: "s-1", pool_id: "p", channel: "webrtc", speaker: "customer",
  speech_profile_id: null, stt_model: "Systran/faster-whisper-small",
  stt_provider: "SpeachesSTTProvider", timestamp: "2026-09-17T12:00:00.000000+00:00",
  audio_ms: 60000, frames: 3000, voiced_frames: 400,
  noise_rms_p10: 0.0, noise_rms_p50: 12.5, noise_rms_p90: 80.1,
  utterances_sent: 5, utterances_transcribed: 5, discarded_vad: 1, discarded_short: 2,
  cut_max_speech: 0, stt_errors: 0, confidence_count: 5,
  confidence_p10: 0.71, confidence_p50: 0.78, confidence_p90: 0.8,
  segmentation: { energy_threshold: 400.0, end_silence_ms: 700, gap_ms: 700, min_speech_ms: 250,
                  max_speech_ms: 15000, vad_filter: true },
  segmentation_scope: { energy_threshold: "global", end_silence_ms: "tenant", gap_ms: "default",
                        min_speech_ms: "global", max_speech_ms: "global", vad_filter: "global" },
}

const desfecho = {
  event_id: "9d0e6a57-2b0a-4a7e-8f4e-0b8e3d5b6c21", event_type: "collect_outcome",
  tenant_id: "tenant_demo", session_id: "s-1", pool_id: null, channel: "webrtc", speech_profile_id: null,
  timestamp: "2026-09-17T12:00:01+00:00", menu_id: "m", interaction: "button", inputs: ["dtmf", "voice"],
  outcome: "invalid", via: null, release_reason: null, speech_inputs: 1, digit_inputs: 0,
  invalid_attempts: 1, invalid_low_confidence: 1, digit_after_speech: false,
  min_confidence: 0.99, end_silence_ms: null, max_speech_ms: null, duration_ms: 4200,
}

describe("speech.metrics (VOZ-22)", () => {
  it("aceita as duas formas que o gateway publica", () => {
    expect(SpeechMetricsEventSchema.safeParse(resumo).success).toBe(true)
    expect(SpeechMetricsEventSchema.safeParse(desfecho).success).toBe(true)
  })

  it("percentil sem amostra é null, e o evento continua válido", () => {
    expect(SpeechMetricsEventSchema.safeParse({ ...resumo, confidence_p50: null, confidence_count: 0 }).success).toBe(true)
  })

  it("RECUSA texto: um campo de transcrição ou de valor não passa calado", () => {
    expect(SpeechMetricsEventSchema.safeParse({ ...resumo, transcript: "meu cpf" }).success).toBe(false)
    expect(SpeechMetricsEventSchema.safeParse({ ...desfecho, value: "correio" }).success).toBe(false)
  })

  it("VOZ-25: o perfil em vigor e o escopo `profile` são aceitos; sem o campo, recusado", () => {
    const comPerfil = {
      ...resumo, speech_profile_id: "sip-g711",
      segmentation_scope: { ...resumo.segmentation_scope, end_silence_ms: "profile" },
    }
    expect(SpeechMetricsEventSchema.safeParse(comPerfil).success).toBe(true)
    expect(SpeechMetricsEventSchema.safeParse({ ...desfecho, speech_profile_id: "sip-g711" }).success).toBe(true)
    const { speech_profile_id: _omitido, ...semCampo } = desfecho
    expect(SpeechMetricsEventSchema.safeParse(semCampo).success).toBe(false)
    expect(SpeechMetricsEventSchema.safeParse({
      ...resumo, segmentation_scope: { end_silence_ms: "perfil" },
    }).success).toBe(false)
  })

  it("desfecho fora do domínio é recusado", () => {
    expect(SpeechMetricsEventSchema.safeParse({ ...desfecho, outcome: "ok" }).success).toBe(false)
  })
})
