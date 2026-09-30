# Escalação por regra — a IA para na fronteira do passo (RUL-02)

> Entregue em 2026-09-30. Antes: RUL-01 (2026-09-29) fez o modo ativo recusar, porque o caminho
> que existia chamava uma rota inexistente e, mesmo consertada, mandaria o contato a um humano com
> a IA ainda rodando. O porquê de cada decisão está em `CHANGELOG.md` § 2026-09-30 (13).

## A pergunta que o desenho responde

*Quem para o agente em curso antes de rotear?* Não o rules-engine: ele decide que a regra
disparou, mas não ativou o agente e não sabe em que passo ele está. O dono da ativação é o
orchestrator-bridge, e quem sabe onde o fluxo está é o engine de skill-flow. A escalação em si
continua sendo o `escalate` de sempre, feito **pelo próprio fluxo**.

```
ai-gateway ──pub/sub session:updates:{sid}──▶ rules-engine
                                                  │ regra ATIVA disparou
                                                  ▼
                                   rules.escalation.events (chave session_id)
                                                  │
                                                  ▼
                               orchestrator-bridge · process_rule_escalation
                    conductor? (roster: primary, native, sem left_at) · humano? · já marcada?
                                                  │ SET NX session:{sid}:rule_escalation
                                                  │ LPUSH menu:signal:{sid}:{iid} {_rule_preempt}
                                                  │ wake_parked_run(sid, iid)
                                                  ▼
                        skill-flow-engine · topo do loop (antes de executar o passo)
                 marca é desta instância? SET NX …:taken · DEL fila de sinal
                 notification_send(customer_notice)  →  escalate sintético (reason: rule_escalation)
                                                  ▼
                 conversation_escalate: participant_left + conversations.inbound(target_pool)
```

## Decisões do dono (2026-09-30)

| # | decisão | mecanismo |
|---|---|---|
| 1 | parar na **fronteira do passo**, nunca no meio | verificação no topo do `while` do `engine.ts`; o passo em curso termina |
| 2 | aviso ao cliente **opcional, da regra** | `Rule.customer_notice` (≤ 500), enviado sem interpolação |
| 3 | só com **IA conduzindo e sem humano** | `rule_escalation.decide_conductor` — sem leitura positiva do roster, não age |
| 4 | **uma vez por sessão** | marca SET NX no bridge; disparos seguintes descartados e logados |

## Desfechos do bridge (todos no log)

| desfecho | quando |
|---|---|
| `marked` | marca gravada, IA sinalizada e acordada |
| `already_escalated` | a sessão já foi escalada por regra |
| `human_present` | há humano no `human_agents` ou no roster ativo |
| `no_roster` · `no_ai_conductor` · `ambiguous_conductor` | sem leitura positiva de quem conduz |
| `session_gone` | `session:{sid}:meta` não existe mais |
| `invalid` | evento ilegível, sem campo obrigatório, ou de shadow no tópico ativo |

## Limites conhecidos

- **`receive` não é acordado.** Um passo `receive` (BLPOP em `receive:result:…`) só devolve o
  controle quando chega mensagem ou vence; a preempção espera.
- **Corrida com a escalação do próprio fluxo.** Se o fluxo chega ao seu `escalate` antes da marca,
  ele decide; a marca sobra sem efeito (TTL 24 h) — ou o bridge já recusa com `no_ai_conductor`.
- **Regra não tem filtro por pool.** Vale para o tenant inteiro.
- Não há dry-run histórico (`RUL-05`, recusa 501).

## Qual sentimento a regra vê (RUL-04)

O **medido**: `core.sentiment.current` em `{t}:ctx:{sid}`, lido por `read_measured_sentiment`
(`session_reader.py`) — a casa onde a medição fora do turno e o valor declarado por um `reason` gravam.
O `sentiment_score` do pub/sub não é lido. A medição chega depois do turno, então o ai-gateway
republica o último turno (`last_rules_update` em `session:{sid}:ai`) com `trigger: sentiment_measured`
logo após gravar a medida. `window_turns` é recusado na escrita (422) e, em regra antiga, não casa:
não há série de sentimento medido por sessão.

## A tela e o que se muda (RUL-03)

`/config/rules` (platform-ui, `modules/rules/`), pela borda `/rules-api/*` → `rules-engine:3201`.
`PUT` e `DELETE /rules/{id}` só em `draft`/`disabled` — 409 nomeando o caminho senão: regra que mede
(`dry_run`/`shadow`) ou age (`active`) não muda por baixo; leve-a a `disabled` → `draft`, edite e
percorra o ciclo. As transições que a tela oferece vêm de `GET /lifecycle`, nunca de cópia.

## Quem pode mexer nas regras (AUT-65)

Toda rota menos `/health` exige credencial (`rules-engine/auth.py`): `config.rules` `read_only` lê,
`read_write` cria, ativa, faz dry-run e evaluate (preset só `admin`); tenant do token; serviço por
`X-Service-Token` (`PLUGHUB_RULES_SERVICE_TOKEN` — mcp-server e e2e-runner). Porta 3201 em loopback.

## O defeito que a entrega achou

`EvaluationContext.sentiment_score` exigia `float`; o ai-gateway publica `null` (não medido) desde
2026-08-23. Toda avaliação quebrava na validação — cinco semanas sem nenhuma regra avaliar, nem em
shadow. Hoje `null` = não medido: condição de sentimento não casa, as outras avaliam.

## Testes

- `skill-flow-engine/src/__tests__/rule-preemption.test.ts`
- `orchestrator-bridge/…/tests/test_rule_escalation.py`
- `rules-engine/…/tests/test_rules_engine.py` §§ 4 e 7
