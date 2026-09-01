# ADR — Remoção do terceiro eixo de papel (`agent_role`)

**Status:** proposto · **Data:** 2026-09-01 · **Grupo no ledger:** `CAP`
**Substitui:** `adr-agent-capability-over-role.md` (mesmo dia, **refutado por medição** — ver § Alternativa refutada)

---

## A pergunta que originou este ADR

Do dono, e ela é mais estreita do que as duas respostas que recebeu antes:

> *"Por que existe um gate para evaluator? Não vejo motivo nem cenário de uso para
> bloquear alguém que não seja evaluator. A sugestão era avaliar o que acontece se
> eliminarmos o gate."*

A resposta anterior foi *"o gate é load-bearing, protege `original_content` desmascarado,
não remova — troque o eixo"*. **Essa resposta estava errada**, e a medição que a derruba
está abaixo.

---

## O que foi medido

### 1. O gate não autentica o chamador — ele valida uma string do input

```ts
// tools/evaluation.ts — evaluation_context_get e evaluation_submit
const { tenant_id } = verifySessionToken(session_token)          // instance_id DESCARTADO
const identity = await readAgentIdentity(tenant_id, participant_id)   // ← veio do INPUT
```

`SessionTokenPayload` carrega **`instance_id`** — a identidade real e **assinada** do
chamador. As duas tools desestruturam apenas `tenant_id` e a descartam. O papel é então
consultado para o `participant_id` que **o chamador digitou**.

Ou seja, o gate responde *"o id que você me deu é de um avaliador?"*, não *"você é um
avaliador?"*. Quem passar o `participant_id` de qualquer instância avaliadora
(`evinstance_<hex>`) atravessa.

⚠️ A ironia é do próprio arquivo: o comentário que explica de onde vem o `agent_role` diz
*"nunca do input do agente — **auto-declaração é asserção, não autorização**"*. É
exatamente o que o gate faz com o `participant_id`.

### 2. O cenário que justificava o gate não fecha

O argumento era *"devolve PII desmascarada"*. Para receber PII, o chamador precisa nomear
um `session_id` que esteja **simultaneamente**:

- **fechado** — o `ReplayContext` é escrito pelo Replayer no `evaluation.requested`, que
  vem depois do `session_closed`;
- **amostrado** para avaliação;
- **dentro do TTL de 1 h**.

Um agente executor em sessão viva **não tem** `ReplayContext` da própria sessão, e não há
caminho pelo qual ele obtenha o id de outra. Nenhuma tool devolve lista de `session_id`
para agente.

### 3. Quem o gate realmente separa

| população | hoje | sem o gate |
|---|---|---|
| avaliador legítimo | passa | passa — **nada muda** |
| uso deliberado / injeção | **passa** (nomeia um avaliador) | passa — **nada muda** |
| avaliador **mal configurado** (skill em `executor`) | bloqueado + log | recebe o contexto |

Sobra **uma** linha, e ela não é fronteira de segurança: é **detecção de erro de
deploy**. É literalmente o que o T4 do `smoke_agent_role_gate.sh` exercita — vira o skill
para `executor` e confere a negação. Erro de configuração se pega na validação do
registry ou no portão, não em runtime servindo produção.

### 4. E os outros dois valores do domínio não têm consumidor

- **`orchestrator`**: zero portadores em 44 skills, e nenhum gate o consome (ambos testam
  `!== "evaluator"`), então comporta-se como `executor`;
- **`role` no agent_type sintetizado do bridge** (`main.py:652`): o comentário afirma que
  ninguém lê — **verificado, confere**, zero leitores.

---

## Decisão

**D1 — O gate sai.** Não porque o eixo incomoda, mas porque **não impede nenhum cenário
que alguém consiga descrever**: passa quem quer entrar, barra quem se configurou errado.

**D2 — `agent_role` sai junto**, perdendo o único consumidor: campo, valores do domínio
(`orchestrator` incluído), coluna do registry, sync e o `role` sintetizado do bridge.

**D3 — `readAgentIdentity` FICA.** Sai o `if`, não a leitura: o `evaluation_submit` usa
`identity.agentTypeId` para **procedência** do resultado (o `evaluator_unknown` que o
Arc 13 corta). Isto é a única pendência real da remoção — ver § Pendência.

**D4 — Consertar em vez de remover foi CONSIDERADO e recusado.** O conserto é uma linha
(usar o `instance_id` do token em vez do `participant_id`), e tornaria o gate autêntico.
Mas **consertar exige um cenário que o justifique**, e é exatamente o que não existe.
Consertar sem cenário é criar manutenção perpétua para uma porta que não dá para lugar
nenhum.

---

## Pendência única e real: a procedência do `evaluation_submit`

Hoje o `agent_type_id` gravado no resultado vem do MESMO hash de instância que o gate
lia. Removido o gate, a leitura fica — mas a fonte segue sendo um hash indexado por um
`participant_id` **vindo do input**, então a procedência é tão auto-declarada quanto a
autorização era.

Isso **não bloqueia** a remoção (o estado não piora), mas fica registrado: se a
procedência do avaliador importa para o Arc 13, ela precisa vir do `instance_id` do
token, não do input. É a mesma correção de uma linha da D4 — só que ali é para
**autorizar** (recusado) e aqui para **atribuir** (a decidir).

---

## Alternativa refutada — *"trocar o eixo em vez de remover"*

Este ADR substitui `adr-agent-capability-over-role.md`, escrito **no mesmo dia**, que
propunha manter a proteção e migrar o gate para um grant de capacidade
(`evaluation.avaliar`), em três fases com `permissions[]` como pré-requisito.

**Por que caiu:** a proposta inteira assentava em *"o gate protege PII desmascarada e é o
único controle"*. A primeira metade é falsa (§2 acima: o cenário não fecha) e a segunda é
verdadeira mas irrelevante — **ser o único controle não vale nada se o controle valida a
string do input** (§1).

**O que sobreviveu dela**, e não deve ser perdido:

- **`permissions[]` está INERTE por norma declarada.** `registry-client.ts:72` fixa
  `permissions: []`, o comentário ao lado chama isso de *"the deploy-driven norm"*, e
  `inference.py:128` trata vazio como **sem filtro**: **73 tools expostas, 0 permissões
  declaradas**. O CAMPO existe em toda a mensagem (`SessionTokenPayload.permissions`,
  `AgentTypeSchema.permissions` com regex de formato, `InferenceRequest.permissions`,
  `AuditRecord.permissions_checked`) — **o que não existe é PRODUTOR**, porque a entidade
  que o declarava (`AgentType`) foi aposentada e a declaração não reapareceu: `pools` não
  tem coluna, `skills.tools` existe e está **0 de 44**, e `pool_skill_slots.config_json`
  só tem `form_id` e `max_concurrent_sessions`.
- Isso é **fato independente deste ADR** e sobrevive à remoção do `agent_role`: um agente
  qualquer enxerga as 73 tools. Se alguém quiser reduzir superfície um dia, é ali — e é a
  mesma forma da AUT-03 (default vazio que significa "tudo" e precisa ser POPULADO antes
  de virar "nada"). Registrado como `CAP-05`, sem dono e sem gatilho declarado.

**Por que a alternativa fica escrita em vez de apagada:** sem ela, "trocar o eixo" é
reproposto em três meses e o caminho inteiro é refeito. É a mesma postura de inverter
testes em vez de apagá-los.

---

## Achado estrutural compartilhado — gate de papel que autoriza pela string do input

Registrado **aqui uma vez** e referenciado pelo grupo `MEN`, porque não é do
`agent_role`: é dos **dois** gates de papel da casa.

| gate | identidade assinada disponível? | o que ele consulta |
|---|---|---|
| `evaluation_context_get` / `_submit` | `instance_id` no token, **descartado** | `participant_id` do input |
| `message_send` (@mention) | `instance_id` **extraído** (`senderInstanceId`, l.403) e usado para outra coisa (l.426) | `participant_id` do input (l.421) |

Nos dois casos a identidade real está em mãos e não é usada para a decisão. Quem escrever
um gate novo de papel deve olhar esta tabela antes.

---

## Fases

| fase | o que faz |
|---|---|
| **R1** | Remover os dois `if` de `agent_role` (`evaluation_context_get`, `evaluation_submit`), mantendo `readAgentIdentity` para procedência. Inverter o `smoke_agent_role_gate.sh` em **testemunha** — o T4 passa a afirmar que o contexto **é** entregue e que a procedência sobrevive |
| **R2** | Decidir a procedência (§ Pendência): fonte do `agent_type_id` no `submit` |
| **R3** | Remover `agent_role` do schema, registry, syncer e bridge; `orchestrator` e o `role` sintetizado saem junto. Recusar o campo **nomeando** na entrada da API, como a lápide do `unrestricted` — Zod/pydantic ignoram chave desconhecida e o remetente veria 200 sobre no-op |

R1 antes de R3; R2 pode correr em paralelo com R3 desde que não dependa do campo.

---

## Riscos

1. **Perde-se a detecção de avaliador mal configurado.** É o único efeito real. Mitigação:
   validar no registry/portão que um pool de avaliação aponta para skill de avaliação —
   é onde o erro nasce, e onde ele é barato.
2. **A remoção do campo de entrada tem custo medido** (lápide do `unrestricted`,
   2026-08-31): quem continuar mandando recebe 200 sobre no-op. Daí a recusa nomeada na R3.
3. **Este ADR pode estar errado como o anterior.** O que o derrubaria é alguém descrever
   um cenário em que um não-avaliador obtenha um `session_id` fechado, amostrado e dentro
   do TTL. Se aparecer, a D4 (consertar em vez de remover) volta à mesa — ela está escrita
   para isso.

---

## Referências

- `TODO.md` § *Gate de evaluator: o que ele verifica não é quem chama*
- `CHANGELOG.md` 2026-08-31 § *A lápide do `unrestricted`* — custo de remover campo de entrada
- `infra/test/smoke_agent_role_gate.sh` — o que vira testemunha na R1
- `pending.md` grupo `MEN` — o outro gate com o mesmo defeito estrutural
