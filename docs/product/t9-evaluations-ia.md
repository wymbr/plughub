# T9 — Arquitetura de Informação das Avaliações (drill-down 3 níveis)

> Blueprint consolidado (2026-06-19) para o T9 da spec de reconciliação Evaluation
> (`evaluation-reconciliation-spec.md` §7, §17). Define a IA-alvo da tela de Avaliações
> como **campanhas → avaliações → avaliação**, o modelo de escopo, o que cada nível mostra,
> e o recorte de implementação. Decisões fechadas em conversa; é a referência do T9.

---

## 1. IA — três níveis (drill-down)

```
Nível 1 — Campanhas        (resumo operacional por campanha; agregado)
   └─ Nível 2 — Avaliações  (fila/tabela das avaliações de uma campanha; linhas)
        └─ Nível 3 — Avaliação  (formulário preenchido + contestação/revisão; detalhe)
```

- **Nível 1 — Campanhas:** as campanhas em que o viewer tem acesso, com resumo operacional
  (agregado da campanha).
- **Nível 2 — Avaliações:** a fila das avaliações **daquela** campanha, com as colunas
  canônicas. É a fila de trabalho (acionável por `available_actions`).
- **Nível 3 — Avaliação:** a tela da avaliação em si — formulário preenchido com as notas,
  render tipado por critério, transcript com evidência, e ações de contestação/revisão.

A **estrutura dos 3 níveis é idêntica para todo perfil** (operador, supervisor, admin). O que
muda por perfil é apenas **quais dados são visíveis** (§2), nunca a navegação.

> **Telas existentes (evitar duplicação):** `/evaluation/campaigns` continua sendo a **gestão**
> da campanha (criar/editar form, sampling, período — não muda). `/evaluation/evaluations`
> passa a ser este **drill-down**; o nível 1 aqui é um **resumo enxuto** orientado a navegar/agir,
> que **não rebuilda** a CampaignsPage (onde fizer sentido, linka para a gestão).

---

## 2. Modelo de escopo — dois eixos

Separação limpa entre **agregado** e **linha individual**:

| Nível | Granularidade | Escopo (quem vê o quê) |
|---|---|---|
| **1 — Campanhas** | agregado (médias/totais) | **ABAC + `accessible_pools` (Arc 7)** gateiam QUAIS campanhas o viewer enxerga. O consolidado é **global da campanha** (não a fatia do viewer) — não é dado sensível, é a saúde da campanha. |
| **2 — Avaliações** | linha individual (por segmento) | **identidade + Grupo (Arc 9)** gateiam QUAIS avaliações o viewer vê: sem permissão de Grupo → **só a si mesmo** (seus segmentos, em todas as campanhas que participou); supervisor com Grupo → as pessoas do(s) seu(s) Grupo(s); admin → tudo. `accessible_pools` entra como filtro adicional de linha. |
| **3 — Avaliação** | detalhe | **mesma tela para todos**; ações e mascaramento por **ABAC** (§4). |

**Princípios herdados da spec (§17.2):** *ABAC nunca amplia visibilidade* — no nível 2 ela só
governa a ação nas linhas já visíveis. O único escopo **novo** é o **self do atendente** (filtro
por agente do segmento, via T2 / `agent_user_id`).

> **Ressalva (small-N):** agregados de nível 1 em campanhas de **N muito pequeno** podem vazar a
> nota alheia por dedução (média + a própria nota → a do outro). Mitigação futura: suprimir/
> arredondar quando `N < k` (ex.: 5). Não bloqueia o T9; fica registrado.

---

## 3. O que cada nível mostra

### Nível 1 — Campanha

**Métricas baratas (dado já disponível):**
- **período** (T17 — `period_start`/`period_end`), **pool avaliado**, **versão do form**;
- **total de avaliações** + **totais por status**: instance (`scheduled/in_progress/completed`)
  e `result_state` (`finalized/open/under_review/ai_review/error_rejected`);
- **tempo médio de avaliação** (de `process_duration_ms`, produced→finalized);
- distribuição de **`finalize_reason`** (uncontested/upheld/revised/timeout — qualidade do ciclo);
- split **humano vs IA avaliado**;
- **saúde de SLA** (quantas perto/acima do `deadline_at`).

**Métricas caras (FORA do T9 — item de Analytics):**
- **tokens / modelo / API IA / custo por avaliação** — o dado vive no **AI Gateway**
  (`usage.events`: `llm_tokens_input/output`, profile `evaluation`), não na evaluation-api.
  Exige pipeline de correlação `session → campanha → avaliação`. Tratado como item separado.

### Nível 2 — Avaliação (colunas)

`date-time` · **status** (`result_state` + round + `finalize_reason`) · **score** · **agente
avaliado (segmento) + role** (sessão como contexto) · **deadline correndo** (horário comercial,
quando há ação) · **elapsed** (start / tempo no estado atual) · badge **"aguardando minha ação"**
(de `available_actions`) · marca **contestado / revisada +Δ**.

### Nível 3 — Avaliação (detalhe)

- Formulário preenchido com **render tipado por critério** (score/boolean/choice/text;
  `auto_computed` cinza, não-contestável) + valor/justificativa/estado por critério;
- **timeline do critério** (avaliador → contestação → revisão, round a round);
- **transcript** em visão dividida com **evidência clicável** (chip → rola e destaca a mensagem
  via `stream_entry_id`);
- **mascaramento por papel** (revisor vê `original_content`; avaliado vê mascarado);
- **ações** manter/alterar/contestar, conforme §4.

---

## 4. Nível 3 — tela única, papéis por ABAC

A página de detalhe é **um único componente**, igual para quem contesta e quem revisa. O que
aparece é dirigido por **`available_actions`** (computado server-side a partir de `result_state`
+ ABAC + posse do segmento — nunca no cliente):

| Quem | Condição | Afordância |
|---|---|---|
| **Avaliado** | dono do segmento + campo `contestar` do round corrente + `open(round R)` | **contestar** |
| **Revisor** | ≠ avaliado + campo `revisar` do round corrente + scope no pool + `under_review(R)` | **revisar** (manter/alterar) |
| **Observador/supervisão** | sem campo de ação | **read-only** |

- **Mascaramento por papel** acompanha a ação: revisor vê `original_content`; avaliado vê
  mascarado.
- Gate "tratar todas" (§15.3): "Salvar revisão" bloqueado na UI **e** 409 `pending_contestations`
  no backend.
- O wiring de `available_actions` server-side é o **T10** — o T9 nível 3 consome o que o T10
  expõe (ou o que já existe), sem recomputar.

---

## 5. Recorte de implementação

| Chunk | Nível | Tamanho | Backend |
|---|---|---|---|
| **T9-A1** | 2 — colunas canônicas (`result_state`+round+reason, segmento, deadline, elapsed; mata o "Submitted") | pequeno | não (list_results já devolve os campos) |
| **T9-A2** | 1 — agrupamento por campanha + cabeçalho-resumo (métricas baratas) | médio | provável endpoint de sumário por campanha |
| **T9-B** | 3 — render tipado por critério + timeline | médio | não (busca o form p/ os tipos) |
| **T9-C** | 3 — transcript + evidência + mascaramento | grande | **sim** (endpoint de transcript + masking), **e2e-blocked** (stream expira ~1h) |
| *fora T9* | 1 — tokens/custo IA | — | pipeline AI Gateway ↔ campanha |

**Dependências/observações:**
- **Self-scope do atendente** (nível 2): filtro por `agent_user_id`/segmento nos results — provável
  ajuste de backend (avaliar no T9-A1/A2). Reusa Arc 7 (pools) + Arc 9 (Grupos).
- **Sumário por campanha** (nível 1): agregação por `result_state`/`finalize_reason`/`process_duration_ms`
  — provável endpoint novo na evaluation-api (ou ClickHouse/analytics).
- **Transcript** (nível 3): não existe endpoint escopado para avaliação hoje; reusar `ReplayContext`
  (session-replayer) ou novo endpoint + masking por papel. e2e-blocked.

**Ordem sugerida:** T9-A1 → T9-A2 → T9-B → T9-C. Um chunk por vez, validado no browser.

---

## 6. Referências
- `docs/product/evaluation-reconciliation-spec.md` §7 (UI), §17 (T9/T10/T11).
- Arc 7 (`accessible_pools`), Arc 9 (Grupos/supervisor scope), Arc 5 (segmentos), T1 (`result_state`),
  T2 (segmento+`agent_user_id`), T17 (período), T10 (`available_actions`).
