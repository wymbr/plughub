"""
usage_attribution.py — T2/D1–D3: a partir de QUANDO o consumo de LLM é atribuível.

Definição única para qualquer leitor de `usage_events` que agrupe por segmento,
conta ou modelo. Existe pelo mesmo motivo do `sla_source.py`, e a lição é a mesma.

────────────────────────────────────────────────────────────────────────────
O QUE MUDOU
────────────────────────────────────────────────────────────────────────────
A T1 (2026-08-28) ligou o produtor: até então `emit_llm_tokens` só disparava de
`POST /inference`, rota **sem chamador algum**. A T2 acrescentou ao evento a chave
de atribuição (`segment_id`) e a identidade da conta e do modelo, e promoveu tudo
a COLUNA no ClickHouse — que antes descartava o `metadata` inteiro no ingest.

────────────────────────────────────────────────────────────────────────────
A ÉPOCA — e por que ela não é decoração
────────────────────────────────────────────────────────────────────────────
É **forward-only por construção**: o `metadata` descartado no ingest não existe em
lugar nenhum, e o `segment_id` nunca viajou no evento. Não há backfill possível —
nem parcial, nem por inferência.

O que a época protege é a distinção entre **DUAS ausências de aparência idêntica**,
ambas gravadas como `''`:

  1. **não media** — evento anterior à T2; a coluna não existia.
  2. **não informado** — evento posterior, de um chamador que não passa a chave
     (ex.: um caminho novo que esquece de propagar o `segment_id`).

A (1) é história e não se conserta. A (2) é DEFEITO e tem de aparecer. Sem o corte,
as duas viram um balde só chamado "desconhecido", e o defeito fica invisível dentro
da história — que é exatamente como o `sla_target_ms` antigo mascarava o
`{t}:pool_config:{p}` expirado até a (iii) separar os dois com um contador.

Regra derivada: **quem agrupa por atribuição corta em `USAGE_ATTRIBUTION_EPOCH` e
CONTA o não-informado do período pós-época como número próprio**, nunca somando com
o pré-época. Encolher a série é o esperado, não sintoma.
"""
from __future__ import annotations

# Data em que o produtor passou a carimbar a chave de atribuição (T2).
# Linha com `timestamp` anterior a isto tem `segment_id`/`source`/conta vazios por
# AUSÊNCIA DE MECANISMO, não por defeito do chamador.
#
# ⚠️ **LIMITE MEDIDO — a granularidade é de DIA, e o corte é de INSTANTE.**
# A T1, a T2 e este corte entraram no MESMO dia (2026-08-28), então os eventos de
# antes da T2 *naquele dia* passam pelo predicado e caem no balde "pós-época e sem
# conta" — que a regra acima manda ler como DEFEITO. Medido em 2026-08-29, na F3: os
# oito eventos assim classificados são `t1-verify-B`/`t1-verify-C`, as sessões de
# verificação da própria T1, emitidas às 20:33 e 20:37; o primeiro evento COM conta é
# de 20:59.
#
# Consequência para quem lê: **o contador de "sem conta" é um TETO do defeito, não a
# medida dele**, enquanto a janela incluir o dia da época. Quem exibe esse número tem
# de dizer isso (`AccountTokensPanel` diz).
#
# Por que não foi convertido para `DateTime` aqui: o valor teria de ser um instante, e
# o único instante disponível seria escolhido OLHANDO os dados desta instalação —
# ajustar a constante para a amostra ficar limpa é a definição de fitting. A conversão
# é legítima quando houver o registro de deploy da T2; até lá, o limite é declarado.
USAGE_ATTRIBUTION_EPOCH = "2026-08-28"


#: Data em que o CONSUMO passou a ser publicado (T1) — antes disso `emit_llm_tokens`
#: só disparava de `POST /inference`, rota sem chamador algum.
#:
#: ⚠️ **É outro fato, e a igualdade de valor com a época acima é coincidência de
#: implantação, não identidade.** As duas respondem perguntas diferentes:
#:
#:   `USAGE_PRODUCER_EPOCH`    — antes disto não há LINHA NENHUMA (nada foi publicado).
#:   `USAGE_ATTRIBUTION_EPOCH` — há linha, mas sem a chave de quem consumiu.
#:
#: Foram entregues no mesmo dia, e é justamente por isso que precisam de nomes
#: separados: fundi-las agora tornaria impossível explicar a série no dia em que uma
#: delas mudar. A primeira é RÓTULO (a tela diz "a série começa aqui"); a segunda é
#: PREDICADO (quem agrupa por atribuição corta nela).
USAGE_PRODUCER_EPOCH = "2026-08-28"


def attribution_where(column: str = "timestamp") -> str:
    """
    Predicado SQL do corte. Recebe o nome da coluna de tempo porque os leitores
    diferem (`timestamp` no evento, `date` na partição).

    Devolver o predicado em UM lugar é o ponto: o `sla_source` nasceu porque três
    leitores tinham três respostas para "de onde vem o alvo", e duas delas mentiam.
    """
    return f"{column} >= toDateTime('{USAGE_ATTRIBUTION_EPOCH} 00:00:00')"
