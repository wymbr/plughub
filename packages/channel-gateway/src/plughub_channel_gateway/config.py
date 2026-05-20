"""
config.py
Channel Gateway settings loaded from environment variables.
Spec: PlugHub v24.0 section 3.5
"""

from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_", case_sensitive=False)

    # Kafka
    kafka_brokers:              str = "localhost:9092"
    kafka_group_id:             str = "channel-gateway-webchat"
    kafka_topic_inbound:        str = "conversations.inbound"
    kafka_topic_outbound:       str = "conversations.outbound"
    kafka_topic_events:         str = "conversations.events"

    # Redis
    redis_url:                  str = "redis://localhost:6379"

    # Entry point pool — backward-compat fallback for single-pool deployments.
    # The preferred way to set the pool is via the URL path: /ws/chat/{pool_id}.
    # This env var is only used when pool_id is absent from the URL (e.g. older
    # docker-compose configs that use a fixed /ws/chat endpoint).
    # Set via PLUGHUB_ENTRY_POINT_POOL_ID.
    # Example: "sac_ia" — clients connecting to /ws/chat are routed to sac_ia.
    entry_point_pool_id:        str = ""

    # Tenant identifier published in routing events.
    # Defaults to the Kafka group_id for backward compatibility.
    tenant_id:                  str = "default"

    # Agent Registry — used for channel endpoint lookup (Layer 2).
    # Set via PLUGHUB_AGENT_REGISTRY_URL.
    # Example: "http://agent-registry:3000"
    agent_registry_url:         str = "http://localhost:3000"
    # In-process TTL (seconds) for channel endpoint lookups.
    # Keeps hot-path latency low while reflecting config changes within ~30s.
    # Set via PLUGHUB_ENDPOINT_CACHE_TTL_S.
    endpoint_cache_ttl_s:       int = 30

    # WebSocket
    ws_heartbeat_interval_s:    int = 30
    ws_connection_timeout_s:    int = 300   # close if idle for 5 min
    ws_contact_max_duration_s:  int = 14400 # 4h max contact duration

    # Session Redis TTL (matches contact max duration)
    session_ttl_seconds:        int = 14400

    # WebSocket auth
    # JWT HS256 secret used to validate customer tokens.
    # In production, override via PLUGHUB_JWT_SECRET env var.
    jwt_secret:                 str = "changeme_32chars_webchat_secret!"
    # How long the server waits for conn.authenticate after conn.hello.
    ws_auth_timeout_s:          int = 30

    # Attachment storage backend selector
    # "filesystem" (default, phase 1) — local disk + PostgreSQL metadata
    # "s3"         (phase 2)          — S3-compatible object storage + PostgreSQL metadata
    attachment_store_type:      str = "filesystem"

    # Attachment storage (filesystem phase 1)
    # Root directory for uploaded attachments.  Override via PLUGHUB_STORAGE_ROOT.
    storage_root:               str = "/var/plughub/attachments"
    # Files are soft-deleted after this many days (matched to session TTL policy).
    attachment_expiry_days:     int = 30
    # PostgreSQL DSN for attachment metadata (session_attachments table).
    database_url:               str = "postgresql://plughub:plughub@localhost:5432/plughub"

    # Attachment storage (S3/MinIO phase 2)
    # endpoint_url: empty = AWS S3; set to http://minio:9000 for MinIO.
    s3_endpoint_url:            str = ""
    s3_bucket:                  str = "plughub-attachments"
    s3_access_key:              str = ""
    s3_secret_key:              str = ""
    s3_region:                  str = "us-east-1"

    # Public-facing URLs for attachment serving and upload endpoints.
    # Override to match the actual host/TLS termination layer.
    webchat_serving_base_url:   str = "http://localhost:8010/webchat/v1/attachments"
    webchat_upload_base_url:    str = "http://localhost:8010/webchat/v1/upload"

    # ── SMS (Twilio / ISMSProvider) ───────────────────────────────────────────
    # Twilio Account SID. Can be overridden per-tenant via Redis:
    # {tenant_id}:config:sms:account_sid
    sms_account_sid:             str = ""
    # Twilio Auth Token for HMAC-SHA1 webhook verification and API calls.
    # Can be overridden per-tenant via Redis: {tenant_id}:config:sms:auth_token
    sms_auth_token:              str = ""
    # Twilio phone number (E.164) used as the sender for outbound SMS.
    # Can be overridden per-tenant via Redis: {tenant_id}:config:sms:from_number
    sms_from_number:             str = ""
    # SMS provider selector: "twilio" (default) or future providers.
    sms_provider:                str = "twilio"
    # Default pool_id used when creating a new SMS session.
    # The routing engine maps the pool to available agents.
    sms_default_pool_id:         str = ""

    # ── Email (Mailgun / IEmailProvider) ─────────────────────────────────────
    # Mailgun API key for outbound sending.
    # Per-mailbox override via ChannelEndpoint metadata in agent-registry.
    email_api_key:               str = ""
    # Mailgun domain (e.g. "empresa.com" or "sandbox<hash>.mailgun.org").
    email_domain:                str = ""
    # Mailgun webhook signing key (HMAC-SHA256 verification).
    email_signing_key:           str = ""
    # Default From address for outbound emails (e.g. "suporte@empresa.com").
    email_from_address:          str = ""
    # Subdomain used for Reply-To addresses: reply+{session_id}@{reply_domain}
    # Requires Mailgun catch-all route on this subdomain.
    email_reply_domain:          str = ""
    # Default pool_id for new email sessions (overridden by ChannelEndpoint lookup).
    email_default_pool_id:       str = ""
    # Email provider selector: "mailgun" (default) or future providers.
    email_provider:              str = "mailgun"

    # ── Voice (Twilio / IVoiceProvider) ──────────────────────────────────────
    # Twilio Account SID for voice calls.
    # Per-DID override via ChannelEndpoint metadata in agent-registry.
    voice_account_sid:              str = ""
    # Twilio Auth Token for HMAC-SHA1 webhook verification and REST API calls.
    voice_auth_token:               str = ""
    # Twilio phone number (E.164) used as caller ID for outbound calls.
    # Inbound DID routing uses ChannelEndpoint (identifier = DID number).
    voice_from_number:              str = ""
    # Voice CPaaS provider selector: "twilio" (default) or future providers.
    voice_provider:                 str = "twilio"
    # Default pool_id for new voice sessions when no ChannelEndpoint matches the DID.
    voice_default_pool_id:          str = ""
    # STT provider: "deepgram" (default).
    voice_stt_provider:             str = "deepgram"
    # Deepgram API key for STT (and optionally Deepgram Aura TTS).
    voice_deepgram_api_key:         str = ""
    # STT language (BCP-47). Deepgram: pt-BR, en-US, es, etc.
    voice_stt_language:             str = "pt-BR"
    # TTS provider: "twilio_say" (Twilio built-in <Say>, no extra API)
    #               "deepgram_aura" (Deepgram Aura REST, higher quality)
    voice_tts_provider:             str = "twilio_say"
    # Twilio Say / Deepgram Aura voice ID.
    # Twilio Say: "Polly.Camila-Neural" (PT-BR), "Polly.Joanna-Neural" (EN)
    # Deepgram Aura: "aura-asteria-en", "aura-zeus-en"
    voice_tts_voice_id:             str = "Polly.Camila-Neural"
    # Public HTTPS host for TwiML callback URLs sent to Twilio.
    # Example: "https://plughub.empresa.com". In dev, use an ngrok URL.
    voice_webhook_host:             str = "https://example.com"
    # Twilio hold music URL while customer waits in conference.
    # Empty = silence. Use a public MP3/WAV URL.
    voice_conference_wait_url:      str = ""
    # Enable STT transcription of the human agent's microphone (optional).
    # Requires dual-channel audio stream from CPaaS.
    voice_agent_stt_enabled:        bool = False
    # ElevenLabs TTS — primary high-quality TTS provider (non-Twilio).
    # Leave empty to fall back to TwilioSay (<Say> verb, no extra API).
    # Set via PLUGHUB_VOICE_ELEVENLABS_API_KEY.
    voice_elevenlabs_api_key:       str = ""
    # ElevenLabs voice ID.  Default: "Adam" (pNInz6obpgDQGcFmaJgB) — multilingual.
    # Browse voices: https://elevenlabs.io/voice-library
    voice_elevenlabs_voice_id:      str = "pNInz6obpgDQGcFmaJgB"
    # TTS fallback provider when primary fails.
    # "twilio_say" (default) — Twilio built-in <Say>, no external API required.
    # "deepgram_aura"        — Deepgram Aura REST TTS.
    voice_tts_fallback_provider:    str = "twilio_say"
    # STT fallback provider when Deepgram fails.
    # "mock" (default) — silent fallback, no STT output (safe for production).
    voice_stt_fallback_provider:    str = "mock"
    # Default TTS recording notice text played before each recorded segment.
    # Override per-tenant via Config API namespace "voice", key "recording_notice".
    voice_default_recording_notice: str = (
        "Esta chamada poderá ser gravada para fins de qualidade e treinamento."
    )

    # ── WhatsApp (Meta Cloud API) ─────────────────────────────────────────────
    # System User token from Meta Business Manager (WABA).
    # Can be overridden per-tenant via Redis: {tenant_id}:config:whatsapp:access_token
    whatsapp_access_token:      str = ""
    # Phone Number ID from Meta Developer Portal → WhatsApp → Phone Numbers.
    # Can be overridden per-tenant via Redis: {tenant_id}:config:whatsapp:phone_number_id
    whatsapp_phone_number_id:   str = ""
    # Shared secret used to verify the HMAC-SHA256 of inbound webhook payloads.
    # Set in Meta Developer Portal → WhatsApp → Configuration → Webhook → App Secret.
    whatsapp_app_secret:        str = ""
    # Token configured in Meta Developer Portal → WhatsApp → Configuration → Webhook.
    # Used to verify the GET challenge. Global per installation — no tenant routing.
    whatsapp_verify_token:      str = ""
    # Meta Graph API base URL — override for mocks / BSP proxies.
    whatsapp_graph_api_url:     str = "https://graph.facebook.com/v19.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
