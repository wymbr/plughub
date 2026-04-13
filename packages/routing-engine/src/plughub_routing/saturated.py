"""
saturated.py
Saturated pool policy — spec PlugHub v24.0 section 3.3a.

When both the primary pool and fallback are simultaneously unavailable:

  Channel         Behaviour
  ─────────────── ──────────────────────────────────────────────────────
  Voice           Priority queue + capacity alert if KEDA does not
                  resolve within 60s.

  Chat/WhatsApp   Wait message + async callback option via
                  Pending Delivery Store.

  Email           Receipt confirmation with expanded SLA (2× the pool's
                  default SLA).

  Urgent voice    sla_urgency > 2.0 → redirect to secondary site
  (sla_urgency    + CRITICAL alert to oncall.
  > 2.0)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

from .models import CustomerProfile, PoolConfig

SaturationActionType = Literal[
    "queue_voice",         # Voice: priority queue
    "queue_with_callback", # Chat/WhatsApp: queue + callback option
    "email_confirmation",  # Email: confirmation with expanded SLA
    "redirect_secondary",  # Urgent voice: redirect to secondary site
]


@dataclass(frozen=True)
class SaturationAction:
    action_type:    SaturationActionType
    estimated_eta_ms: int | None    # estimated service ETA in ms
    expanded_sla_ms: int | None     # expanded SLA (for email)
    alert_oncall:   bool            # whether to fire an oncall alert
    alert_keda:     bool            # whether to trigger KEDA scale-out
    secondary_site: str | None      # site for redirect (urgent voice)
    message:        str             # message for logging / notification


class SaturationHandler:
    """
    Applies saturation policy by channel and urgency.
    Spec 3.3a.
    """

    def __init__(self, keda_alert_timeout_seconds: int = 60) -> None:
        self._keda_timeout = keda_alert_timeout_seconds

    def handle(
        self,
        channel:      str,
        sla_urgency:  float,
        pool:         PoolConfig,
        profile:      CustomerProfile,
    ) -> SaturationAction:
        """
        Returns the appropriate saturation action for the channel and urgency.

        Args:
            channel:     origin channel of the contact
            sla_urgency: oldest_queue_wait_ms / pool sla_target_ms
            pool:        configuration of the saturated pool
            profile:     customer profile
        """
        # Voice with sla_urgency > 2.0 — most critical case, evaluated first
        if channel == "voice" and sla_urgency > 2.0:
            secondary = pool.remote_sites[0] if pool.remote_sites else None
            return SaturationAction(
                action_type      = "redirect_secondary",
                estimated_eta_ms = None,
                expanded_sla_ms  = None,
                alert_oncall     = True,
                alert_keda       = True,
                secondary_site   = secondary,
                message          = (
                    f"[CRITICAL] Pool {pool.pool_id} saturated with sla_urgency={sla_urgency:.2f}. "
                    f"Redirecting to secondary site: {secondary}. "
                    "Oncall alert fired."
                ),
            )

        # Standard voice — priority queue + KEDA alert after 60s
        if channel == "voice":
            congestion_sla = int(pool.sla_target_ms * 1.5)
            return SaturationAction(
                action_type      = "queue_voice",
                estimated_eta_ms = congestion_sla,
                expanded_sla_ms  = None,
                alert_oncall     = False,
                alert_keda       = True,   # KEDA must resolve within 60s
                secondary_site   = None,
                message          = (
                    f"Pool {pool.pool_id} saturated (voice). "
                    f"Customer placed in priority queue. Estimated ETA: {congestion_sla}ms. "
                    f"KEDA alert triggered (timeout {self._keda_timeout}s)."
                ),
            )

        # Chat / WhatsApp — wait message + callback option
        if channel in ("chat", "whatsapp"):
            congestion_sla = int(pool.sla_target_ms * 1.5)
            return SaturationAction(
                action_type      = "queue_with_callback",
                estimated_eta_ms = congestion_sla,
                expanded_sla_ms  = None,
                alert_oncall     = False,
                alert_keda       = False,
                secondary_site   = None,
                message          = (
                    f"Pool {pool.pool_id} saturated ({channel}). "
                    "Sending wait message to customer. "
                    "Async callback option available via Pending Delivery Store."
                ),
            )

        # Email — receipt confirmation with expanded SLA (2× the default)
        if channel == "email":
            expanded_sla = pool.sla_target_ms * 2
            return SaturationAction(
                action_type      = "email_confirmation",
                estimated_eta_ms = expanded_sla,
                expanded_sla_ms  = expanded_sla,
                alert_oncall     = False,
                alert_keda       = False,
                secondary_site   = None,
                message          = (
                    f"Pool {pool.pool_id} saturated (email). "
                    f"Sending receipt confirmation with expanded SLA: {expanded_sla}ms "
                    f"(2× the default of {pool.sla_target_ms}ms)."
                ),
            )

        # Other channels (sms, webrtc) — default handling same as chat
        congestion_sla = int(pool.sla_target_ms * 1.5)
        return SaturationAction(
            action_type      = "queue_with_callback",
            estimated_eta_ms = congestion_sla,
            expanded_sla_ms  = None,
            alert_oncall     = False,
            alert_keda       = False,
            secondary_site   = None,
            message          = (
                f"Pool {pool.pool_id} saturated ({channel}). "
                f"Customer queued. Estimated ETA: {congestion_sla}ms."
            ),
        )
