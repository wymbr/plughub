---
name: skill-flow-authoring
description: Como escrever, validar, publicar e testar um Skill Flow do PlugHub (YAML em packages/skill-flow-engine/skills). Use ao criar ou editar skill_*.yaml ou agente_*.yaml, ao escrever steps (task, choice, catch, escalate, complete, invoke, reason, notify, menu, suspend, collect, resolve, receive, delegate, loop, begin_transaction, end_transaction), referências @ctx./$./@segment./@masked., context_tags e escrita no ContextStore, output_schema e prompt de step reason, ao decidir entre skill e DialogForm, ao validar com /v1/skills/validate, e ao publicar skill num pool (PUT /v1/skills, set-next, promote, deploy_skill_to_slot.sh).
---

# skill-flow-authoring — salvar não muda o que roda

Três fatos que mais custam a quem escreve um flow aqui:
1. **O que roda é o snapshot do slot `current` do POOL** — editar o YAML ou dar `PUT` na skill não
   muda execução nenhuma sem `set-next` → `promote`.
2. **Referência que não resolve não dá erro** — vira argumento ausente, `""` ou `[]`, conforme o lugar.
3. **O `prompt_id` do `reason` é só rótulo** — a instrução do autor vai no `input`.

Fatos com `arquivo:linha` em [`references/fatos-do-codigo.md`](references/fatos-do-codigo.md).

## 1. Antes de escrever: é skill mesmo?

- **Conteúdo scriptado e linear** (pesquisa, OTP, formulário) é **DialogForm** (JSON no
  `dialog-api`, `infra/dialog/*.json`); a skill só controla (`delegate` ao runner, ou consumo
  inline em hook). **Branching é da skill**, nunca do JSON.
- **Em que POOL vai rodar?** O perfil sai dos canais do pool: tem `webhook` → **`workflow`**; senão
  (inclusive sem canais) → **`agent`**.

| Perfil | Proibido | Por quê |
|---|---|---|
| `workflow` | `menu` · `notify` · `begin_transaction` · `end_transaction` | não há cliente conectado |
| `agent` | `suspend` · `collect` | há um cliente esperando |
| ambos | — `delegate` é permitido | é caminho vivo nos dois |

  ⚠️ O perfil **não** é conferido no save — só no `set-next` e no `promote` (`step_fora_do_perfil`).

- **Disparo endereça POOL, nunca `skill_id`.**

## 2. Arquivo e identidade

- `packages/skill-flow-engine/skills/<id>.yaml`; `id` casa `^skill_[a-z0-9_]+$`, **estável e sem
  versão** (versão é do DEPLOY; `_v\d+` antigo ainda vale). `version` é rótulo.
- Topo: `id` · `name` · `version` · `description` · `classification` · `entry` · `steps`
  (+ opcionais `required_context`, `mention_commands`, `config_params`, `delegation_input`).
- `entry` precisa existir e o flow precisa de ao menos um `complete` ou `escalate`.
- ⚠️ **Ciclos e alvos `on_*` inexistentes não são barrados no save** — rode
  `infra/test/probe_flow_transitions.sh`.

## 3. Referências

| Forma | Lê | Não resolveu |
|---|---|---|
| `$.pipeline_state.x` · `$.session…` · `$.tenant_id` | JSONPath no estado | `undefined` |
| `@ctx.<tag>` | ContextStore (`journey.*`/`core.journey.*` → hash da journey) | `undefined` |
| `@segment.<x>` | `segment.{segmentId}.<x>` | `undefined` |
| `@masked.<x>` | memória da transação mascarada | `""` |
| `{{ … }}` em texto | template | `""` |

- **Num `input`, referência PURA não resolvida é OMITIDA** (argumento ausente, nunca `null`) —
  campo obrigatório da tool recusa alto; opcional segue sem ele. Literal `null` no YAML viaja.
- **Não há log genérico de ausência.** Se a ausência muda o caminho, trate-a no flow (`choice`),
  não confie em que "vai dar erro".
- Use a **tag canônica**, nunca o alias legado (`gate_published_alias_census.sh` acusa).

## 4. Escrever no ContextStore

- Superfícies de escrita: `context_tags.outputs` · `delegate.context` · `collect.context`
  (prefixadas `session.`) · `mention_commands … set_context` · `context_json` ·
  `invoke.context_set`/`context_write`.
- **Toda tag escrita tem de estar CADASTRADA** (tela `/config/context-map`) antes do `PUT` —
  senão `unregistered_context_tag` / `archived_context_tag`. **Nome dinâmico é recusado.**
- **Root `core.*` é da plataforma**; skill escreve `session.*` (4 h), `journey.*` (30 d),
  `segment.*` ou outro root (vai para a sessão). ⚠️ `customer.*` **não** roteia para o hash do cliente.
- ⚠️ Com o config-api fora do ar, a checagem de cadastro **passa com warn** — não trate verde de
  save como prova de cadastro; rode `probe_contextstore_cadastro.sh`.

## 5. Steps que mais mordem

- **`reason`** — obrigatórios `prompt_id` · `output_schema` · `output_as` · `on_success` · `on_failure`.
  ⚠️ `prompt_id` **não é resolvido** por ninguém: o ai-gateway manda prompt de sistema genérico +
  schema + `input`. **Escreva a instrução no `input`** (padrão: `instrucoes: |`) e descreva os
  campos no `output_schema`. Sentimento só é medido se `customer_utterance` referenciar a fala
  (`$.`/`@ctx.`, nunca literal). `model_profile`: `fast|balanced|powerful|evaluation`.
- **`invoke`** — `tool` · `input` · `output_as` · `on_success`/`on_failure`; sem `target` usa
  `mcp-server-plughub`. ⚠️ O `invoke` de flow **não passa** pelo `judgeInvoke`, e `tools[]` do YAML
  **não é sincronizado** ao registry: não conte com permissão de tool como barreira aqui.
- **`menu`** — `timeout_s`: `0` não espera · `-1` espera indefinida · padrão `300`.
  `standby: true` só acorda por `@mention`. `collect` declara a coleta (`input` text/dtmf/voice, timeouts de
  canal, dígitos, `echo`, `max_invalid`) e o registry recusa dtmf/voz sem `first_input_timeout_s`;
  desfechos do canal saem por `on_timeout`/`on_invalid` (`on_invalid` exige `max_invalid`).
  ⚠️ **Dentro de `begin_transaction` isso NÃO vale hoje** (medido 2026-09-24, `NIV-19`): o
  motor manda toda saída de falha do bloco, timeout incluído, para o `on_failure` do
  `begin_transaction` (`engine.ts:627`), e o `on_timeout`/`on_invalid` do menu nunca roda. Até a
  NIV-19, trate o desfecho no destino do bloco.
  ⚠️ **Na voz, o canal fala a instrução do teclado sozinho**: *"Para X, tecle um…"* nas opções e
  *"Digite e termine com jogo da velha."* quando há `terminator` (`collect_core.py`). O prompt diz
  só O QUE pedir (*"Por favor, informe o seu PIN."*); repetir a instrução faz o cliente ouvir duas
  vezes (medido na VOZ-37).
  ⚠️ Numa chamada, a FALA do cliente só responde menu cujo `collect.input` tem `voice` — sem isso
  ela é só registro (VOZ-05 fatia 5b); menu que precisa ouvir o cliente declara a coleta por voz.
  `collect.voice.end_silence_ms`/`max_speech_s` valem por menu. `min_confidence` compara com confiança
  MEDIDA e **reprova fala certa** (medido: certas de 0,35 a 0,92) — não use para filtrar ruído, que o
  VAD do STT já descarta; declare só com medição no seu público (VOZ-18/19). Dado sensível: `masked: true` dentro de
  `begin_transaction`/`end_transaction`; `@masked.*` nunca vai a estado, stream ou log.
- **`delegate`** — `pool` não resolvido é falha dura (`on_timeout` com `pool_ref_unresolved`);
  `timeout_hours` não resolvido cai para 24 h com warn.
- **`collect`** — só `workflow`; o prazo conta do ENVIO. No survey outbound o veículo é link web,
  não `collect`.
- **`loop`** — o `body` volta ao id do loop e contém um passo que bloqueia; `over` que não é array
  vira `[]` e o loop termina sem aviso.
- **`catch`** — reexecuta o step que falhou; contador no `pipeline_state`.
- **`escalate`** — `target.pool`; referência não resolvida vai a `on_failure`, nunca a fallback.
- **`complete`** — ⚠️ `outcome_from` com valor inválido cai no `outcome` literal **sem log**.

## 6. Validar

`POST :3300/v1/skills/validate` responde **`200 {valid, errors}`** (nunca 422: o veredicto é o
corpo). ⚠️ **O corpo não é o YAML plano**: são os metadados no topo + `flow: {entry, steps}` —
mandar o YAML inteiro como `flow` dá erro de forma. O jeito seguro de montar é copiar o do
`deploy_skill_to_slot.sh` (bloco do passo 1), que também manda `x-tenant-id` e `x-service-token`.

Mesmo validador do `PUT` (`validateSkillPayload`): `invalid_shape` · `invalid_masked_block` ·
`invalid_masked_type` · `unregistered_context_tag` · `archived_context_tag` · `agent_role_removed`.
O que ele **não** vê: perfil (só no deploy), ciclos e alvos (`probe_flow_transitions.sh`),
`choice` que nunca casa (`probe_choice_operator_parity.sh`).

## 7. Publicar

```bash
wsl.exe -d ubuntu -- bash -lc 'cd /home/a1/projects/plughub && bash infra/scripts/deploy_skill_to_slot.sh packages/skill-flow-engine/skills/<id>.yaml <pool_id> <âncora>'
```

- Faz `PUT` → `set-next` → `promote` e **confere o snapshot promovido contra a âncora** (texto que
  só existe no flow novo). Sem âncora, um `PUT` que falhou promove o flow ANTIGO com "OK".
- Preserva o `config_json` do slot (`set-next` sem ele grava `{}`); `CONFIG_MERGE='{…}'` acrescenta.
- Skill é **seed-if-absent**: editar YAML já semeado e reiniciar é no-op. Vários editados:
  `infra/test/repromote_edited_skills.sh`. Lote atômico em vários pools: `promote-batch`.
- Sem slot `current` o pool **não roda** (`ALLOW_LIVE_FLOW_FALLBACK` é vazio no compose).

## 8. Testar

- Unitários do motor: `packages/skill-flow-engine/src/__tests__/` (um por step) e do validador:
  `packages/agent-registry/src/__tests__/skill-validator.test.ts`.
- Gates: `probe_flow_transitions.sh` · `probe_choice_operator_parity.sh` ·
  `probe_skill_profile_steps.sh` · `probe_contextstore_cadastro.sh` ·
  `gate_published_alias_census.sh`.
- E2E: `packages/e2e-tests/scenarios/` (13 workflow, 14 collect, 17 context store, 20 masked form).
- Método (veredicto, mutação, imagem × árvore): skill `testing-pattern`; deploy da imagem de
  motor/serviço: skill `deployment`.
