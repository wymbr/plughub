"""
test_calculator.py
Unit tests for PricingCalculator.

Tests are pure (no DB I/O) — they mock pricing_db functions directly.
"""
from __future__ import annotations

import pytest
from datetime import date
from unittest.mock import AsyncMock, patch, MagicMock

from plughub_pricing_api.calculator import (
    PricingCalculator,
    InvoiceLineItem,
    ReserveGroup,
    Invoice,
    invoice_to_xlsx,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_resource(
    resource_type: str,
    quantity: int,
    pool_type: str = "base",
    reserve_pool_id: str | None = None,
    active: bool = True,
    label: str = "",
) -> dict:
    return {
        "id": "res-1",
        "tenant_id": "t1",
        "installation_id": "default",
        "resource_type": resource_type,
        "quantity": quantity,
        "pool_type": pool_type,
        "reserve_pool_id": reserve_pool_id,
        "active": active,
        "billing_unit": "monthly",
        "label": label,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


PRICE_TABLE = {
    "unit_prices": {
        "ai_agent":       120.00,
        "human_agent":     50.00,
        "whatsapp_number": 15.00,
        "voice_trunk_in":  40.00,
    },
    "reserve_markup_pct": 10.0,
    "currency": "BRL",
}


# ── TestUnitPrice ──────────────────────────────────────────────────────────────

class TestUnitPrice:
    def test_known_resource(self):
        calc = PricingCalculator(MagicMock(), PRICE_TABLE)
        assert calc.unit_price("ai_agent") == 120.00

    def test_unknown_resource_returns_zero(self):
        calc = PricingCalculator(MagicMock(), PRICE_TABLE)
        assert calc.unit_price("unknown_resource") == 0.0

    def test_reserve_markup_applied(self):
        calc = PricingCalculator(MagicMock(), PRICE_TABLE)
        # 120 × 1.10 = 132.0
        assert calc.reserve_unit_price("ai_agent") == pytest.approx(132.0)

    def test_reserve_markup_zero(self):
        calc = PricingCalculator(MagicMock(), {**PRICE_TABLE, "reserve_markup_pct": 0.0})
        assert calc.reserve_unit_price("ai_agent") == pytest.approx(120.0)

    def test_currency_from_price_table(self):
        calc = PricingCalculator(MagicMock(), PRICE_TABLE)
        assert calc._currency == "BRL"

    def test_currency_default(self):
        calc = PricingCalculator(MagicMock(), {})
        assert calc._currency == "BRL"


# ── TestBaseCalculation ────────────────────────────────────────────────────────

class TestBaseCalculation:
    @pytest.fixture
    def resources(self):
        return [
            make_resource("ai_agent",    5, pool_type="base"),
            make_resource("human_agent", 10, pool_type="base"),
        ]

    @pytest.mark.asyncio
    async def test_base_items_count(self, resources):
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock) as m_list, \
             patch("plughub_pricing_api.calculator.pricing_db.count_active_days", new_callable=AsyncMock) as m_days:
            m_list.return_value = resources
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", "default", date(2026, 1, 1), date(2026, 1, 31))
        assert len(inv.base_items) == 2
        assert len(inv.reserve_groups) == 0

    @pytest.mark.asyncio
    async def test_base_subtotal(self, resources):
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock) as m_list:
            m_list.return_value = resources
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", "default", date(2026, 1, 1), date(2026, 1, 31))
        # ai: 5×120 + human: 10×50 = 600+500 = 1100
        assert inv.base_total == pytest.approx(1100.0)
        assert inv.grand_total == pytest.approx(1100.0)

    @pytest.mark.asyncio
    async def test_base_item_fields(self, resources):
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock) as m_list:
            m_list.return_value = resources
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", "default", date(2026, 1, 1), date(2026, 1, 31))
        ai = next(i for i in inv.base_items if i.resource_type == "ai_agent")
        assert ai.quantity     == 5
        assert ai.unit_price   == 120.0
        assert ai.days_active  is None   # base items don't have days_active
        assert ai.billing_days == 31

    @pytest.mark.asyncio
    async def test_zero_quantity_item_included(self):
        resources = [make_resource("ai_agent", 0)]
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock) as m_list:
            m_list.return_value = resources
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", "default", date(2026, 1, 1), date(2026, 1, 31))
        assert inv.base_items[0].subtotal == 0.0


# ── TestReserveCalculation ─────────────────────────────────────────────────────

class TestReserveCalculation:
    @pytest.fixture
    def resources(self):
        return [
            make_resource("ai_agent",    2, pool_type="reserve",
                         reserve_pool_id="peak_pool", active=True),
            make_resource("human_agent", 3, pool_type="reserve",
                         reserve_pool_id="peak_pool", active=True),
        ]

    @pytest.mark.asyncio
    async def test_reserve_group_created(self, resources):
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock) as m_list, \
             patch("plughub_pricing_api.calculator.pricing_db.count_active_days", new_callable=AsyncMock, return_value=10):
            m_list.return_value = resources
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", "default", date(2026, 1, 1), date(2026, 1, 31))
        assert len(inv.reserve_groups) == 1
        assert inv.reserve_groups[0].pool_id == "peak_pool"

    @pytest.mark.asyncio
    async def test_reserve_days_active(self, resources):
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock) as m_list, \
             patch("plughub_pricing_api.calculator.pricing_db.count_active_days", new_callable=AsyncMock, return_value=10):
            m_list.return_value = resources
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", "default", date(2026, 1, 1), date(2026, 1, 31))
        assert inv.reserve_groups[0].days_active == 10

    @pytest.mark.asyncio
    async def test_reserve_subtotal_with_markup(self, resources):
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock) as m_list, \
             patch("plughub_pricing_api.calculator.pricing_db.count_active_days", new_callable=AsyncMock, return_value=10):
            m_list.return_value = resources
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", "default", date(2026, 1, 1), date(2026, 1, 31))
        # ai: 2 × 132 / 31 × 10 + human: 3 × 55 / 31 × 10
        ai_sub = 2 * 132.0 / 31 * 10
        hu_sub = 3 * 55.0  / 31 * 10
        expected = ai_sub + hu_sub
        assert inv.reserve_total == pytest.approx(expected, rel=1e-4)

    @pytest.mark.asyncio
    async def test_reserve_zero_days_zero_cost(self, resources):
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock) as m_list, \
             patch("plughub_pricing_api.calculator.pricing_db.count_active_days", new_callable=AsyncMock, return_value=0):
            m_list.return_value = resources
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", "default", date(2026, 1, 1), date(2026, 1, 31))
        assert inv.reserve_total == 0.0

    @pytest.mark.asyncio
    async def test_multiple_reserve_pools(self):
        resources = [
            make_resource("ai_agent", 2, "reserve", "pool_a", active=True),
            make_resource("ai_agent", 1, "reserve", "pool_b", active=False),
        ]
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock) as m_list, \
             patch("plughub_pricing_api.calculator.pricing_db.count_active_days", new_callable=AsyncMock, return_value=5):
            m_list.return_value = resources
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", "default", date(2026, 1, 1), date(2026, 1, 31))
        assert len(inv.reserve_groups) == 2
        pool_ids = {g.pool_id for g in inv.reserve_groups}
        assert pool_ids == {"pool_a", "pool_b"}


# ── TestBillingCycle ───────────────────────────────────────────────────────────

class TestBillingCycle:
    @pytest.mark.asyncio
    async def test_default_cycle_is_current_month(self):
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock, return_value=[]):
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1")
        assert inv.cycle_start.day == 1
        assert inv.cycle_end == inv.cycle_start.replace(
            day=__import__("calendar").monthrange(inv.cycle_start.year, inv.cycle_start.month)[1]
        )

    @pytest.mark.asyncio
    async def test_billing_days_february(self):
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock, return_value=[]):
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", cycle_start=date(2026, 2, 1), cycle_end=date(2026, 2, 28))
        assert inv.billing_days == 28

    @pytest.mark.asyncio
    async def test_billing_days_january(self):
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock, return_value=[]):
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", cycle_start=date(2026, 1, 1), cycle_end=date(2026, 1, 31))
        assert inv.billing_days == 31


# ── TestInvoiceToDict ──────────────────────────────────────────────────────────

class TestInvoiceToDict:
    @pytest.mark.asyncio
    async def test_to_dict_keys(self):
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock, return_value=[]):
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", cycle_start=date(2026, 1, 1), cycle_end=date(2026, 1, 31))
        d = inv.to_dict()
        for key in ("tenant_id", "cycle_start", "cycle_end", "billing_days",
                    "currency", "base_items", "reserve_groups",
                    "base_total", "reserve_total", "grand_total", "generated_at"):
            assert key in d

    @pytest.mark.asyncio
    async def test_totals_rounded_to_two_decimal(self):
        resources = [make_resource("ai_agent", 1)]
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock) as m_list:
            m_list.return_value = resources
            calc = PricingCalculator(MagicMock(), {**PRICE_TABLE, "unit_prices": {"ai_agent": 99.9999}})
            inv  = await calc.calculate("t1", cycle_start=date(2026, 1, 1), cycle_end=date(2026, 1, 31))
        d = inv.to_dict()
        # Should be rounded to 2 decimal places
        assert d["grand_total"] == round(d["grand_total"], 2)


# ── TestXlsxExport ────────────────────────────────────────────────────────────

class TestXlsxExport:
    @pytest.mark.asyncio
    async def test_returns_bytes(self):
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock, return_value=[]):
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", cycle_start=date(2026, 1, 1), cycle_end=date(2026, 1, 31))
        result = invoice_to_xlsx(inv)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_xlsx_magic_bytes(self):
        """openpyxl output starts with PK (ZIP magic bytes)."""
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock, return_value=[]):
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", cycle_start=date(2026, 1, 1), cycle_end=date(2026, 1, 31))
        result = invoice_to_xlsx(inv)
        assert result[:2] == b"PK"   # ZIP archive signature

    @pytest.mark.asyncio
    async def test_xlsx_with_reserve_groups(self):
        resources = [
            make_resource("ai_agent", 5, "base"),
            make_resource("ai_agent", 2, "reserve", "peak_pool", active=True, label="Peak Pool"),
        ]
        with patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock) as m_list, \
             patch("plughub_pricing_api.calculator.pricing_db.count_active_days", new_callable=AsyncMock, return_value=15):
            m_list.return_value = resources
            calc = PricingCalculator(MagicMock(), PRICE_TABLE)
            inv  = await calc.calculate("t1", cycle_start=date(2026, 1, 1), cycle_end=date(2026, 1, 31))
        result = invoice_to_xlsx(inv)
        assert isinstance(result, bytes) and len(result) > 1000
