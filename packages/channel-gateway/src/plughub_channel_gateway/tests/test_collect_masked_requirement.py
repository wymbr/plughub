"""
test_collect_masked_requirement.py
NIV-02 — a exigência de canal DERIVA da declaração `masked:` do DialogForm.

POR QUE ESTE TESTE EXISTE. A eleição de canal do `collect` tinha duas
implementações: `select_channel` (capability-aware, pura, testada) e
`_negotiate_channel` (a que roda). Medido em 2026-09-03: o produtor do evento que
alimenta a primeira tem **zero chamadores**, e a segunda **nunca perguntava** o que
o canal sabe fazer — enquanto o `requires` chegava até ela pelo corpo do POST e era
descartado sem uso.

As asserções vêm em pares, porque cada uma sozinha passa pelo motivo errado:

  * derivar × NÃO derivar — um probe que só verificasse *"exige masked_input"*
    ficaria verde numa implementação que exige sempre, e aí toda pesquisa NPS
    passaria a só poder sair por webchat.
  * eleger × RECUSAR — eleger o canal certo prova a preferência, não o portão. O
    que prova o portão é o mapa sem canal capaz levantar em vez de escolher o
    primeiro.
  * negociado × FIXO — o ramo `channel:` explícito não passa pelo negociador. Sem
    o par, a exigência derivada seria contornável escrevendo uma linha no YAML.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from plughub_channel_gateway.collect_requirements import (
    MASKED_INPUT,
    DialogFormMaskProbe,
    derive_collect_requires,
    form_masks,
)

# Recorte fiel de `infra/dialog/dialog_limite_solicitacao.json` — o ÚNICO dos 10
# DialogForms do tenant que declara campo mascarado (medido 2026-09-03).
FORM_MASCARA = {
    "form_id": "dialog_limite_solicitacao",
    "nodes": [
        {"id": "abertura", "kind": "statement", "text": {"pt-BR": "oi"}},
        {"id": "dados", "kind": "question", "output_key": "dados", "fields": [
            {"id": "nome",  "label": "Nome",  "type": "text"},
            {"id": "email", "label": "Email", "type": "text"},
            {"id": "valor", "label": "Valor", "type": "money"},
            {"id": "cvv",   "label": "CVV",   "type": "text", "masked": "card_cvv"},
        ]},
    ],
}

FORM_LIMPO = {
    "form_id": "dialog_nps_v1",
    "nodes": [
        {"id": "nps", "kind": "question", "output_key": "nps", "interaction": "text"},
    ],
}


# ── form_masks — o par detectar × não detectar ────────────────────────────────

def test_form_masks_detecta_campo_mascarado():
    assert form_masks(FORM_MASCARA) is True


def test_form_masks_nao_inventa_mascara_no_form_limpo():
    """Controle negativo. Sem ele, uma implementação que devolvesse sempre True
    passaria no teste acima e estreitaria a eleição de TODO collect."""
    assert form_masks(FORM_LIMPO) is False
    assert form_masks({"nodes": []}) is False
    assert form_masks(None) is False


def test_form_masks_ve_mascara_no_NIVEL_DO_NO():
    """`masked` existe em duas alturas no `QuestionNodeSchema` (nó e campo).
    Cobrir só a do campo deixaria a pergunta escalar mascarada passar."""
    no_mascarado = {"nodes": [
        {"id": "pin", "kind": "question", "output_key": "pin", "masked": "credential"},
    ]}
    assert form_masks(no_mascarado) is True


def test_form_masks_respeita_o_override_explicito_false():
    """`false` é o único jeito de dizer *"este campo NÃO mascara"* (T7-A mantém o
    literal justamente por isso). Tratá-lo como truthy transformaria o override em
    declaração, invertendo o que o autor escreveu."""
    assert form_masks({"nodes": [
        {"id": "q", "kind": "question", "output_key": "q", "fields": [
            {"id": "x", "label": "x", "type": "text", "masked": False},
        ]},
    ]}) is False


def test_form_masks_conta_o_true_legado():
    """A T7-B (tolerância do runtime a `true`) está adiada por decisão: um snapshot
    de slot anterior à T6 continua executável. Se ele executa, ele mascara — e a
    exigência de canal tem de acompanhar, senão o legado é o caminho sem portão."""
    assert form_masks({"nodes": [
        {"id": "q", "kind": "question", "output_key": "q", "masked": True},
    ]}) is True


# ── derive_collect_requires — acrescenta, nunca substitui ─────────────────────

def test_derive_acrescenta_masked_input():
    assert derive_collect_requires(None, True) == [MASKED_INPUT]


def test_derive_nao_acrescenta_quando_nao_mascara():
    assert derive_collect_requires(None, False) == []


def test_derive_preserva_o_declarado_pelo_autor():
    """A derivação ACRESCENTA. Substituir apagaria em silêncio um `requires:`
    escrito à mão — e o autor não teria como saber."""
    assert derive_collect_requires(["file_upload"], True) == ["file_upload", MASKED_INPUT]


def test_derive_nao_duplica():
    assert derive_collect_requires([MASKED_INPUT], True) == [MASKED_INPUT]


# ── DialogFormMaskProbe — degradação restritiva e NOMEADA ─────────────────────

@pytest.mark.asyncio
async def test_probe_sem_form_id_nao_exige_nada():
    """Collect sem `dialog_form_id` não renderiza formulário: não há declaração
    `masked:` alcançável, logo não há exigência a derivar. Exigir aqui estreitaria
    a eleição de todo collect de texto simples."""
    p = DialogFormMaskProbe("http://dialog:3760")
    assert await p.masks("t", "") == (False, "sem_form")


@pytest.mark.asyncio
async def test_probe_form_ilegivel_exige_por_precaucao():
    """Restritivo vence. O oposto — assumir *"não mascara"* — mandaria um CVV por
    SMS na primeira instabilidade do dialog-api, em silêncio."""
    p = DialogFormMaskProbe("")          # sem URL = uma das formas de ilegível
    mascara, motivo = await p.masks("t", "dialog_x")
    assert mascara is True
    assert motivo == "sem_dialog_api_url"


@pytest.mark.asyncio
async def test_probe_falha_de_rede_nao_e_cacheada(monkeypatch):
    """O `True` conservador de uma indisponibilidade de 2 s não pode estreitar a
    eleição pelos 5 min seguintes. Cachear a falha faria exatamente isso."""
    p = DialogFormMaskProbe("http://dialog:3760")
    mascara, motivo = await p.masks("t", "dialog_x")   # sem servidor → erro de rede
    assert (mascara, motivo) == (True, "form_ilegivel")
    assert p._cache == {}, "falha não pode entrar no cache"


# ── _negotiate_channel — o par eleger × recusar ───────────────────────────────

def _adapter():
    """WebhookAdapter sem __init__ (ele monta identity/OTP/Kafka). O que se exercita
    aqui é o negociador puro; `_reachable_channels` é o único colaborador."""
    from plughub_channel_gateway.adapters.webhook import WebhookAdapter
    a = WebhookAdapter.__new__(WebhookAdapter)
    a._reachable_channels = AsyncMock(return_value=[])
    return a


POLICY = {
    "channels":        {"sms": "pool_sms", "webchat": "pool_web"},
    "preferred_order": ["sms", "webchat"],
}


@pytest.mark.asyncio
async def test_negotiate_sem_exigencia_honra_a_preferencia():
    """Controle positivo. Sem ele, um negociador que recusasse tudo passaria nos
    testes de recusa abaixo."""
    canal, pool = await _adapter()._negotiate_channel("t", "c", POLICY)
    assert (canal, pool) == ("sms", "pool_sms")


@pytest.mark.asyncio
async def test_negotiate_com_masked_input_abandona_a_preferencia():
    """`sms` é o preferido e NÃO sabe mascarar. A capacidade tem de vencer a
    preferência — senão o portão existe e não decide nada."""
    canal, pool = await _adapter()._negotiate_channel("t", "c", POLICY, [MASKED_INPUT])
    assert (canal, pool) == ("webchat", "pool_web")


@pytest.mark.asyncio
async def test_negotiate_recusa_quando_nenhum_canal_mascara():
    """O par que prova o portão. Antes da NIV-02 este caso elegia `sms` e seguia."""
    so_sms = {"channels": {"sms": "pool_sms"}, "preferred_order": ["sms"]}
    with pytest.raises(ValueError) as exc:
        await _adapter()._negotiate_channel("t", "c", so_sms, [MASKED_INPUT])
    assert MASKED_INPUT in str(exc.value)


@pytest.mark.asyncio
async def test_negotiate_recusa_nomeia_a_causa():
    """A recusa chega ao autor como 409 do channel-gateway. Uma mensagem genérica
    faria o autor procurar no lugar errado: a exigência é DERIVADA, então ele não
    a escreveu em lugar nenhum e não tem por onde começar."""
    so_sms = {"channels": {"sms": "pool_sms"}}
    with pytest.raises(ValueError) as exc:
        await _adapter()._negotiate_channel("t", "c", so_sms, [MASKED_INPUT])
    msg = str(exc.value)
    assert "DERIVADA" in msg and "channel_policy.channels" in msg


# ── o ramo do `channel:` FIXO não escapa ──────────────────────────────────────

def _adapter_com_form(mascara: bool):
    from unittest.mock import MagicMock
    a = _adapter()
    a._read_ctx_root = AsyncMock(return_value="sess_root")
    probe = MagicMock()
    probe.masks = AsyncMock(return_value=(mascara, "declarado" if mascara else "sem_campo_mascarado"))
    a._form_mask = probe
    return a


_ARGS = dict(
    tenant_id="t", session_id="s", customer_id="c", step_id="st",
    collect_token="tok", target={"type": "customer", "id": "c"},
    interaction="form", prompt="p", dialog_form_id="dialog_limite_solicitacao",
)


@pytest.mark.asyncio
async def test_channel_fixo_incapaz_e_RECUSADO():
    """O portão que o autor pode desligar sozinho não e portao. Antes da NIV-02 o
    ramo `channel:` so conferia se o canal estava no mapa — escrever `channel: sms`
    contornava a eleicao inteira, e com ela qualquer exigencia de capacidade."""
    with pytest.raises(ValueError) as exc:
        await _adapter_com_form(True).handle_collect(
            channel="sms",
            channel_policy={"channels": {"sms": "pool_sms", "webchat": "pool_web"}},
            **_ARGS,
        )
    assert "sms" in str(exc.value) and MASKED_INPUT in str(exc.value)


@pytest.mark.asyncio
async def test_channel_fixo_incapaz_passa_quando_o_form_NAO_mascara():
    """Par obrigatorio do anterior: a recusa tem de vir da CAPACIDADE, nao do ramo.
    Sem este caso, uma implementacao que recusasse todo `channel:` fixo ficaria
    verde acima e quebraria todo collect de transporte fixo."""
    a = _adapter_com_form(False)
    # Falha adiante (Redis ausente), mas NAO com a recusa de capacidade — e isso
    # basta: o que se afirma aqui e que o portao deixou passar.
    try:
        await a.handle_collect(
            channel="sms",
            channel_policy={"channels": {"sms": "pool_sms"}},
            **_ARGS,
        )
    except ValueError as exc:
        assert MASKED_INPUT not in str(exc), f"recusou por capacidade sem mascara: {exc}"
    except Exception:
        pass   # qualquer outra falha e I/O ausente, nao o portao
