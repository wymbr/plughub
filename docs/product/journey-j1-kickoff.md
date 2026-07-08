# J1 — Kickoff de implementação (Journey · espinha de proveniência)

> **Para a sessão nova (abrir com Opus).** Ler apenas: `CLAUDE.md`, este arquivo, e
> `docs/product/journey-3-niveis-implementation-spec.md` (§2, §3, §7-J1, §8). **Não** carregar a árvore `docs/`.
> Contexto de design completo: `journey-retorno-modelo-3-niveis-design.md` (só se precisar do porquê).
> **Ambiente:** o shell isolado não alcança `\\wsl.localhost\...` — edições são feitas pelas ferramentas de
> arquivo; **build e testes rodam no WSL** (pelo usuário). Trabalhar incrementalmente, um pacote por vez.

## Objetivo do J1 (e só ele)

A **espinha de proveniência**: `root_session_id` em toda sessão (imutável, **nunca null**) + propagação
automática top-down + `journey_id` como cache = `root` no nascimento. **Cobre os cenários 1 e 2-com-journey.**
**Fora do J1:** merge/alias (`journey_merge`, `journey.merges`, `journey_aliases`, union-find) = J3;
`/reports/journeys` + Vista Processos = J2; avaliação N3 = J4. **Não implementar nada de merge no J1.**

## Invariantes (não violar)

- `root_session_id` **imutável, nunca null**: valor propagado do chamador, senão auto-mint = `self` (`session_id`).
- Propagação é **de plataforma** (injetada como o `origin_session_id` já é hoje), **não** campo de fluxo no YAML.
- `sessions.journey_id` é **cache** (= `root_session_id` no open); **nunca** fonte de verdade. Ninguém lê dele
  como verdade no J1.
- Manter `origin_session_id` (1 salto) **e** `root_session_id` (raiz transitiva) — não substituir um pelo outro.
- Migração ClickHouse **aditiva**: `ADD COLUMN IF NOT EXISTS`; **não** mexer no `ORDER BY` de `sessions`.

## Arquivos a tocar (ordem sugerida)

1. **`packages/schemas/src/stream.ts`** — declara `origin_session_id` e o `session_opened`/`ConversationInbound`.
   Espelhar `root_session_id: z.string()` (**não-nullable**) ao lado de `origin_session_id`. Named exports
   explícitos (nunca `export *`). *(Conferir também `workflow.ts`/`survey.ts` — só se referenciarem o schema de
   sessão; provavelmente não precisam mudar no J1.)*
2. **`packages/channel-gateway/.../adapters/webhook.py`** — `handle_trigger` e `handle_delegate` já setam
   `origin_session_id` (star topology). Adicionar `root_session_id`: no inbound sem param → `self`; em
   delegação/collect → herdar o `root_session_id` do chamador (mesma mecânica do `origin_session_id`). Escrever
   no `context` do inbound e no ContextStore (`session.root_session_id`, source `bridge`/`core`, conf 1.0,
   visibility `agents_only`).
3. **`packages/orchestrator-bridge/.../main.py`** — no `session_opened`/criação de sessão nativa, resolver e
   carimbar `root_session_id` (param → self). Em `_close_contact_layer` (onde já resolve `customer_id` no Slice 4)
   garantir `root_session_id` na linha de fechamento.
4. **`packages/skill-flow-engine/src/engine.ts`** (+ `executeDelegate`/`executeCollect`/`task`) — propagar
   automaticamente o `root_session_id` do chamador ao filho, junto do que já é propagado (`origin_session_id`).
   **Não** adicionar campo obrigatório ao `skill.ts`.
5. **`packages/analytics-api/src/plughub_analytics_api/clickhouse.py`** — migração
   `ALTER TABLE sessions ADD COLUMN IF NOT EXISTS root_session_id String DEFAULT session_id` (espelhar
   `_DDL_SESSIONS_MIGRATE_ORIGIN`, Arc 19); registrar na lista de migrações. `models.py::parse_inbound`
   (hoje popula `origin_session_id`) → popular `root_session_id` e `journey_id` (= `root`) no open.

## Build / rebuild (WSL)

Mudança em `@plughub/schemas` ⇒ rebuildar os consumidores TS **juntos** (invariante do `CLAUDE.md`): `schemas`
→ `sdk`, `mcp-server-plughub`, `skill-flow-engine`, `agent-registry`. Serviços Python (channel-gateway,
orchestrator-bridge, analytics-api) leem o evento como JSON — sem dependência de compile —, mas conferir se há
mirror Pydantic do schema de sessão que precise do campo.

## Verificação (rodar no WSL)

- **Unit:** parser `session_opened`/`inbound` com e sem `root_session_id` (default = `self`); propagação
  herda a raiz do chamador em delegate/collect.
- **e2e (harness):** **cenário 1** (workflow N3 dispara 2 filhos → todos com `root = N3`); **cenário 2-com-journey**
  (segmento com journey dispara workflow → herda a mesma raiz). Assert: `analytics.sessions.root_session_id`
  populado e igual à raiz esperada; `journey_id` = `root` no open.
- **Guard:** nada lê `sessions.journey_id` como fonte de verdade; nada reescreve `root_session_id`.

## Doc ao concluir

Mover J1 do `TODO.md` (§ Journey retorno) para `CHANGELOG.md`; atualizar `docs/modelos-de-dados.md`
(`root_session_id`) e a spec (§7 marcar J1 ✅). Seguir as regras de manutenção de doc do `CLAUDE.md`.
