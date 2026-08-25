"""
sla_source.py — D14 (iii): de ONDE vem o alvo de SLA, e desde QUANDO.

Definição única para os três leitores de SLA do repositório:

    query.py           `get_pool_sla_1h`   (dashboard, última hora)
    reports_query.py   `_cv_sla_series`    (overlay diário da Voz do Cliente)
    reports_query.py   `query_pools_queue` (relatório Fila/SLA)

────────────────────────────────────────────────────────────────────────────
POR QUE ESTE MÓDULO EXISTE
────────────────────────────────────────────────────────────────────────────
A D14 decidiu que **SLA é fato do SEGMENTO DE ESPERA, nunca da sessão**: uma
sessão carrega UM alvo, então o contato que espera em duas filas perde a
violação da segunda, e a média mistura populações não comparáveis.

A (ii) (2026-08-24) entregou a ESCRITA — `analytics.segments.sla_target_ms`,
copiada no fechamento da espera a partir do `{t}:pool_config:{p}`. A (iii)
(este módulo) migra a LEITURA. A partir daqui:

    `sessions.sla_target_ms` é **PROJEÇÃO, nunca fonte de cálculo.**

Enquanto essa regra viveu só em prosa (CLAUDE.md, CHANGELOG,
`conference-mechanics.md` § 41), ela era da família *"invariante sem mecanismo
que a imponha"* — a mesma do DDL de `participation_intervals`, que **afirmava**
a ordenação que ninguém impunha. O mecanismo agora é o gate
`test_sla_reads_the_segment.py`, que reprova se um leitor voltar à sessão.

────────────────────────────────────────────────────────────────────────────
A ÉPOCA — e por que ela não é decoração
────────────────────────────────────────────────────────────────────────────
A (ii) é **forward-only**: linha antiga fica `NULL` e **não há migração
possível**, porque o `first_queued_ms` que daria o alvo é consumido na saída da
fila. Trocar a fonte encolhe o denominador. Medido no tenant demo antes de
codar (`infra/test/q_sla_source_delta.py`, 2026-08-25):

    72 esperas · 51 elegíveis pela SESSÃO · **1** elegível pelo SEGMENTO
    aderência global 70,6% → 100,0% (n=1 — ruído, não melhora)
    `retencao_humano` 34 elegíveis a 64,7% → 1 elegível

**Decisão do dono (2026-08-25): saída (b) — cortar a série numa data
declarada.** A alternativa (a) era ler o segmento com fallback à sessão durante
a transição: preserva a série, mas mistura duas fontes num mesmo número e
ninguém saberia qual respondeu em cada linha.

O corte NÃO é o que exclui a linha antiga — o `sla_target_ms > 0` já a excluiria
sozinho. A época existe para **separar duas ausências que têm a mesma cara**:

    espera ANTES da época, sem alvo   →  "não medíamos" — ausência esperada
    espera DEPOIS da época, sem alvo  →  **DEFEITO**, e tem nome: o
                                         `{t}:pool_config:{p}` expirou antes do
                                         fechamento da espera (dois escritores
                                         da mesma chave com TTLs diferentes,
                                         86 400 × 3 600, e o último vence)

Sem a época, a segunda se esconde dentro da primeira e o buraco no ledger fica
invisível para sempre. É por isso que `query_pools_queue` publica
`sla_unstamped` — contador da segunda população, ao lado do contador de
presença. Degradação nunca é silenciosa.

⚠️ **O corte é sobre `segments.started_at`, não sobre o instante do carimbo.**
A espera é carimbada quando FECHA, mas indexada por quando COMEÇOU. Logo uma
espera que começou 30 s antes da época e fechou depois dela sai do denominador
apesar de ter alvo. É a borda conservadora (perde-se uma linha, não se inventa
nenhuma), afeta no máximo os contatos em curso no instante do deploy, e é
coerente com o resto: os três leitores já janelam por `started_at`/`opened_at`.
"""
from __future__ import annotations

# Instante da PRIMEIRA espera carimbada pelo produtor da (ii), medido em
# 2026-08-25 por `infra/test/q_sla_source_delta.py` (E2E de tráfego real:
# `retencao_humano`, espera de 10 065 ms, `sla_target_ms=300 000`; as 71
# esperas anteriores do tenant estão todas em `\\N`).
#
# ⚠️ NÃO é "a data em que alguém rodou o deploy" — é o primeiro fato observado.
# Um valor anterior a este colocaria esperas sem alvo do lado "defeito" do
# corte e produziria um `sla_unstamped` fabricado; um valor posterior jogaria
# fora medição boa. Se a série for reiniciada noutro ambiente, este valor é
# medido lá, nunca copiado daqui.
SEGMENT_SLA_EPOCH = "2026-08-25 00:52:29"


def segment_sla_epoch_clause(column: str = "started_at") -> str:
    """Cláusula SQL do corte (b). Recebe a coluna porque cada leitor janela sobre
    um alias diferente (`w.started_at`, `started_at`), e concatenar o nome à mão
    em três lugares é como se ganha a quarta cópia que diverge."""
    return f"{column} >= '{SEGMENT_SLA_EPOCH}'"
