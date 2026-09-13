# ADR — Porta de Identidade: a plataforma identifica, prova e registra; a régua é da aplicação

> **Status:** proposto · **Data:** 2026-09-11
> **Emenda:** [`adr-identity-channel-possession.md`](adr-identity-channel-possession.md) — a decisão #4
> (o default seguro) muda de DONO, e `verification_class` deixa de ser o que abre portão.
> **Absorve:** §6 e §7 de [`identity-resolver-nivel-b-spec.md`](../product/identity-resolver-nivel-b-spec.md)
> (contrato com o nível a; fluxo de resolução no inbound). Os Lookups 1 e 2 e o `PendingEntry`
> continuam como estão.
> **Aplica:** [`adr-agent-flow-single-authored-level.md`](adr-agent-flow-single-authored-level.md) à
> porta de inbound — mesmo precedente do `skill_dialog_runner_v1`.
> Os três documentos ficam **intactos como raciocínio de época** e ganharam nota datada apontando
> para cá.
> **Descritivo de uso:** página "Porta de Identidade" (revisada com o dono em 2026-09-10/11).

> ⚠️ **DESAMBIGUAÇÃO.** "Três níveis" nomeia dois modelos neste repositório. Esta ADR trata do
> modelo de FLUXO DE AGENTE (N1 I/O · N2 canal · N3 negócio), já dissolvido pela ADR do nível
> autorado. O modelo de ESCOPO `segment` / `session` / `journey` (`CLAUDE.md` § *Never create a
> wide container…*) segue vigente e é USADO aqui (D5), não alterado.

---

## 1. O problema

A proposta de partida era resolver identificação **de forma genérica**: uma camada que
identificasse o cliente e devolvesse um **índice de confiança** para a aplicação decidir. A
discussão com o dono mostrou que essa imagem estava errada, e o motivo é de escopo, não de gosto:

> Identidade tem duas metades com donos diferentes. O **mecanismo de evidência** (OTP, biometria,
> chegada autenticada pelo canal, atestação de terceiro) é universal e sem negócio — é plataforma.
> A **régua** (o que basta para quê) é irredutivelmente do negócio, e varia por operação dentro do
> mesmo tenant. Um índice tenta tornar a régua exprimível do lado da plataforma; mas índice só tem
> sentido contra um limiar que só o negócio conhece. Ele não remove a decisão — acrescenta uma
> indireção antes dela.

É a mesma forma que a ADR do nível autorado achou no N2: **um contêiner para o que já tinha casa.**

## 2. O que foi medido (2026-09-10/11)

A premissa do dono — *"OTP só faz sentido contra um cadastro obtido FORA dos meios imediatos da
plataforma (ANI, e-mail de origem)"* — não descrevia um risco futuro. Descrevia o estado atual:

**(1) O índice de confiança já existe, é exposto, e mede a coisa errada.**
`KIND_CONFIDENCE` ([normalize.py:19](../../packages/channel-gateway/src/plughub_channel_gateway/identity/normalize.py))
é confiança no TIPO da âncora (`princ 0.95 · cpf 0.90 · email 0.80 · phone 0.70 · dev 0.30`), e o
bônus de verificação **não entra** no número exposto (comentário em `:87`). Um CPF que qualquer um
digitou vale 0,90; um telefone provado por OTP vale 0,70.

**(2) O OTP em produção não entrega nada, e diz que entregou.** *(Fechado em 2026-09-13, PID-10: sem
canal, `sent: false, reason: delivery_unavailable`.)* `_deliver` só loga sob flag de dev;
o ramo de produção é `TODO(prod)` ([otp.py:109](../../packages/channel-gateway/src/plughub_channel_gateway/identity/otp.py)),
e `challenge` devolve `{"sent": true}` de qualquer jeito.

**(3) O fluxo vivo desafia um CPF.** *(Fechado em 2026-09-13, PID-10 — mas o que ele gravou ficou: 25
CPFs `possessed`, IDN-13 — rebaixados no mesmo dia: leitura, escrita e migração do boot.)* `skill_limite_entrada_v1` faz `otp_challenge(kind: cpf, value:
<o CPF digitado>)` (`:210`). Não existe canal de entrega para um CPF.

**(4) `possessed` é fato DURÁVEL da âncora lido como fato da SESSÃO.** O `resolve_or_provision`
devolve a classe gravada na âncora ([index.py:205](../../packages/channel-gateway/src/plughub_channel_gateway/identity/index.py)),
e `pending_workflow_get` abre o portão quando ela é `possessed`
([workflow.ts:429](../../packages/mcp-server-plughub/src/tools/workflow.ts)) — sem amarração à
sessão presente, ao canal ou à idade da prova. O cliente real faz OTP uma vez; qualquer um que
digite aquela âncora depois recebe o `resume_token`. **Latente** — só porque (2) impede qualquer
âncora de virar `possessed` em produção. Ligar a entrega abre, no mesmo commit, a funcionalidade e
o vetor.

**(5) A evidência pode ser forjada.** `context_set` não exige `session_token`, aceita qualquer
`session_id`/`tenant_id`/tag, e o `source` é o chamador que informa
([session.ts:938](../../packages/mcp-server-plughub/src/tools/session.ts)); `writeContextTag` não
bloqueia `core.*` ([journey.ts:230](../../packages/mcp-server-plughub/src/tools/journey.ts)). A
reserva do `core.*` já é furada por dois fluxos autorados (`skill_limite_processo_v1:238`,
`skill_revisao_treplica_v1:70`) — ela existe no cadastro de tags, não na escrita.

**(6) As tools de identidade e retomada não sabem quem chama.** `workflow_resume` recebe só
`resume_token` e `decision` (`workflow.ts:248`); `pending_workflow_get`, âncoras e `tenant_id`; o
`withGuard` é só o detector de injeção ([tool-guard.ts:59](../../packages/mcp-server-plughub/src/infra/tool-guard.ts)).

**(7) As oito rotas de identidade do channel-gateway não têm credencial**
([main.py:1159–1250](../../packages/channel-gateway/src/plughub_channel_gateway/main.py)) — nenhum
`Depends`, nenhum middleware global —, o `tenant_id` vem do corpo, e a UI proxia `/v1/channels` em
dev (`vite.config.ts:68`) e em produção (`Dockerfile:158`). O `probe_route_credential_coverage.sh`
mede a analytics-api: terceira ocorrência de *"um censo desenhado para um eixo não prova nada
sobre o eixo vizinho"*.

**(8) Não há eixo de procedência.** O único eixo da âncora é `verification_class` — *como* foi
provada. Nada diz *de onde veio*. A aba Cliente do Console cria cadastro e grava atributos por
desenho (`ClienteTab.tsx:118`), e seu resultado é indistinguível de uma âncora vinda do CRM.

**(9) A chegada autenticada não gera âncora.** O `from` do WhatsApp é o E.164 do remetente
autenticado pela Meta (`whatsapp.py:155`), e o adapter o usa como chave de sessão e o repassa como
`customer_id` BRUTO no inbound — mas não produz âncora nenhuma. O envelope `origin_identity` da
spec §4.4 **não existe em lugar nenhum do código.**

**(10) As rotas de deploy não têm portão.** `app.ts:49`: *"slots sub-routes (deploy) — não
gateado nesta fatia"*, com tenant e usuário vindos dos headers `x-tenant-id`/`x-user-id` (default
`system`). A edição de skill, ao contrário, exige `skill_flows.editar` (`:50`).

**(11) O deploy em lote registra sem mudar.** `skill_deploy` (`pool_ids`) → `POST
/v1/skills/:id/deploy` grava `skill.flow` e um `SkillDeployment` com os pools
([skills.ts:410](../../packages/agent-registry/src/routes/skills.ts)), e **não toca slot**. O
bridge executa o snapshot do slot `current`; `skill.flow` só vale para pool não migrado.

**(12) O merge de journey descarta evidência nova.** `migrateJourneyContext`
([journey.ts:312](../../packages/mcp-server-plughub/src/tools/journey.ts)) copia o hash da journey
absorvida para a canônica **só onde a canônica ainda não tem a tag**, e apaga a origem. A
sobrevivente é a raiz mais ANTIGA — o processo pendente. Como a prova roda antes do merge, uma
evidência nova é descartada sempre que o processo já guardar uma velha.

**(13) O hash de contexto da sessão morre em 4 h** e o helper de extensão do suspend não o cobre
(cobre `session:{id}:meta`, `{t}:resume_tokens` e `resume_meta`).

**(14) Os dois intakes vivos repetem os mesmos 18 step ids**, na mesma ordem
(`skill_limite_entrada_v1` × `agente_portabilidade_intake_v1`). Não é semelhança; é cópia.

## 3. Decisão

### D1 · Identificar não autoriza

A plataforma **resolve** identidade, **executa** mecanismos de prova, **registra** evidência e
**recusa conceder capability sem evidência**. A régua — *quanto basta* — é da aplicação. Nada
nesta ADR produz um número de confiança.

### D2 · A porta é pool + runner de plataforma

O endereço de chegada (`ChannelEndpoint(canal, identificador)`) resolve um **pool**; o skill do pool
é o runner de plataforma `skill_intake_runner_v1`, parametrizado por `config_params` →
`config_json` → `$.config.*`, com o roteiro em `DialogForm`:

```yaml
door_mode:         entry | resume | both
require:           []                 # explícito; vazio é válido, ausente é erro
on_new_pool:       <pool do N3>       # disparado quando não há pendência
degrade_target:    <pool>             # ramo OBRIGATÓRIO de falha
dialog_form_id:    <forma>
accept_resume_key: true | false
```

Quando é possível ter identificador distinto, **entrada e retomada são portas diferentes**. Quando
não é — o WhatsApp responde na mesma thread; a voz cai no número principal —, a porta `both`
acumula os dois papéis.

### D3 · Mecanismos são agentes de plataforma; o orquestrador é dono da composição

Cada mecanismo (OTP, biometria) é único e de plataforma. `skill_identity_orchestrator_v1` os executa
seletivamente, com **um `config_param` por mecanismo** (`enable_otp`, `enable_biometrics`). Tipo novo
nasce `required: false` **com default** — senão o `required-config` reprova o deploy de todos os
pools existentes. O orquestrador só se justifica sendo dono da COMPOSIÇÃO; enquanto não for, nasce
dentro da porta, e a interface (`delegate` + retorno padrão) é a mesma nos dois casos.

### D4 · Evidência sem score; exigência é CONJUNTO de mecanismos

A exigência é declarada como conjunto (`["otp"]`), nunca como limiar. Cada mecanismo grava tags
escalares — não JSON: `choice` não compara campo dentro de string, a camada de masking veria blob
opaco, e o `interpolate` injetaria JSON cru num prompt.

```
core.journey.identity.<tipo>.status             not_run | pending | verified | failed | expired
core.journey.identity.<tipo>.anchor_kind
core.journey.identity.<tipo>.verified_at
core.journey.identity.<tipo>.source             procedência da âncora
core.journey.identity.<tipo>.proven_in_session  a sessão que provou
```

Sem `value`: nenhuma leitura razoável dele seria o código (segredo que nunca sai do `OtpService`)
nem a âncora em claro (PII). O mecanismo **sempre escreve o status**, inclusive ao falhar ou
expirar — senão a ausência da tag é ambígua entre *não rodou* e *rodou e falhou*.

### D5 · Escopo: journey pelo alcance, sessão pela validade

A retomada é fato de PROCESSO, e o processo retomado (sessão B) precisa ver o que a porta (sessão
A) provou — por isso a evidência mora na **journey** (`core.journey.*`, raiz canônica). Mas *"quem
está aqui provou"* é fato de UM acesso: um flag de journey de 30 dias seria herdado por qualquer
contato posterior do mesmo processo, que é o vetor de (4) com prazo. O registro carrega o
**discriminador do fenômeno** (`proven_in_session`, `verified_at`) — a regra de Postura sobre
identidade derivada —, e o consumidor só aceita evidência da sessão que está retomando, ou da sua
continuação direta, dentro de uma idade máxima.

### D6 · Quem verifica grava; o piso mora no dispensador

- A evidência é gravada **no servidor, na mesma chamada que verifica** (`otp_verify` escreve as
  próprias tags). `context_set` **recusa** `core.journey.identity.*` e `core.identity.*` —
  medido: zero fluxos quebram (recusar `core.*` inteiro quebraria os dois de (5)).
- `pending_workflow_get` e `workflow_resume` passam a exigir **`session_token` assinado**. Receber
  a sessão como argumento seria o chamador declarando a própria autorização (o defeito da CAP-01).
- O token só é liberado, e a retomada só ocorre, **contra evidência da sessão que pede** satisfazendo o
  `resume_requires` da pendência. A plataforma exige *que haja* evidência; nunca lê *quanta* basta.
- **Quem transporta a evidência para o processo retomado é o `workflow_resume`**, no servidor — não
  o merge, que por (12) descarta a evidência nova.

### D7 · A exigência de retomada é do N3, com mínimo no skill

O N3 declara, no ponto em que já suspende, o que a retomada exige e por onde se volta:

```yaml
resume_requires: "$.config.resume_requires"   # união objeto | ref, como channel_policy
resume_door:     "$.config.resume_door"
```

O **mínimo** é declarado no skill (atrás de `skill_flows.editar`) e não é editável no slot. A
config do slot tem de **conter** o mínimo (config ⊇ mínimo); se não contiver, o deploy é
**recusado** (`judgeIdentityFloor`, junto de `judgeRequiredConfig`/`judgeMaskedDeploy`), nunca
ajustado em silêncio — um ajuste faria a tela mostrar `[]` enquanto roda `["otp"]`. Config ausente é
erro; `[]` é válido quando o mínimo permite. ⚠️ Enquanto (10) valer, o mínimo protege o VALOR mas
não a escolha do skill: trocar o `skill_id` no slot o contorna (PID-07 precede PID-06).

### D8 · Âncoras entregáveis × não-entregáveis; OTP só contra procedência autoritativa

- **Entregáveis** — `phone`, `email`: existe canal para mandar código. **Só estes admitem OTP.**
- **Não-entregáveis** — `cpf`, `princ`, `dev`: identificam, nunca recebem código.

OTP só é emitido contra âncora entregável **de procedência autoritativa**. O mecanismo **recusa sobre
si mesmo**, de forma explícita — nunca `sent: true` sem entrega. OTP contra âncora declarada é
tautológico: prova que o interlocutor tem o número que ele próprio informou.

> **Implementado em 2026-09-13 (PID-10).** Três escolhas que o texto acima não dizia: **(1)** a
> recusa tem DUAS casas e cada uma é dona de um fato — o `OtpService` recusa o que independe do
> cliente (`undeliverable_kind`, `delivery_unavailable`; `DELIVERABLE_KINDS` em `normalize.py`) e o
> adaptador recusa a âncora que `anchor_provenance` não dá como `authoritative` **para o
> `customer_id` que pede** (`anchor_not_authoritative`); a do mecanismo é consultada primeiro, para
> um CPF não responder sobre o cliente. **(2)** O modo dev é um **canal**, e diz que é
> (`delivery: "dev_log"`); o default dele passou a desligado, e desligado o desafio recusa. **(3)**
> O desafio é **amarrado ao cliente** (`subject`), e o verify só confere para o mesmo — sem isso o
> portão da procedência seria contornável na conferência, que anexava a posse a qualquer
> `customer_id` informado.
>
> Como não se guarda telefone em claro, **o número do "canal cadastrado" vem do cliente**: o
> `skill_limite_entrada_v1` pede o celular, e o que prova é casar com o cadastro E receber o código
> nele. A recusa tem uma frase só, qualquer que seja o motivo — dizer *"este não é o número
> cadastrado"* faria do passo um oráculo de telefone por CPF.

### D9 · Chegada autenticada é evidência, produzida pelo adapter

- **`princ`** — o `sub` do login federado do tenant. Para quem chega de uma área logada da loja ou
  do app, a identificação está resolvida na chegada; `require: []` é a configuração correta.
- **`(whatsapp, from)`** — quando o `from` bate com o telefone **cadastrado**, é evidência de canal;
  se for outro número, volta ao OTP.

Quem produz é o adapter que recebeu a mensagem ou o JWT autenticado (`origin_identity`), nunca um
fluxo. Limite conhecido de toda prova por posse, que vale igual para SMS: número reciclado pela
operadora passa a ser do novo dono.

### D10 · A chave de volta

- **Token longo** (`resume_token`) na marcação, para canais com link (webchat por parâmetro de URL).
  Mora no índice `{t}:resume_tokens`, **nunca no hash de contexto** (13).
- **Não há código curto como artefato próprio.** Um código de 15 min emitido na marcação expira
  antes de o cliente voltar; emitido na volta, é o OTP ao canal cadastrado.
- **Voz:** o token não passa (DTMF, DNIS e ANI não o carregam). A associação vem da identidade:
  ANI → cliente → pendência, com OTP por SMS digitado em DTMF — que **não** é tautológico mesmo
  quando o cadastrado é o próprio ANI (forjar ANI não dá acesso ao chip). **ANI bloqueado =
  identificação ausente**: mesmo caminho do anônimo (CPF por DTMF → OTP ao canal cadastrado), não
  recusa. O ideal — a plataforma liga para o número cadastrado — depende de discador e plano de mídia.
- O **transporte** da chave é capacidade de canal, declarada na tabela canônica. No WhatsApp a
  porta passa a ter regra de parse da primeira mensagem, e a resposta a chave inválida tem forma
  constante.

### D11 · Porta compartilhada: prova para VER, prova para ENTRAR

- Quem libera a **lista** de pendências é a evidência de **chegada**: forte (`princ`, `from` =
  cadastrado) → lista direto; fraca (CPF digitado, ANI, webchat anônimo) → prova mínima (OTP ao
  canal cadastrado) → lista.
- A **seleção** aciona a exigência do item escolhido.
- A lista é ordenada pela pendência mais próxima de expirar (`expires_at`; hoje o
  `find_pending_by_customer` achata a primeira em ordem arbitrária).
- **Abrir processo novo nunca exige identificação** — só a retomada exige. A duplicata que isso
  permite é decisão do N3 (por exemplo, checar pendência de mesmo `intent`), não da porta.

### D12 · Degradação é caminho, não exceção

Identidade insuficiente é comum e legítima. O ramo é **obrigatório no contrato** da porta: destino
em config (`degrade_target`), roteiro em `DialogForm`. As saídas são servir o que não exige
identidade, encaminhar a humano, ou começar processo novo.

### D13 · Procedência é o segundo eixo da âncora

`verification_class` (*como foi provada*) ganha o irmão **procedência** (*de onde veio*):
`declared | channel_origin | authoritative | operator`. A fonte autoritativa da v1 é o cadastro
`identity.customers`, **assumido confiável como premissa de trabalho**, com as lacunas de (7) e (8)
registradas como fichas.

> **Implementado em 2026-09-13 (PID-12), com a porta escolhida pelo dono em 2026-09-12:** a
> **importação com credencial** é a única que carimba `authoritative` —
> `POST /v1/channels/webhook/identity/import`, campo ABAC `contacts.importar_cadastro`, tenant do
> JWT. A premissa *"`identity.customers` é confiável"* deixa de ser presumida: passa a valer para as
> âncoras que vieram por essa porta, e só para elas. Três escolhas que a implementação fez e que o
> ADR não dizia: **(1)** a trava mora no índice, não na rota, porque as rotas irmãs seguem sem
> credencial (IDN-06); **(2)** a procedência **zera quando a âncora muda de cliente**; **(3)** o
> legado fica `NULL` (*não registrada*), nunca `declared`.
>
> **A leitura (IDN-07, mesmo dia):** a procedência é lida **só do cadastro durável** e **só quando
> ele atribui a âncora ao mesmo cliente** — `anchor_provenance(tenant, customer_id, kind, value)` é a
> pergunta da PID-10. Não é copiada no índice Redis: `authoritative` só nasce no PG, e duas casas
> para a mesma confiança foi o defeito da IDN-09. A conferência do cliente existe porque o índice
> Redis pode apontar a âncora para quem o cadastro não reconhece (IDN-10).

## 4. Consequências

**Contabilidade de segmento** — a porta suspende no `delegate`, e suspender libera o agente: porta →
mecanismo → porta são segmentos SEQUENCIAIS, sem sobreposição. `agent_time_ms` cresce, e
corretamente (precedente: reclassificar o agente de fila moveu `retencao_humano` em +4,8%). TMA
(`elapsed_time_ms`) não muda. A contagem de segmentos cresce, e o relatório de complexidade vai
ler um contato simples como complexo. `participation_intervals` subconta: dois segmentos do mesmo
participante colidem numa linha (`ORDER BY (tenant, session, participant)`); a tabela certa é
`segments`.

**O OTP fica desligado de fato** até existirem procedência autoritativa (IDN-07, PID-12) e entrega
real. No dia da entrega, `enable_otp: true` produz recusa explícita, não prova. O cenário de
contato novo funciona inteiro; a retomada funciona com `require: []` e com evidência de chegada. O
roteiro de demo perde o passo de OTP — decisão consciente, porque a alternativa é a tautologia.

> **Atualizado em 2026-09-13 (PID-10).** Com a importação autoritativa de pé, o demo **recuperou** o
> passo de OTP — para cliente importado, ao celular cadastrado, com entrega `dev_log`. Sem entrega
> real, fora do demo, segue desligado de fato, e agora com recusa explícita. ⚠️ **E isto torna o
> (4) VIVO para cliente importado:** quem conclui o OTP deixa o telefone `possessed` no cadastro, e
> a posse durável volta a ser lida como fato da sessão por quem apresentar aquele telefone. O que
> fecha é D5/D6 (PID-01..03), não esta fatia. Exposição no dia: **zero** clientes importados fora
> das fixtures de probe.

**Release de skill de plataforma custa N promotes.** Skill é seed-if-absent (`CLAUDE.md` §
Configuration): corrigir o runner é `PUT` com `x-skill-publish` + `set-next`/`promote` por porta,
e um bug do runner atinge todas as portas ao mesmo tempo. Precisa de promote em lote sobre slots
(PID-08).

**v1 = clusters idênticos.** O tier Enterprise de cluster dedicado existe só na especificação
([14-multi-tenant.md](../sections/14-multi-tenant.md)); não há helm, k8s nem terraform em `infra/`.
A v1 assume um schema, uma versão e um conjunto de skills de plataforma. "Idêntico" precisa de
verificação de versão para não virar promessa sem mecanismo.

## 5. O que não muda

`ChannelEndpoint`, `resume_token`, `PendingEntry`, `workflow_trigger`, `workflow_resume` (ganha
`session_token`, não perde nada), `delegate`, `dialog_runner`, `form_get`, a tabela de capacidade
de canal, os Lookups 1 e 2 e o modelo de segmentos. **O desenho é composição do que já existe**; as
peças novas são a porta, o orquestrador de prova, o eixo de procedência e a identidade assinada nas
tools.

## 6. Fatias

Caminho crítico: **PID-01 → PID-02 → PID-03** (sem identidade assinada e sem evidência que não se
forja, a porta não garante nada contra fluxo autorado). **PID-07 precede PID-06.** **PID-10 depende
de IDN-07.** A migração dos dois intakes (PID-04) vem **depois** da chave de retomada.

| ficha | o quê | decisão |
|---|---|---|
| PID-01 | `session_token` assinado nas tools de identidade e retomada | D6 |
| PID-02 | quem verifica grava; `context_set` recusa `core.journey.identity.*` / `core.identity.*` | D6 |
| PID-03 | `workflow_resume` transporta a evidência para o processo retomado | D5, D6 |
| PID-04 | `skill_intake_runner_v1` + migração dos dois intakes | D2 |
| PID-05 | `skill_identity_orchestrator_v1` (composição, um param por mecanismo) | D3 |
| PID-06 | `resume_requires`/`resume_door` + mínimo no skill + `judgeIdentityFloor` | D7 |
| PID-07 | portão nas rotas de slot do agent-registry | D7 |
| PID-08 | deploy em lote que registra sem mudar + promote em lote sobre slots | §4 |
| PID-09 | `origin_identity` no adapter: `princ` e `(whatsapp, from)` | D9 |
| PID-10 | OTP só entregável e autoritativo, recusa explícita; corrige o desafio a CPF | D8 |
| PID-11 | lista na porta compartilhada gated por chegada; ordem por `expires_at` | D11 |
| PID-12 | quem grava procedência `authoritative`, e com qual credencial | D13 |
| IDN-06 | credencial nas rotas de identidade do channel-gateway | (7) |
| IDN-07 | eixo de procedência na âncora | D13 |
| IDN-08 | a aba Cliente carimba `operator` | (8) |

## 7. O que esta ADR NÃO decide

- ~~**Quem grava `authoritative`**~~ — **decidido pelo dono em 2026-09-12 e implementado em
  2026-09-13 (PID-12): a importação de base com credencial.** As outras duas (MCP de domínio no CRM
  do tenant, que é o alvo arquitetural; federação de login) seguem nomeadas e não eleitas.
- **Biometria** — desenhável, não executável: nenhum canal do parque captura mídia (Arc 15).
- **Voz** — todo o D10 de voz é papel enquanto VOZ-01/VOZ-03 valerem.
- **A forma do step-up no meio do processo** (revelar dado mascarado, por exemplo) — usa a mesma
  regra de D5/D6, mas o contrato do N3 para pedir isso não está desenhado aqui.
- Não toca o modelo de escopo `segment`/`session`/`journey`. Ver a desambiguação no topo.
