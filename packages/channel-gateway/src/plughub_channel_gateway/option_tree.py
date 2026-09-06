"""option_tree.py — a árvore de opções vista do CANAL (F2 do orquestrador por árvore).

── O defeito que este módulo fecha ──────────────────────────────────────────────

Um `menu` cujas opções carregam filhos era **achatado em silêncio**: o adapter
montava as linhas com `[o["label"] for o in options]` e os filhos simplesmente não
existiam para ele. Quando o FLUXO desce nível a nível (a navegação do orquestrador)
isso é inócuo — ele só precisa dos ids do topo. Mas um runner genérico que entrega
o `render` inteiro a um `menu` (`agente_nps_v1`, `skill_dialog_runner_v1`,
`skill_survey_multi_v1`, `skill_survey_runner_v1` — quatro medidos em 2026-09-06)
entregaria ao cliente só as PASTAS, e ele nunca alcançaria uma folha. Sem erro em
lugar nenhum: o menu renderiza, o cliente escolhe, e a resposta é um id de pasta
que o skill não espera.

⚠️ **Exposição ≠ dano.** Nenhuma das formas daqueles quatro runners tem árvore hoje
(medido: 2 formas com árvore em 14, e nenhuma delas vai por ali). Isto é uma guarda
contra o dia em que alguém acrescentar um nível — não o conserto de um dano vivo.

── O que o canal pode desenhar ─────────────────────────────────────────────────

A lista interativa do WhatsApp tem **seções tituladas**, e isso é exatamente uma
árvore de UM nível: pasta → título de seção, folha → linha. Onde cabe, o cliente
resolve a árvore inteira num turno em vez de dois.

⚠️ **A linha responde com o CAMINHO** (`sac.info_plano`), não com o id da folha.
Sem isso o cursor do fluxo quebraria: `optionsAtPath` procuraria `info_plano` na
RAIZ, não acharia, e a navegação reiniciaria parecendo certa. O `dialog_tree_level`
divide o `chosen_id` por ponto justamente para aceitar essa resposta, e o ponto é
separador seguro porque `DialogOptionSchema` proíbe ponto no id.
"""

from __future__ import annotations

from typing import Any, Optional

# Tetos da lista interativa do WhatsApp (Cloud API). Não são preferência: acima
# deles o provider REJEITA a mensagem, e o cliente não veria menu nenhum.
WA_MAX_ROWS = 10
WA_ROW_TITLE_MAX = 24
WA_SECTION_TITLE_MAX = 24


def _filhos(op: dict[str, Any]) -> list[dict[str, Any]]:
    f = op.get("options")
    return [x for x in f if isinstance(x, dict)] if isinstance(f, list) else []


def is_tree(options: Any) -> bool:
    """Alguma opção deste nível tem filhos?

    Gêmeo do `temArvore` de `@plughub/schemas/dialog-render.ts`. São duas
    linguagens, então não há como compartilhar código — o que há é paridade
    conferida por gate, como em `py-contextstore`.
    """
    if not isinstance(options, list):
        return False
    return any(isinstance(o, dict) and _filhos(o) for o in options)


def tree_depth(options: Any) -> int:
    """Profundidade máxima. Folha solta = 1; pasta com folhas = 2."""
    if not isinstance(options, list) or not options:
        return 0
    fundo = 0
    for o in options:
        if not isinstance(o, dict):
            continue
        fundo = max(fundo, 1 + tree_depth(_filhos(o)))
    return fundo


def flatten_to_sections(
    options: Any,
    *,
    max_rows: int = WA_MAX_ROWS,
    row_title_max: int = WA_ROW_TITLE_MAX,
    section_title_max: int = WA_SECTION_TITLE_MAX,
) -> Optional[list[dict[str, Any]]]:
    """Achata uma árvore de UM nível em seções tituladas, com id = CAMINHO.

    Devolve `None` quando o canal **não pode** desenhar — e quem chama tem de
    tratar isso como degradação NOMEADA, nunca como "renderiza o que der":

      · não é árvore  → não há o que agrupar (o caminho plano já serve);
      · profundidade > 2 → seção dentro de seção não existe no WhatsApp;
      · linhas > `max_rows` → o provider rejeitaria a mensagem inteira.

    Recusar aqui é o ponto: o chamador então mostra o NÍVEL CORRENTE e o fluxo
    desce, que é o comportamento antigo e correto. O que não pode acontecer é
    mandar as pastas como se fossem folhas.
    """
    if not is_tree(options) or tree_depth(options) > 2:
        return None

    secoes: list[dict[str, Any]] = []
    soltas: list[dict[str, Any]] = []
    linhas = 0

    for o in options:
        if not isinstance(o, dict):
            continue
        oid = str(o.get("id") or o.get("label") or "")
        rotulo = str(o.get("label") or oid)
        filhos = _filhos(o)

        if not filhos:
            soltas.append({"id": oid, "title": rotulo[:row_title_max]})
            linhas += 1
            continue

        rows = []
        for f in filhos:
            fid = str(f.get("id") or f.get("label") or "")
            rows.append({
                # O CAMINHO, não a folha — é o que torna o fluxo agnóstico de turno.
                "id": f"{oid}.{fid}",
                "title": str(f.get("label") or fid)[:row_title_max],
            })
            linhas += 1
        secoes.append({"title": rotulo[:section_title_max], "rows": rows})

    if soltas:
        # Folhas de primeiro nível não têm pasta. Seção sem título é legítima na
        # API; inventar um rótulo ("Outros") seria conteúdo que ninguém autorou.
        secoes.append({"rows": soltas})

    if linhas > max_rows or not secoes:
        return None
    return secoes
