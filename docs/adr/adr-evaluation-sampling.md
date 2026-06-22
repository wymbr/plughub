# ADR — Amostragem de Avaliação: Cota por Agente (virada para estado)

> Status: **Aceito · R10 + R12 implementados (modo `quota`)** · 2026-06-20 (design) / 2026-06-22 (R10/R12)
> Implementação: `evaluation-api/sampling.py::should_sample_quota` + wiring em `main.py`;
> backfill ordenado por `ended_at` + quota-aware em `backfill.py` (R12, contador compartilhado
> com o forward); config `%` por-agente (`quota_rate_human`/`quota_rate_ai`) backend pronto
> (R11 UI pendente). Ver `CHANGELOG.md` (2026-06-22).
> Relacionado: [`docs/arcos/arc-evaluation-metrics-methodology.md`](../arcos/arc-evaluation-metrics-methodology.md) (Parte IV),
> [`arc6-evaluation.md`](../arcos/arc6-evaluation.md) (`SamplingRules`),
> [`arc6-phase2-observability.md`](../arcos/arc6-phase2-observability.md) (âncora-pool/deploy).

## Contexto

A amostragem que decide **quais contatos viram avaliação** é hoje **stateless e
determinística**: `_sample_percentage(session_id, rate)` faz bucketing por SHA-256 do
`session_id`, com `%` **por campanha (global)**, mais filtros (`min_duration_s`,
`agent_type_ids`, `pool_ids`, `channels`, `outcome_filter`) e `priority`. É idempotente e o
backfill é determinístico por hash.

**Problema:** o `%` global **não garante cobertura por agente** — um agente de baixo volume pode
não ter nenhum contato amostrado. Para QA de equipe, o objetivo é **cobertura justa** (todo
agente auditado), não representatividade estatística da população de contatos. Vale para agentes
humanos **e** IA; muda o peso e o volume, não o mecanismo.

## Decisão

1. **Cota por agente, cumulativa, por déficit.** O primeiro contato elegível de cada agente é
   sempre amostrado (piso); a cada contato recomputa-se `avaliados/total`; se `< x%`, amostra o
   contato-gatilho. Converge para x% e garante o piso.

2. **Cumulativo (não diário).** Elimina o viés de "só o primeiro do dia" — só o primeiríssimo
   contato do agente é front-load; depois a seleção se espalha pelos horários.

3. **Chave do contador** (Redis, `INCR` atômico):
   - Humano: `(campaign, user_id)`
   - IA: `(campaign, pool_id, skill_id, deploy_version)` — **não** `agent_type` (eixo aposentado,
     Fase 3d). Chavear por versão **é** a semântica de "reset no deploy" sem reset destrutivo: a
     versão nova cai num bucket novo → 1º contato amostrado (= `deploy_baseline`) → converge
     dentro da versão.

4. **Pré-requisito — carimbo de versão.** Gravar `skill_id` + `deploy_version` no
   `ContactSegment`, **ancorado no início do segmento**, resolvido do `SkillDeployment` ativo
   para `(pool, skill)`. Propagar a `analytics.segments`, à evaluation instance e ao
   `evaluation_finalized`. (Conserta, de quebra, a precisão da lente de deploy do Arc 6 Fase 2.)

5. **Denominador = só contatos elegíveis** (após `min_duration`/`outcome`/`channel`); um contato
   filtrado não infla o `total`.

6. **Backfill ordenado por `closed_at`** para reprodutibilidade do déficit (a seleção é
   dependente de ordem). **Implementado (R12):** como `/reports/segments` não expõe
   `closed_at` por segmento, o backfill ordena por `ended_at` (fechamento do segmento;
   fallback `started_at`→`sequence_index`→`segment_id`) e usa o **mesmo** contador Redis do
   forward (cumulativo backfill+tempo-real, idempotente no re-run via `:seen:`).

7. **Semântica do `%`:** passa de "x% de todos os contatos" para "**x% por agente**"; humano e
   IA têm `%` próprios (IA tipicamente menor, opera 24×7). Tornar explícito na config.

## Consequências

**Positivas:**
- Cobertura justa por agente (objetivo de QA atendido).
- `deploy_baseline` de graça (versão nova = bucket novo, 1º contato amostrado).
- O carimbo de versão conserta a atribuição de qualidade por deploy (Arc 6 Fase 2).

**Negativas / trade-offs aceitos:**
- **Perde idempotência/determinismo** do hash; a seleção é **dependente de ordem**.
- **Concorrência:** contatos do mesmo agente fechando em paralelo exigem `INCR` atômico por
  chave.
- **Piso infla a taxa efetiva** acima de `x%` em baixo volume (decisão consciente:
  **cobertura > teto de `%`**).
- **Estado mutável** (contador Redis) — novo componente de estado em evaluation-api; inverte a
  decisão original de amostragem stateless/determinística.

## Notas de implementação / bordas

- **`deploy_version` não resolvível** (segmentos legados sem carimbo; contatos externos sem
  versão): fallback para o bucket `(campaign, pool_id, skill_id)` (sem versão) — nunca bloqueia a
  amostragem.
- **Hot-reload:** o flow é cacheado por `skill_id` estável e invalidado no deploy; uma sessão que
  retoma após o deploy pode pegar a versão nova → `start_time` é **aproximado**. O carimbo no
  **início** do segmento é a convenção robusta (atribui à versão ativa quando o segmento
  começou; um segmento que genuinamente atravessa duas versões é atribuído à de início).
- **Módulo agnóstico/externo:** como a versão precisa vir **dentro** do contato (não há timeline
  de deploy externa para cruzar), o carimbo é o mesmo mecanismo para nativo e externo.

## Alternativas consideradas

| Alternativa | Por que rejeitada |
|---|---|
| Manter hash stateless, estratificado por agente | Não garante cobertura (mesma fragilidade em baixo volume) e ainda exige `%` por agente |
| Reset **diário** do contador | Garante cobertura diária mas reintroduz o viés do primeiro-do-dia e infla mais em baixo volume — preterido por cumulativo |
| **Inferir** versão por `start_time` × timeline de deploy (sem carimbo) | Só funciona p/ nativo (externo não tem timeline); frágil no overlap de hot-deploy; depende de histórico de deploy retido |
| **Resetar** o contador no deploy (em vez de chavear por versão) | Operação destrutiva, briga com a atomicidade; substituída por chavear por `deploy_version` (bucket novo emerge sozinho) |
| Mudar `skill_id` por deploy (id version-bearing) | Quebra referências (`PoolSkillSlot`, `mention_commands`) e não resolve "um skill em vários pools"; preterido por `skill_id` estável + `deploy_version` |

## Roteiro

R9 (carimbo de versão), R10 (cota por déficit), R11 (config `%` por agente), R12 (backfill
ordenado) em [`arc-evaluation-metrics-methodology.md`](../arcos/arc-evaluation-metrics-methodology.md) § Parte V.
