"""
dialog_form_pin.py — resolve a VERSÃO publicada de um DialogForm, para o pin.

S1 do `adr-deploy-time-content-snapshot` (caminho A, decidido 2026-08-29) e D14 do
`adr-dialog-tree-options` (2026-09-05).

── O problema que isto fecha ────────────────────────────────────────────────────

O formulário é lido DUAS vezes e nada as amarra: o renderer, quando o atendente
reivindica o item, e o `segment_outcome_record`, no submit. Entre as duas há
`timeout_s: -1` e um ACW de até 24 h. Se alguém publicar uma versão nova nesse
intervalo, o submit deriva os eventos da árvore NOVA sobre uma resposta dada na
VELHA — um caminho que era folha pode ter virado pasta, e a categoria sai
reinterpretada **sem nada ficar vermelho**.

O pin não remove a segunda leitura: faz as duas apontarem para a MESMA versão. A
corrida deixa de ser eliminada e passa a ser **inócua**.

── Por que aqui, e por que sem cache ───────────────────────────────────────────

**Quem grava é o SERVIDOR** — requisito inegociável da S1. Vindo do payload do
cliente, um browser escolheria qual versão do formulário descreve a própria
resposta, que é a família da recusa de deixar o renderizador ecoar os `captures`.
Este módulo é chamado no `delegate`, que é servidor e acontece ANTES de qualquer
render.

**Sem cache, de propósito.** O `DialogFormMaskProbe` vizinho cacheia 300 s, e ali
isso é correto — a pergunta dele ("este form mascara?") tolera atraso. Aqui não:
cachear faria um form publicado há 10 s ser pinado na versão anterior pelos 5 min
seguintes, e o atendente veria um documento velho sem que nada o dissesse. É uma
chamada HTTP por delegate, num caminho que já faz várias.

── Degradação ──────────────────────────────────────────────────────────────────

Não resolveu ⇒ devolve `None` e **loga nomeando o que deixa de valer**. Ausência
do pin é exatamente o comportamento de hoje (cada leitura resolve "a última
publicada"), então degradar não quebra nada — mas o log tem de dizer QUAL garantia
caiu, senão vira o `fallback` mudo que a § Postura de Engenharia caça.

**Nunca inventa versão.** Um `1` chutado seria pior que a ausência: fixaria o
submit num documento que pode não ser o exibido, com cara de garantia.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

__all__ = ["resolve_published_version"]


async def resolve_published_version(
    dialog_api_url: str,
    tenant_id:      str,
    form_id:        str,
    timeout_s:      float = 5.0,
) -> int | None:
    """
    Devolve a `version` da forma PUBLICADA, ou `None` — nunca um palpite.

    `None` significa *"não foi possível pinar"*, e o chamador deve simplesmente
    omitir a tag: sem ela, cada leitura resolve a última publicada, que é o
    comportamento anterior a este mecanismo.
    """
    if not form_id:
        return None

    if not dialog_api_url:
        logger.warning(
            "dialog_form_pin: dialog_api_url VAZIA — o form %s NÃO será pinado. "
            "Sem pin, o render e o submit resolvem 'a última publicada' de forma "
            "independente: uma republicação durante o ACW faz o submit derivar os "
            "eventos de uma árvore que o atendente não viu.",
            form_id,
        )
        return None

    url = dialog_api_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.get(
                f"{url}/v1/dialog/forms/{form_id}",
                params={"status": "published"},
                headers={"X-Tenant-ID": tenant_id},
            )
            r.raise_for_status()
            form = r.json()
    except Exception as exc:  # noqa: BLE001 — o motivo vai no log, nunca some
        logger.warning(
            "dialog_form_pin: não consegui ler o form %s na dialog-api (%s) — "
            "seguindo SEM pin. Consequência nomeada: render e submit podem ler "
            "versões diferentes se houver publicação durante o ACW.",
            form_id, exc,
        )
        return None

    version = form.get("version")
    # `isinstance(version, bool)` é excluído de propósito: em Python `True` é `int`,
    # e um JSON malformado com `"version": true` viraria o pin da versão 1.
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        logger.warning(
            "dialog_form_pin: form %s publicado sem `version` inteira utilizável "
            "(recebido %r) — seguindo SEM pin em vez de inventar uma.",
            form_id, version,
        )
        return None

    logger.info("dialog_form_pin: form=%s pinado na versão %d", form_id, version)
    return version
