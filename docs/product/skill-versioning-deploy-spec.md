# Skill Versioning & Deploy — Design (editor livre × versão = deploy do pool)

> Estado: **design acordado, implementação pendente**. Última atualização: 2026-06-24.
> Origem: discussão R14 (affordances do editor + disciplina de versão) que revelou um
> modelo maior. Substitui a interpretação ingênua "versão = campo do skill".

## 1. Problema

O editor de skill-flow (`/agent-flow/editor`) hoje **sobrescreve `skill.flow` no `PUT`** e o
orchestrator-bridge **executa o `skill.flow` vivo** (hot-reload via `registry.changed`). Resultado:
**editar vaza para produção na hora** — não existe a fronteira `edição → deploy → produção`. Além
disso, "versão" está sobrecarregada num campo de texto livre (`skill.version`, regex `^\d+\.\d+$`)
que serve, ao mesmo tempo, de rótulo do operador **e** de chave de identidade do analytics — o que
torna deploys do mesmo rótulo indistinguíveis e força "nova versão = renomear o skill".

## 2. Modelo acordado

Separar **autoria** de **versionamento**. A versão é do **POOL** (o que está em produção), não um
campo do skill.

| Conceito | O que é | Onde vive |
|---|---|---|
| **Skill-flow** | conteúdo editável, **nome único** (`skill_id`, sem `_v`, sem `version` como identidade) | editor = biblioteca de rascunhos |
| **Rascunho** | working copy do editor — editar nunca toca produção | campo `flow_draft` |
| **Deploy** | bind de um snapshot de skill-flow → pool, **agendado**; promove no horário (calendário existente) | `PoolSkillSlot` + `SkillDeployment` |
| **Versão** | um **deploy = uma versão do POOL** (identidade = `deployed_at`; `flow_id` = qual skill rodou) | carimbo `segments.deploy_version` |
| **Produção** | o pool roda seu slot `corrente`; **só o deploy escreve** no campo que o bridge lê | bridge |

Princípio (modelo Git): edita-se a working copy à vontade; **versões são os deploys** (commits). O
`skill_id` é atributo da versão (qual conteúdo), não a identidade. Mesmo skill em dois pools = duas
linhas de versão independentes (casa com o N:1 e a "curva por pool" do epoch). Deploy de uma revisão
**ou** de um skill totalmente diferente = igualmente "nova versão do pool".

### Decisões fechadas

- **P1 (anti-vazamento) — DECISÃO REVISADA (2026-06-24):** o editor salva num **rascunho** (`flow_draft`)
  e o **bridge passa a executar o snapshot do slot `current` do POOL** (não o `skill.flow` vivo). A
  revisão veio da descoberta de que o deploy real é **por pool** (slots `next→current→previous`), o que
  torna o P2 (produção global em `skill.flow`) incoerente: promover num pool vazaria para todos os pools
  que compartilham o skill. P1 é fiel ao "versão = deploy do pool" e unifica os dois mecanismos.
  **Fallback seguro:** pool sem slot `current` roda o `skill.flow` publicado (retrocompat — nada quebra).
  (Preterido P2 = bridge ler `skill.flow` global; só funciona com uma produção por skill, não por pool.)
- **Versão ancorada no POOL.** Identidade = `deployed_at` do deploy ativo do pool. O `flow_id` do
  segmento registra qual skill. (Preterido: versão do skill.)
- **`skill_id` estável e único** (sem `_v\d+`); **`version` deixa de ser identidade** (vira rótulo
  livre opcional ou some).
- **`PoolSkillSlot` é a autoridade** do estado deployado + rollback; **`SkillDeployment`** = histórico
  (append-log de snapshots); **`SkillVersionSlot` (slots por-skill) é aposentado** (duplicação — o
  "3 lugares" do roteiro vira um só).
- **Hot-reload dispara no deploy/promote agendado** (calendário existente), não no save.

## 3. Levantamento — dependências do campo `skill.version`

Conclusão: **nada roteia/executa por `skill.version`** (o runtime resolve por `skill_id`). Amarras:

| Consumidor | Uso | Criticidade |
|---|---|---|
| `orchestrator-bridge/main.py:323` (`_skill_version_cache`) | carimba `segments.deploy_version` (R9) | troca de fonte → identidade do deploy |
| `evaluation-api/main.py:181` (`_fetch_skill_version`) | carimba `instances.deploy_version` (R9d) | troca de fonte |
| `analytics-api/consumer.py:150` + `deployments_client.fetch_skill_version` | fallback do carimbo | troca de fonte |
| `analytics-api/deployments_client.py:45` (`version_label`) | markers/epoch lêem `SkillDeployment.version` | passa a ser a identidade do deploy |
| `schemas/skill.ts:977` | `version` **required** (regex `^\d+\.\d+$`) | tornar **opcional** |
| `agent-registry/prisma` `SkillDeployment.version` | **NOT NULL** | gravar a identidade do deploy |
| `schemas/skill.ts:1055` `version_policy`/`exact_version` + `agent_type.skills[]` + SDK + UI | referência a skill por versão **declarada, NÃO resolvida** | **vestigial** — não bloqueia |
| `platform-ui SkillFlowsPage` `v{version}`, `registry_syncer` seed, SDK convert/import/certify | display/seed/geração | cosmético |

`form_version`, `rubric` version, locale version, `agent_type_id` version → **outras entidades**, fora de escopo.

## 4. Plano em fases (ordem de menor risco)

- **Fase A — `skill_id` estável + `version` opcional.** Relaxar regex `^skill_[a-z0-9_]+$`
  (`schemas/skill.ts`, `workflow-api/router.py`, `registry_syncer.py`); `version` → opcional no Zod;
  reescrever o `409` (id estável, não "_v2"). Retrocompat: ids `_v1` seguem válidos. *(= R14d)*
  **Verificação:** `PUT /v1/skills/skill_teste` (sem `_v`) → 201.
- **Fase B — anti-vazamento (P1) ✅ (2026-06-24).** Campo `flow_draft` no `Skill`; **editor** `PUT`
  escreve `flow_draft` (produção intacta); canal **sync/deploy** (`x-skill-publish:true`, RegistrySyncer)
  escreve produção. O **bridge** (`get_pool_current_flow`) executa o snapshot do slot `current` do pool,
  com **fallback** para `skill.flow` (pools não migrados). Slot `set-next` captura `flow_draft ?? flow`.
  Cache por pool invalidado no `registry.changed(pool)`. Editor mostra "rascunho não publicado". Verificado:
  editor cria skill com produção vazia (não vaza); skill IA (`skill_wrapup_v1`) executa normal (fallback).
  *(Plano original previa P2/`skill.flow` — revisado para P1 ao descobrir o deploy por-pool.)*
  Editor: salvar = rascunho; ação de deploy/publicar explícita. Bridge intacto (lê `flow`).
  **Verificação:** editar um skill publicado **não** muda o que roda até deployar.
- **Fase C — versão = deploy (C-full) ✅ (2026-06-24).** Identidade = **`set_at` do slot `current`**
  (momento do promote). O **promote grava um `SkillDeployment`** (`deployed_at=set_at`, `version`=rótulo,
  `pool_ids=[pool]`) — unifica slot + append-log. O **bridge** carimba `segments.deploy_version = set_at`
  (cache por pool; fallback `skill.version`); instâncias herdam (cobertura 1b consistente). **analytics**
  casa por `deployed_at` (Fase C) **e** por rótulo (legado), expõe `version_label`. **UI** mostra
  rótulo+data no eixo, timestamp no tooltip. Verificado: promote cria `SkillDeployment` (deployed_at =
  momento); testes 11/11. Transição: dados legados seguem por rótulo.
- **Fase D — affordances UI.** Botão "Novo skill" + Save habilitado para skill novo (flag `isNew`) +
  hint quando desabilitado. i18n. *(= R14a/b)*
- **Fase E — cleanup (opcional).** Aposentar `SkillVersionSlot`; remover/neutralizar `version_policy`
  vestigial; ajustar display do `version` (rótulo). Migração/limpeza de YAMLs.

Ordem sugerida: **A → D** (baixo risco, valor imediato) **→ B → C → E**. B e C são acoplados (depois
que o deploy é o único a escrever produção, "versão = deploy" é natural).

## 5. Reaproveitamento (o que já existe)

`PoolSkillSlot` (slots `anterior|corrente|próximo` por pool) · `SkillDeployment` (append-log + snapshot
+ `deployed_at` + `pool_ids`) · scheduled-deploy (`skill_scheduled_deploy_v1`) + calendário ·
`segments.flow_id` + `segments.deploy_version` (R9) · epoch ancorado no pool (R15) · cobertura (1b).
A espinha de versionamento **já está montada**; o trabalho é (i) cortar o vazamento e (ii) trocar a
fonte da identidade de versão.

## 6. Riscos / em aberto

- **Identidade do deploy por-pool × por-skill.** Um `SkillDeployment` mira `pool_ids` (N pools). A
  versão é do pool → a identidade carimbada deve ser resolvida **para o pool da sessão** (via
  `PoolSkillSlot.corrente`), não um valor global do skill. Definir no detalhe da Fase C.
- **Migração de dados.** Segmentos/deploys antigos têm `version` como string ("1.0"). Convivem com as
  novas identidades (timestamp) — transição, eixo do epoch mistura por um período.
- **`SkillVersionSlot` aposentado** exige conferir consumidores (UI de deploy/slots) antes de remover.
- **`version` no `BLANK_TEMPLATE`/seeds** some ou vira rótulo — alinhar com a Fase E.
