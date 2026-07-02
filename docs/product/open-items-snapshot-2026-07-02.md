# Open Items Snapshot — 2026-07-02

> **Isto é um retrato, não a fonte de verdade.** A fonte canônica de pendências continua sendo
> `TODO.md` (histórico completo, com contexto de cada decisão). Este documento é uma leitura
> **curada e priorizada** do `TODO.md` num dado momento — útil para retomar trabalho ou planejar
> a próxima sessão sem reler 1500 linhas de histórico. Gerado a partir de uma varredura completa do
> `TODO.md` + consolidação dos itens de survey (`F11` → `customer-surveys.md` / `customer-contact-history.md`).
>
> Se este arquivo e o `TODO.md` divergirem no futuro, **o `TODO.md` vence** — atualize ou apague este
> snapshot em vez de deixá-lo como uma segunda fonte.

---

## 1. Dívida técnica conhecida (infra/config)

- ~~`agent-registry` — `db push --accept-data-loss` no boot normal~~ → **✅ resolvido (2026-07-02)**,
  ver `CHANGELOG.md` § "agent-registry — bootstrap seguro...".
- **Config Consolidation** — arco híbrido de burn-down (`docs/arcos/config-consolidation.md`):
  - F2 item 6: seeds `evaluation`/`pricing` → bootstrap idempotente via API (estacionado).
  - F3: bootstrap idempotente único, substitui `infra/seed/*.py` + YAML-fonte (nota: distinto do fix
    de F1 já resolvido acima — F3 é a unificação arquitetural mais ampla, ainda não atacada).
  - F4: política de env vars — inventário final de segurança.
  - Migração seeds/YAML → migração versionada if-absent, store por store (baixa urgência,
    arquitetural, sem bug pendente hoje).
- ~~Access ↔ Groups — associar usuário a grupo pela tela do usuário~~ → **✅ já estava implementado**
  (confirmado 2026-07-02, item removido do TODO.md).
- **evaluation-api — 10 testes de `test_router.py` quebrados por drift de ambiente** (achado
  2026-07-02, ao validar o fix de self-view abaixo): Python 3.10→3.12 / `mock` lib mais nova quebra
  `dict(record)` sobre `AsyncMock`; endpoint `/review` com schema desalinhado; fixture sem
  `state.redis`. Não bloqueia nada, baixa prioridade.

## 2. Segurança

- ~~G-PROBE — perna humana `curar`~~ → **✅ resolvida (2026-07-02)** — código já existia (achado por
  recon), faltava seed de grant demo + smoke; ver `CHANGELOG.md`. Resta só a perna agente/sistema
  (pre-review/ai-review/seed-flush), **re-roteada para depender do Agent Principal** (§6) em vez de um
  token de serviço ad-hoc — bloqueada até essa spec ser implementada.
- **Hardening de sessão do Console** — refresh token hoje em `localStorage` + silent re-auth.
  Levers em aberto (decisão de produto pendente): cookie httpOnly, idle timeout, TTL menor,
  `sessionStorage` (fechar aba = deslogar).
- ~~Bug real — self-view de avaliação quebra por `pool_id` divergente~~ → **✅ resolvido (2026-07-02)**,
  ver `CHANGELOG.md` § "evaluation-api — bug self-view...".

## 3. Avaliação / Quality

- **P4 — eixo X por epoch/versão** no board de Agentes (lente `deploy`). Hoje é série diária +
  markers; ler "v1 vs v2" ainda é manual. **Diferido por decisão sua** ("deixar e reavaliar").
- **Nits do bench**: quality score geral diluído (denominador errado — deveria ser média só sobre
  avaliações, não sobre todas as sessões/dias); janela/período inconsistente entre KPI/lente/tabela;
  default de range pouco previsível.
- **S2.3/S2.4** — dispatcher automático por calendário (pode já estar coberto pelo destrave do
  combo de calendário em 2026-06-28, checar) e workflow de revisão (`S2.4` foi formalmente
  aposentado como decisão — Arc 13 REST é o contrato único).
- **G-UI** — só falta validação ao vivo pós-fix de 2026-07-01 (praticamente fechado).

## 4. Arquitetura / limpeza

- **Arc 19 residual** — remover topic `workflow.events` do Kafka e arquivar o package
  `skill-flow-worker`. ⚠️ **atenção**: esse pacote recebeu o wiring de `preferred_config_ids` (LLM
  Accounts, 2026-07-02) — confirmar se ainda está em uso em produção antes de arquivar, ou mover o
  wiring para o consumidor correto.
- **Agent-registry — unificar binding skill↔pool** (hoje espalhado em `PoolSkillSlot`,
  `SkillVersionSlot.pool_ids`, `SkillDeployment.pool_ids` — risco de divergência).
- **Scheduler central de timers** — consolidar timeouts espalhados (channel-gateway, bridge,
  collect) num módulo único de scheduling (ADR aceito, não implementado).
- **Skill hot-reload via YAML em disco sem restart** — endpoint `POST /admin/skills/sync` no bridge
  (dev/demo only; o fluxo editor→deploy já funciona sem isso).

## 5. Customer Surveys — módulo completo (consolida F11 + histórico de contatos)

Spec fechada, **nenhuma fase implementada**. Dois documentos relacionados:

### 5a. `docs/arcos/customer-surveys.md` — módulo de pesquisas de satisfação

5 instrumentos (CSAT/NPS/CES/PMF/FCR), separando instrumento × gatilho × veículo. Gatilho decidido
sempre no skill (a plataforma só carimba `contact_outcome` no ContextStore antes do hook). Política
de quarentena anti-fadiga. Interface web pública para pesquisa diferida. Form-builder com biblioteca
de perguntas reutilizáveis. Navegador de respostas com verbatim/áudio. Agente IA analista de
verbatims que classifica e endereça. Retorno outbound reusando a inbox pull já existente do Console.

**Fases (nenhuma iniciada):**

| Fase | Escopo |
|---|---|
| S1 | Normalização dos 5 instrumentos no consumer (`ces/pmf/fcr`) |
| S2 | Survey-runner genérico + `survey_definition` (PG) |
| S3 | Gatilho decidido no skill (outcome no ContextStore) |
| S4 | Quarentena (política + ledger + tool MCP) |
| S5 | Interface web pública + envio de link |
| S6 | Bancada: lente `customer_voice` |
| S7 | Form-builder (`/config/surveys`) |
| S8 | Navegador de respostas (`/analise/surveys`) |
| S9 | Agente IA analista de verbatims |
| S10 | Retorno outbound + caixa de ações (reusa inbox pull) |
| S11 | NPS/PMF relacional agendado + grão journey E2E |

### 5b. `docs/arcos/customer-contact-history.md` — histórico de contatos (transversal)

Sub-arco promovido do §20 da spec acima, mas **transversal** — serve qualquer atendimento, não só o
retorno de survey. **~60% já existe**: lista de contatos por cliente (`HistoricoTab`) e endpoint de
transcrição por sessão já funcionam, só não estão ligados.

**Fases:**

| Fase | Escopo |
|---|---|
| H1 | Drill lista → transcrição (wiring — endpoint já existe) |
| H2 | Busca no histórico (backend — full-text sobre `sessions_stream`) |
| H3 | Busca no histórico (UI — caixa + filtros na `HistoricoTab`) |
| H4 | Destaque do contato de origem no briefing de retorno (S10 acima) |
| H5 | Visão por cliente fora do atendimento (futuro, supervisão) |

**Se for atacar o módulo Surveys, H1–H3 é o quick win independente** — menor esforço, já é útil
sozinho em qualquer atendimento, e destrava o briefing do S10.

## 6. Propostas grandes, não iniciadas

- **Agent Principal** — identidade de máquina (`subject_type:"agent"`) para agentes nativos e
  externos se autenticarem via MCP, distinta das roles humanas. Spec fechada. Bloqueia a perna
  agente/sistema do G-PROBE (§2).
- **Business in Any Media** — reposicionamento process-centric + comércio conversacional: cadastro
  de identidade cross-canal, contrato delegate por pool, commerce-cards, fluxo de intake
  channel-abstract. 4 specs em `docs/product/`.
- **Record/Replay Harness** — generaliza o Session Replayer num harness "VCR" determinístico em
  todas as costuras (channel-gateway, AI Gateway, MCP, Kafka) para regressão e gate de promoção.

## 7. Deferred — aguardando gatilho concreto (sem ação agora)

- **Usage Metering** — `whatsapp_conversations`/`voice_minutes`/`sms_segments`/`email_messages`:
  funções prontas em `usage_emitter.py`, adapters de canal não chamam ainda.
- **Pricing × Metering** — módulo que lê `usage.events`, aplica planos do Config API e escreve
  `{tenant}:quota:limit:*` no Redis.
- **Audit LGPD Fases 2–5** — `original_content` desmascarado (endpoint batch no Core),
  `user_access` logs, SAR/erasure pipeline, `config_snapshot` read-only.
- **Masking Bloco 3 (TTS)** — só quando houver adapter de voz/TTS real.

---

## Como usar este snapshot

- Cada seção tem esforço/urgência bem diferentes — não é uma fila única. Se quiser, posso priorizar
  por "quick win vs. arco grande" antes da próxima sessão.
- Itens da seção 5 (Customer Surveys) são os únicos com **plano de fases já pronto** — são o caminho
  mais direto para começar a implementar algo hoje, se esse for o próximo foco.
- Para retomar contexto completo de qualquer item, o `TODO.md` tem o histórico de investigação por
  trás de cada decisão (datas, descobertas, código exato).
