"""
collect_requirements.py — a exigência de canal DERIVA da declaração `masked:`.

NIV-02 (ADR `adr-agent-flow-single-authored-level.md` § F2).

POR QUE ESTE MÓDULO EXISTE
==========================
`select_channel()` existe, é pura, é testada e **nunca recebeu uma exigência
real**: nenhum YAML do repositório declara `requires:`. A leitura óbvia disso é
*"falta o insumo"* — e ela está pela metade. Medido em 2026-09-03, antes de
escrever uma linha:

  1. **O caminho de `select_channel` está MORTO.** Ele é chamado de
     `main.py::_dispatch_collect`, consumidor de `collect.requested` no tópico
     `collect.events`. O ÚNICO produtor desse evento é
     `workflow_api.kafka_emitter.emit_collect_requested`, que tem **zero
     chamadores** (medido por AST, não por `grep` — o nome aparece só no
     `import`). As duas rotas de collect da workflow-api respondem **410** desde
     o Arc 19 Fase D. A analytics-api já sabia: `reports.py` carrega o comentário
     *"GATILHO: quando `collect_events` tiver produtor"*.
     ⇒ Alimentar `requires[]` ali não mudaria nada: não chega evento.

  2. **A eleição VIVA é outra, e é CEGA a capacidade.** O caminho real é
     `skill-flow-service` → `POST /v1/channels/webhook/collect` →
     `WebhookAdapter.handle_collect` → `_negotiate_channel`, que escolhe por
     `preferred_order` ∩ `channels` e **nunca pergunta o que o canal sabe
     fazer**.

  3. **E o `requires` JÁ CHEGA lá — e é jogado fora.** O engine o envia
     (`steps/collect.ts`), o `skill-flow-service` o repassa no corpo, o
     `main.py` o desempacota (`requires = body.get("requires")`) e o
     `handle_collect` o **declara no parâmetro e nunca lê** (medido por AST:
     zero usos em `Load` no corpo de 228 linhas).

São **duas implementações de eleição de canal**, e a que roda é a permissiva —
a mesma forma que a NIV-01 fechou um nível abaixo, no INVENTÁRIO de capacidade.
Por isso este módulo faz as duas metades juntas: sem (i) o insumo é inerte, sem
(ii) o insumo não é lido.

O QUE ESTE MÓDULO DECIDE
========================
* **A derivação tem UM sítio, e é este.** `derive_collect_requires` é pura e
  recebe o formulário já resolvido; a busca (com cache) fica em
  `DialogFormMaskProbe`, separada para que o veredicto seja testável sem rede.
* **A fonte da declaração é o `DialogForm`**, porque é lá que o `collect`
  mascara: `CollectStep.fields[]` **não tem** campo `masked` e não ganha um aqui
  — inventar um segundo sítio de declaração é exatamente o que a fatia proíbe.
  Se um dia ganhar, o lugar de somar é esta função, não outro `if` no adapter.
* **`masked` truthy = qualquer coisa que não seja `false`/ausente.** Depois da
  T6 ele nomeia um TIPO (`"card_cvv"`); `true` legado ainda executa (ver
  `masking-policy.ts::normalizeDecl`) e conta; `false` é o override explícito de
  *"este campo NÃO mascara"* e não conta.
* **Formulário ilegível ⇒ EXIGE `masked_input`, com log alto.** É "restritivo
  vence" aplicado à ELEIÇÃO, não uma recusa: o collect continua acontecendo, só
  que no canal que sabe mascarar. O oposto (assumir que não mascara) mandaria um
  CVV por SMS em silêncio na primeira instabilidade do dialog-api — trocar uma
  falha barulhenta por uma mentira tranquila.
  ⚠️ Se **nenhum** canal do mapa mascarar, o `_negotiate_channel` levanta
  `ValueError` → 409 → o engine lança. Isso é desejado e é a metade de runtime
  da NIV-03; aqui a exigência apenas passa a existir.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("plughub.channel-gateway.collect-requirements")

#: Capacidade exigida por um collect que carrega campo mascarado. String literal
#: do `ChannelCapabilitySchema` (@plughub/schemas/src/skill.ts) — o mesmo
#: vocabulário de `CHANNEL_CAPABILITIES`, que é a casa única da NIV-01.
MASKED_INPUT = "masked_input"


# ── Metade pura: o formulário mascara? ────────────────────────────────────────

def _decl_masks(value: Any) -> bool:
    """
    `masked` declarado ⇒ mascara, salvo o override explícito `false`.

    Depois da T6 o valor nomeia um TIPO do catálogo (`"card_cvv"`); `true` é a
    forma anônima legada, que o runtime ainda tolera de propósito. Só `false` e
    a ausência significam *"não mascara"*.
    """
    if value is None or value is False:
        return False
    return True


def form_masks(form: dict[str, Any] | None) -> bool:
    """
    True se qualquer nó do `DialogForm` coleta valor mascarado.

    Duas alturas, espelhando `QuestionNodeSchema`: a do NÓ (`masked` no
    question) e a do CAMPO (`fields[].masked`). Nós `statement` não coletam
    nada, então não são consultados — mas a varredura não ramifica por `kind`
    de propósito: um `kind` novo que colete entra coberto em vez de entrar mudo,
    que é o defeito que a tabela de capacidade da NIV-01 acabou de fechar.
    """
    if not form:
        return False
    for node in form.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        if _decl_masks(node.get("masked")):
            return True
        for field in node.get("fields") or []:
            if isinstance(field, dict) and _decl_masks(field.get("masked")):
                return True
    return False


def derive_collect_requires(
    declared:     list[str] | None,
    form_masked:  bool,
) -> list[str]:
    """
    O SÍTIO ÚNICO da derivação (NIV-02).

    `declared` é o que o autor escreveu em `collect.requires[]` — mantido, para
    que a derivação **acrescente** e nunca substitua. `form_masked` é o veredicto
    sobre o `DialogForm` referenciado.

    Ordem estável (declarados na ordem original, derivados depois) porque a lista
    entra em log e em mensagem de recusa: ordem instável faz duas execuções
    idênticas parecerem diferentes.
    """
    out = list(declared or [])
    if form_masked and MASKED_INPUT not in out:
        out.append(MASKED_INPUT)
    return out


# ── Metade com rede: ler o formulário, com cache e degradação nomeada ─────────

class DialogFormMaskProbe:
    """
    Responde *"o formulário `X` mascara?"* contra o dialog-api, com cache.

    O cache existe porque a resposta é **fato do FORMULÁRIO PUBLICADO**, não da
    chamada: uma campanha em fan-out dispararia N GETs idênticos no caminho de
    eleição. É TTL, e não invalidação por evento, porque publicar formulário **não
    emite `config.changed`** — prometer invalidação que ninguém dispara seria a
    promessa sem mecanismo que este repositório cataloga.

    ⚠️ **Os dois veredictos NÃO envelhecem no mesmo prazo, e a assimetria é a
    parte que importa.** Servir `True` velho superprotege (a eleição fica no canal
    que mascara — inócuo). Servir `False` velho SUBprotege: um formulário que
    acabou de ganhar campo mascarado continuaria elegendo SMS, e o autor veria a
    máscara "não funcionar" exatamente enquanto a testa. Logo `True` vive
    `ttl_s` (300 s) e `False` vive `ttl_negativo_s` (30 s) — longo o bastante para
    colapsar a rajada, curto o bastante para o autor não perseguir um fantasma.
    Mesma forma do `core.fileMode`: o lado restritivo é o que pode ficar.

    ⚠️ **Falha nunca é cacheada.** O `True` conservador de uma indisponibilidade de
    2 s não deve estreitar a eleição pelos 5 min seguintes.
    """

    def __init__(
        self,
        dialog_api_url: str,
        ttl_s:          int   = 300,
        ttl_negativo_s: int   = 30,
        timeout_s:      float = 5.0,
    ) -> None:
        self._url      = (dialog_api_url or "").rstrip("/")
        self._ttl      = ttl_s
        self._ttl_neg  = ttl_negativo_s
        self._timeout  = timeout_s
        self._cache: dict[tuple[str, str], tuple[float, bool]] = {}

    def _cached(self, key: tuple[str, str]) -> bool | None:
        hit = self._cache.get(key)
        if not hit:
            return None
        idade = time.monotonic() - hit[0]
        if idade < (self._ttl if hit[1] else self._ttl_neg):
            return hit[1]
        return None

    async def masks(self, tenant_id: str, form_id: str) -> tuple[bool, str]:
        """
        Devolve `(mascara, motivo)`. O motivo é sempre NOMEADO — quem lê o log
        precisa distinguir *"o form não mascara"* de *"não consegui ler o form"*,
        porque as duas produzem eleições diferentes e só a segunda é um problema.
        """
        if not form_id:
            return False, "sem_form"
        key = (tenant_id, form_id)
        hit = self._cached(key)
        if hit is not None:
            return hit, "cache"

        if not self._url:
            logger.warning(
                "collect: dialog_api_url VAZIA — não dá para saber se o form %s mascara; "
                "exigindo %s por precaução (a eleição estreita, o collect não para)",
                form_id, MASKED_INPUT,
            )
            return True, "sem_dialog_api_url"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.get(
                    f"{self._url}/v1/dialog/forms/{form_id}",
                    params={"status": "published"},
                    headers={"X-Tenant-ID": tenant_id},
                )
                r.raise_for_status()
                form = r.json()
        except Exception as exc:  # noqa: BLE001 — o motivo vai no log, não some
            logger.warning(
                "collect: form %s ILEGÍVEL no dialog-api (%s: %s) — exigindo %s por "
                "precaução. NÃO é recusa: a eleição fica restrita ao canal que sabe "
                "mascarar; se nenhum souber, o negotiate recusa alto (409)",
                form_id, type(exc).__name__, exc, MASKED_INPUT,
            )
            return True, "form_ilegivel"

        veredicto = form_masks(form)
        self._cache[(tenant_id, form_id)] = (time.monotonic(), veredicto)
        return veredicto, "declarado" if veredicto else "sem_campo_mascarado"
