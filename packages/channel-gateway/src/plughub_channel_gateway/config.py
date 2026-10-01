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
    # Invalidação do cache in-process do endpoint_resolver. Consumido com group_id
    # ÚNICO POR PROCESSO — é broadcast, não fila: cada réplica tem o próprio cache
    # e precisa de TODOS os eventos. Ver registry_invalidation_consumer.py.
    kafka_topic_registry_changed: str = "registry.changed"
    # Session signals (survey web vehicle → analytics.session_signal). Same topic
    # survey_record (mcp-server) publishes to — the web submit reuses the trail.
    kafka_topic_signals:        str = "session.signals"

    # Dialog primitive — dialog-api (form store) URL. The survey web vehicle
    # snapshots the published DialogForm at token creation. PLUGHUB_DIALOG_API_URL.
    dialog_api_url:             str = "http://localhost:3760"
    # AUT-62 — a dialog-api exige credencial também na leitura. PLUGHUB_DIALOG_SERVICE_TOKEN
    # (== o da dialog-api). Vazio ⇒ 401 e cada leitor degrada logando. Ver `dialog_headers`.
    dialog_service_token:       str = ""
    # Survey web token TTL (seconds). Default 7 days.
    survey_web_ttl_s:           int = 604800
    # Survey response store (S8/S9) — evaluation-api persiste a resposta operacional
    # (verbatim/áudio LGPD) ANTES de emitir o session.signals. Default = nome de serviço
    # docker (demo); PLUGHUB_EVALUATION_API_URL sobrescreve. Token de serviço opcional
    # (evaluation-api usa _require_service, no-op quando vazio no demo).
    evaluation_api_url:         str = "http://evaluation-api:3400"
    evaluation_service_token:   str = ""

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

    # Credencial de serviço do agent-registry (= AGENT_REGISTRY_SERVICE_TOKEN lá).
    # Necessária para o resolver receber `token_hash` do endpoint — material de
    # credencial que a leitura GERAL não devolve, porque é o mesmo endpoint que a UI
    # consome. Ausente ⇒ a verificação de token não tem contra o quê comparar, e o
    # gateway RECUSA disparo em endpoint com `auth_required` (fail-closed, com log):
    # deixar passar seria transformar "não sei verificar" em "está autorizado".
    # Set via PLUGHUB_AGENT_REGISTRY_SERVICE_TOKEN.
    agent_registry_service_token: str = ""

    # IDN-06 — credencial INBOUND dos chamadores internos (mcp-server, mailing-api) nas
    # rotas `/v1/channels/webhook/identity/*` e `/pending/*`, via `X-Service-Token`.
    # Vazio NÃO libera: a porta de serviço fica fechada (401) e o boot avisa — nunca
    # "sem token configurado ⇒ aberto". Ver `identity_auth.py`.
    # Set via PLUGHUB_CHANNEL_GATEWAY_SERVICE_TOKEN.
    channel_gateway_service_token: str = ""

    # PID-09 — a chegada pelo WhatsApp vira evidência de posse, gravada pelo mcp-server
    # (`POST /internal/identity-evidence`, credencial = MCP_INTERNAL_SERVICE_TOKEN de lá).
    # Qualquer um vazio DESLIGA a evidência, com aviso no boot — o cliente volta ao OTP.
    # Set via PLUGHUB_MCP_SERVER_URL / PLUGHUB_MCP_INTERNAL_SERVICE_TOKEN.
    mcp_server_url:             str = ""
    mcp_internal_service_token: str = ""

    # Config API — source of horizontal config (webchat namespace etc.). Read via
    # the HTTP-backed WebchatConfigCache (config-http-propagation arc), NOT the
    # Redis cache directly. Set via PLUGHUB_CONFIG_API_URL. Example: http://config-api:3600
    config_api_url:             str = "http://localhost:3600"
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

    # ── Auth-api USER JWT (module_config ABAC) — standardized name ────────────
    # Must match PLUGHUB_AUTH_JWT_SECRET used by auth-api (same var analytics-api
    # consumes). Used to verify the approver's JWT on internal approval resume (A5)
    # → possessed-grade attribution + ABAC approvals.decide. Empty = internal
    # verification disabled (resume falls back to the external/claimed path).
    auth_jwt_secret:            str = ""

    # ── Routing Engine HTTP API (Frente 1 pull) ───────────────────────────────
    # Used to read the claim-lease holder for the A5 caller==claimant check on the
    # internal approval resume (the arbiter owns the lease — the gateway never reads
    # the routing Redis directly). Port = ROUTING_HTTP_PORT on the routing-engine
    # (default 3550). Set via PLUGHUB_ROUTING_ENGINE_URL.
    routing_engine_url:         str = "http://routing-engine:3550"
    # Admin token for the routing HTTP API (matches ROUTING_ADMIN_TOKEN on that side).
    routing_admin_token:        str = ""
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
    # ATT-05 — antivírus da esteira de anexos (clamd, protocolo INSTREAM). Wiring, não política:
    # vazio = não configurado, e todo anexo de contato fica em QUARENTENA (nunca servido sem varredura).
    clamav_host:                str = ""
    clamav_port:                int = 3310
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
    # AAS-03 — URL pública pela qual o mundo alcança este gateway; o AgentCard anuncia
    # `{base}/a2a/{slug}`. Wiring, não política: vazio = card não servido (503 + ERROR), nunca
    # adivinhado pelo `Host` da requisição.
    a2a_public_base_url:        str = ""

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

    # ── Speech (legacy provider pair of the WebRTC bot leg) ──────────────────
    # VOZ-03 (2026-09-28): the Twilio TwiML leg of the `voice` channel was RETIRED — the phone
    # call enters by the SIP trunk into the SFU room, and its speech is the WebRTC bot leg's.
    # What stays here is what that bot leg still reads: the Deepgram/ElevenLabs pair (used only
    # when `webrtc_speech_provider` is empty) and the env layer of the STT language (VOZ-17:
    # profile → tenant → this env). The `voice_` prefix is kept so no deploy's env changes.
    voice_deepgram_api_key:         str = ""
    # STT language (BCP-47) — last layer of the speech config, under profile and tenant.
    voice_stt_language:             str = "pt-BR"
    # Set via PLUGHUB_VOICE_ELEVENLABS_API_KEY.
    voice_elevenlabs_api_key:       str = ""
    # ElevenLabs voice ID.  Default: "Adam" (pNInz6obpgDQGcFmaJgB) — multilingual.
    voice_elevenlabs_voice_id:      str = "pNInz6obpgDQGcFmaJgB"

    # ── WebRTC (LiveKit SFU) ──────────────────────────────────────────────────
    # LiveKit server URL (WebSocket) as seen from INSIDE the deploy network — used by
    # the server API and by the bot leg. Example: "ws://livekit:7880".
    # ⚠️ Sem default desde a VOZ-01 (era "wss://localhost:7880", valor plausível que
    # apontava para lugar nenhum). Vazio = o provider RECUSA nomeando esta env.
    webrtc_livekit_url:             str = ""
    # URL the CLIENT (browser / Console) receives. Empty = same as the internal one,
    # and the adapter logs that once per process.
    webrtc_livekit_public_url:      str = ""
    # LiveKit API key + secret (from livekit-server config / env LIVEKIT_KEYS).
    # Used exclusively by Channel Gateway — never exposed to browsers.
    webrtc_livekit_api_key:         str = ""
    webrtc_livekit_api_secret:      str = ""
    # JWT token TTL (seconds) for LiveKit room participants.
    webrtc_token_ttl_s:             int = 3600
    # Default pool_id for WebRTC sessions when no ChannelEndpoint matches.
    webrtc_default_pool_id:         str = ""
    # Enable STT transcription of the customer audio track (Deepgram streaming).
    webrtc_stt_enabled:             bool = True
    # VOZ-05 — conversão de voz do bot leg. `speaches` = serviço AUTO-HOSPEDADO do compose
    # (faster-whisper + Piper, decisão do dono). Vazio = cai no par legado Deepgram/ElevenLabs,
    # que exige chave; sem nenhum, o bot leg fica INDISPONÍVEL e diz o que falta.
    # ⚠️ Os modelos têm de ser os que o serviço `speaches-models` provisiona.
    webrtc_speech_provider:         str = ""
    webrtc_speaches_url:            str = ""
    webrtc_stt_model:               str = "Systran/faster-whisper-small"
    webrtc_tts_model:               str = "speaches-ai/piper-pt_BR-faber-medium"
    webrtc_tts_voice:               str = "faber"
    # VOZ-06 — aviso de gravação de FÁBRICA. O texto do tenant mora no config-api
    # (`webrtc.recording_notice`, aba WebRTC) e vence este; este só vale com o config-api fora ou
    # a chave ausente/inválida, e o log diz qual respondeu (`recording_config.py`).
    webrtc_recording_notice:        str = (
        "Esta chamada poderá ser gravada para fins de qualidade e treinamento."
    )
    # Rascunho do egress: o `livekit-egress` e o gateway montam o MESMO volume NESTE caminho
    # (topologia, por isso env). O gateway lê o arquivo quando o egress termina, guarda no
    # AttachmentStore e apaga.
    webrtc_egress_output_dir:       str = "/var/plughub/webrtc-recordings"

    # ── Verificação ativa da fala (VOZ-23) — processo `speech-check`, não o gateway ──
    # Pool de calibração para onde o endpoint TEMPORÁRIO da verificação aponta (fixture seedada).
    speech_check_pool_id:           str = "speech_check"
    # Onde o executor liga, como um cliente: o gateway pela rede do compose.
    speech_check_gateway_ws_url:    str = "ws://channel-gateway:8010"
    # Credencial de quem pede a verificação (mcp-server). VAZIO = a rota RECUSA tudo (503).
    speech_check_service_token:     str = ""
    speech_check_port:              int = 3870
    # VOZ-27: onde o GATEWAY alcança o executor, para servir o botão "Executar agora" da tela.
    # Vazio ⇒ `POST /v1/speech-checks` recusa 503 nomeando a env; nunca finge ter pedido.
    speech_check_url:               str = ""

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
