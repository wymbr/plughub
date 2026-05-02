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


@lru_cache
def get_settings() -> Settings:
    return Settings()
