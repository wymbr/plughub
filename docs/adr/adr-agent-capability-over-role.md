# ADR — Capacidade do agente no lugar de `agent_role`

**Status:** proposto · **Data:** 2026-09-01 · **Grupo no ledger:** `CAP`

---

## Contexto

A plataforma tem **três** vocabulários para responder *"quem pode o quê"*, e eles não se
tocam em lugar nenhum do código:

| eixo | domínio | escopo do fato | onde decide |
|---|---|---|---|
| **capacidade do usuário** (ABAC) | módulo × campo, `none…read_write` | pessoa | 8 serviços, verificador canônico `plughub_authz` |
| **papel de participação** | `primary` `specialist` `supervisor` `evaluator` `reviewer` | (participante, sessão) | roster, mascaramento, gate de @mention |
| **propósito do agente** (`agent_role`) | `executor` `orchestrator` `evaluator` | artefato (skill) | **duas** tools de avaliação |

O terceiro é o assunto deste ADR.

### O que foi medido antes de propor (2026-09-01)

**`agent_role` está VIVO e é load-bearing** — isto não está em dúvida e o ADR não o
contesta:

- 44 skills no registry, zero nulos: **41 `executor` + 3 `evaluator`**;
- produtor real: `agent_login` carimba no hash da instância, lendo do **registry**,
  nunca do input do agente (`tools/runtime.ts:211`);
- consumidores: `evaluation_context_get` e `evaluation_submit` recusam quem não é
  `evaluator`;
- portão dedicado (`infra/test/smoke_agent_role_gate.sh`) passa **incluindo o ramo
  negativo** (T4: com o skill em `executor`, o contexto não é entregue);
- o que protege é concreto: o `ReplayContext` carrega `original_content`
  **desmascarado**, e este gate já falhou **aberto** uma vez — lia um campo sem
  produtor, com `if (role && …)` curto-circuitando na string vazia, e qualquer agente
  com `session_token` válido lia PII.

**E é o ÚNICO controle de identidade do chamador nessas tools**: `verifySessionToken`
confere só a assinatura, a tool usa apenas o `tenant_id`, e ela não pertence a nenhum
grupo de permissão.

### O problema, então, não é o gate — é o vocabulário

Manter um enum de papel no artefato para responder *"quem pode"* cria a terceira
gramática de autorização da casa. O custo não é hipotético: **medindo esta mesma
semana, o eixo errado foi medido duas vezes** — inclusive por quem escreve este ADR, que
foi verificar o eixo de participação achando que respondia ao terceiro. Três
vocabulários é a condição que faz censo enganar e comentário mentir.

Agrava que o nome colide: existe uma variável local `agentRole` em `server.ts:3091`
que guarda `primary`/`specialist` — o **segundo** eixo com o nome do terceiro.

---

## Decisão

**D1 — O gate do avaliador troca de EIXO, não de existência.** `agent_role: evaluator`
deixa de ser o discriminador e passa a ser um grant de capacidade
(`evaluation.avaliar`, nome a fixar na F2), verificado pelo mesmo caminho que o resto da
plataforma. Mesma proteção, um vocabulário a menos.

**D2 — Remover o gate não é opção.** O que ele protege é PII desmascarada, e o estado
"sem gate" já existiu e tem consequência registrada. Toda fase abaixo preserva a
proteção em todo instante.

**D3 — O valor `orchestrator` sai do domínio junto.** Medido: **zero portadores**, e
nenhum gate o consome (ambos testam `!== "evaluator"`), então ele se comporta como
`executor`. É valor declarado sem função — a família do `unrestricted`.

**D4 — O `role` propagado no agent_type sintetizado do bridge sai junto**
(`orchestrator-bridge/main.py:652`). O comentário local afirma que ninguém o lê;
**verificado, e confere**: zero leitores.

---

## O pré-requisito que a medição revelou, e que muda a ordem

O caminho natural — *"o não-avaliador nem veria a tool"* — **não funciona hoje**:

```
registry-client.ts:72   permissions: []          ← fixo no código
registry-client.ts:46   "empty permissions ⇒ no MCP tool filtering, the deploy-driven norm"
inference.py:128        if req.permissions:      ← vazio = SEM filtro
```

**73 tools expostas, 0 permissões declaradas em qualquer lugar.** O filtro de
`permissions[]` que o `CLAUDE.md` descreve como invariante existe, está correto, e é
**inerte por norma declarada**.

⚠️ **É a mesma forma da AUT-03**: um default vazio que significa *"tudo"* e precisa ser
POPULADO antes de poder ser invertido para *"nada"*. Ligar o filtro sem declarar
permissões daria **zero tools a todo agente** — a falha catastrófica, não a segura.

E daí a consequência de ordem: **trocar o gate de eixo (F2) antes de ligar o filtro (F1)
troca um gate que funciona por um que não roda.** Essa é a razão de F1 vir primeiro, e
não é preferência.

---

## Fases

| fase | o que faz | como se sabe que terminou |
|---|---|---|
| **F1** | Declarar `permissions[]` por skill e MEDIR o delta antes de inverter o default: quantas tools cada agente perde, quais agentes ficam sem tool essencial | censo publicado: por skill, tools usadas × tools declaradas. **Reprova se algum agente perder tool que ele comprovadamente chama** |
| **F2** | Criar o grant de capacidade e fazer os dois gates o consultarem — **mantendo `agent_role` em paralelo**, ambos exigidos (E lógico) | o `smoke_agent_role_gate.sh` continua verde, e um smoke novo prova que o grant sozinho **não** libera enquanto o par estiver ativo |
| **F3** | Retirar `agent_role` do gate, do schema, do registry e do bridge; `orchestrator` sai junto | o smoke inverte-se em testemunha (o campo não volta), como `probe_unrestricted_claim.sh` |

**F1 antes de F2, F2 antes de F3.** A ordem não é estética: em nenhum instante das três
existe uma janela em que o `ReplayContext` fique sem gate.

⚠️ **A F2 mantém os DOIS gates de propósito.** Trocar de uma vez cria a janela em que o
novo está errado e o velho já saiu — e o modo de falha desse gate é servir PII em
silêncio, que ninguém nota.

---

## O que este ADR NÃO decide

- **o nome do grant** (`evaluation.avaliar` é sugestão) — F2;
- **de onde vem o grant do AGENTE**: o ABAC atual é de PESSOA (`module_config` no JWT do
  usuário), e o agente autentica por `session_token`, que não carrega `module_config`.
  **Isto é trabalho de desenho da F2, não detalhe**: pode ser que o eixo certo para
  agente seja `permissions[]` (o mecanismo da F1) e não o ABAC de pessoa — e nesse caso
  a F2 é só *declarar `evaluation_context_get` nas permissões dos skills avaliadores*, e
  o "terceiro vocabulário" vira o primeiro sem inventar um quarto;
- **se `permissions[]` deve ser por skill ou por pool** — a F1 mede e o dono decide.

---

## Riscos

1. **F1 é a fase cara e a única que toca todos os agentes.** Se o censo mostrar que
   declarar permissões é inviável no parque atual, D1 fica bloqueada e `agent_role`
   **permanece** — resultado legítimo, e melhor que um gate que não roda.
2. **A F3 remove um campo de ENTRADA da API do registry.** A lápide do `unrestricted`
   (2026-08-31) mediu o custo disso: pydantic/Zod ignoram chave desconhecida, e quem
   continuar mandando recebe **200 sobre um no-op**. A F3 recusa nomeando, como lá.
3. O ganho é de **legibilidade e de um vocabulário a menos**, não de segurança — a
   proteção é a mesma em todas as fases. Se o custo da F1 for alto, o ganho não paga.

---

## Referências

- `TODO.md` § *Gate de @mention* — a medição que revelou o filtro inerte (e o desvio de
  eixo que a originou)
- `CHANGELOG.md` 2026-08-31 § *A lápide do `unrestricted`* — o custo de remover campo de
  entrada
- `infra/test/smoke_agent_role_gate.sh` — o que precisa continuar verde nas três fases
