"""
test_saturated.py
SaturationHandler tests — spec PlugHub v24.0 section 3.3a.

Covers:
  - Saturated voice channel: alert_keda=True with configurable 60s timeout
  - Urgent voice (sla_urgency > 2.0): redirect_secondary + alert_oncall
  - Chat/WhatsApp channel: queue_with_callback, no KEDA alert
  - Email channel: email_confirmation with expanded SLA
"""

import pytest
from ..saturated import SaturationHandler, SaturationAction
from ..models import CustomerProfile, PoolConfig, RoutingExpression


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

def _pool(pool_id: str = "retencao_humano", channel_types=None, **kwargs) -> PoolConfig:
    return PoolConfig(
        pool_id            = pool_id,
        tenant_id          = "tenant_test",
        channel_types      = channel_types or ["voice", "webchat"],
        sla_target_ms      = 480_000,
        routing_expression = RoutingExpression(
            weight_sla=1.0, weight_wait=0.8,
            weight_tier=0.6, weight_churn=0.9, weight_business=0.4,
        ),
        **kwargs,
    )


def _profile(tier: str = "standard") -> CustomerProfile:
    return CustomerProfile(tier=tier, churn_risk=0.0, business_score=0.0, risk_flag=False)


# ─────────────────────────────────────────────
# Voice channel: alert_keda + 60s timeout
# ─────────────────────────────────────────────

class TestVoiceSaturation:
    def test_saturated_voice_triggers_alert_keda(self):
        """
        Saturated voice pool must trigger alert_keda=True.
        KEDA must resolve the scale-out within 60s (default).
        Spec 3.3a: "Priority queue + capacity alert if KEDA does not resolve within 60s."
        """
        handler = SaturationHandler(keda_alert_timeout_seconds=60)
        pool = _pool(channel_types=["voice"])

        action = handler.handle(
            channel     = "voice",
            sla_urgency = 1.1,   # saturated but has not exceeded 2.0
            pool        = pool,
            profile     = _profile(),
        )

        assert action.action_type == "queue_voice"
        assert action.alert_keda is True
        assert action.alert_oncall is False
        # Message must mention KEDA timeout of 60s
        assert "60" in action.message or "60s" in action.message

    def test_saturated_voice_keda_timeout_configurable(self):
        """
        keda_alert_timeout_seconds is configurable — default 60s, but can be adjusted.
        """
        handler_30s  = SaturationHandler(keda_alert_timeout_seconds=30)
        handler_120s = SaturationHandler(keda_alert_timeout_seconds=120)
        pool = _pool(channel_types=["voice"])

        action_30  = handler_30s.handle("voice",  1.2, pool, _profile())
        action_120 = handler_120s.handle("voice", 1.2, pool, _profile())

        assert action_30.alert_keda  is True
        assert action_120.alert_keda is True
        assert "30"  in action_30.message
        assert "120" in action_120.message

    def test_urgent_voice_sla_above_2_redirects_to_secondary_site(self):
        """
        sla_urgency > 2.0 on voice channel is the critical case:
        - action_type = "redirect_secondary"
        - alert_oncall = True
        - alert_keda = True
        Spec 3.3a: "sla_urgency > 2.0 → redirect to secondary site + CRITICAL alert to oncall."
        """
        handler = SaturationHandler()
        pool = _pool(
            channel_types = ["voice"],
            remote_sites  = ["site-backup.plughub.io"],
        )

        action = handler.handle(
            channel     = "voice",
            sla_urgency = 2.5,   # critical: above 2.0
            pool        = pool,
            profile     = _profile(),
        )

        assert action.action_type   == "redirect_secondary"
        assert action.alert_oncall  is True
        assert action.alert_keda    is True
        assert action.secondary_site == "site-backup.plughub.io"
        assert "CRITICAL" in action.message

    def test_urgent_voice_without_secondary_site_configured(self):
        """
        sla_urgency > 2.0 without remote_sites configured:
        secondary_site must be None but alerts are still fired.
        """
        handler = SaturationHandler()
        pool = _pool(channel_types=["voice"])  # no remote_sites

        action = handler.handle("voice", 2.1, pool, _profile())

        assert action.action_type  == "redirect_secondary"
        assert action.alert_oncall is True
        assert action.secondary_site is None


# ─────────────────────────────────────────────
# Chat/WhatsApp channel: no KEDA alert
# ─────────────────────────────────────────────

class TestChatWhatsAppSaturation:
    def test_saturated_chat_no_keda_no_oncall(self):
        """
        Saturated chat: queue_with_callback, NO alert_keda, NO alert_oncall.
        Spec 3.3a: "Wait message + async callback option."
        """
        handler = SaturationHandler()
        pool = _pool(channel_types=["webchat"])

        action = handler.handle("webchat", 1.3, pool, _profile())

        assert action.action_type == "queue_with_callback"
        assert action.alert_keda   is False
        assert action.alert_oncall is False
        assert action.estimated_eta_ms is not None

    def test_saturated_whatsapp_same_as_chat(self):
        """WhatsApp follows the same policy as chat."""
        handler = SaturationHandler()
        pool = _pool(channel_types=["whatsapp"])

        action = handler.handle("whatsapp", 1.0, pool, _profile())

        assert action.action_type == "queue_with_callback"
        assert action.alert_keda   is False

    def test_estimated_eta_is_150_percent_of_sla_target(self):
        """Estimated ETA = sla_target_ms × 1.5 for chat and voice."""
        handler = SaturationHandler()
        pool = _pool(channel_types=["webchat"])  # sla_target_ms = 480_000

        action = handler.handle("webchat", 1.0, pool, _profile())

        assert action.estimated_eta_ms == int(480_000 * 1.5)


# ─────────────────────────────────────────────
# Email channel: expanded SLA
# ─────────────────────────────────────────────

class TestEmailSaturation:
    def test_saturated_email_confirms_receipt_with_expanded_sla(self):
        """
        Saturated email: receipt confirmation + SLA = 2× the default.
        No KEDA, no oncall.
        """
        handler = SaturationHandler()
        pool = _pool(channel_types=["email"])

        action = handler.handle("email", 0.8, pool, _profile())

        assert action.action_type   == "email_confirmation"
        assert action.alert_keda    is False
        assert action.alert_oncall  is False
        assert action.expanded_sla_ms == 480_000 * 2
        assert action.estimated_eta_ms == 480_000 * 2
