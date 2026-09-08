# ADR — Granularidade dos módulos ABAC e delegação por template

**Status:** proposto · **Data:** 2026-08-31 · **Demanda do dono:** rotatividade alta de
funcionários exige que o supervisor contrate sem que ele possa reescrever a própria fronteira.

> ⚠️ **EMENDADO em 2026-09-08 — leia a emenda no fim do arquivo antes de implementar
> qualquer coisa daqui.** A **D2** (veículo da delegação) e a **D4** (`delegable`) foram
> **substituídas**, e a **D5** foi fechada. O texto abaixo fica na íntegra porque é a evidência
> de por que o desenho mudou — mas quem implementar a D4 lendo só esta metade constrói um
> mecanismo que a emenda removeu. D1, D6 e D7 continuam valendo sem alteração.

---

## Contexto

O arco ABAC TOTAL (2026-08-27) fechou os bypasses e tornou o menu *grant-first*. Ele deixou
**duas costuras abertas**, e a operação real esbarra nas duas ao mesmo tempo.

### Costura 1 — não existe veículo de delegação no eixo de ESCOPO

O split `config.users` × `config.permissions` separou *administrar pessoa* de *conceder
capacidade*. Correto — mas o supervisor, que detém a primeira, **não consegue contratar**:

```python
# models.py — CreateUserRequest
roles: list[Role] = ["operator"]     # default, aceito sem config.permissions
accessible_pools: list[str] = []     # <- zero pools
```

Módulos vêm do preset do papel (passo 3). **Pools não têm preset**, por decisão: pool é do
tenant, não da plataforma. O contratado nasce **enxergando nada** e quem o contratou **não pode
corrigir**, porque `accessible_pools` é campo de capacidade (`_CAPACITY_FIELDS`).

Medido em 2026-08-31, e é por isso que a costura não é teórica: o `supervisor@` da instalação
detém `config.permissions: read_write` com 2 pools em escopo — ou seja, **pode marcar
"selecionar todos" e ir a 36**. O `seed_auth.py` exclui esse campo do supervisor de propósito,
com comentário que descreve essa exata consequência. **O modelo está certo; a população está
errada, e nada nunca conferiu uma contra a outra** — preset aplica na criação e não retroage,
seed é if-absent. Promessa sem mecanismo.

### Costura 2 — campos que carregam mais de um fato

`config` já é o módulo mais granular (9 campos). Os contêineres largos estão fora dele, e os
próprios rótulos os denunciam:

| campo | rótulo | fatos distintos |
|---|---|---|
| `contacts.operacao` | "Monitor em tempo real, Agent Assist" | Monitor (observar) × Console (atender) — e o seed admite: *"este campo também abre o Console"* |
| `workflows.operacao` | "Editor, Monitor, Calendário" | autorar × observar × agenda |
| `config.resources` | "Pools, Agent Types, Skills, Instâncias" | quatro — e é o campo que gateia **criar pool** |
| `contacts.visualizar` | "Visualizar contatos e transcrições" | Analytics inteiro, mais a transcrição de um contato |

É a regra que o próprio split registrou: **um campo cujo rótulo tem "e" provavelmente são dois
fatos.** Três destes rótulos são listas separadas por vírgula.

### Lacuna de registro

`ls docs/adr/ | grep -i abac` devolve **vazio**. O modelo ABAC nunca teve ADR — existe apenas o
guia de implementação (`docs/guias/abac-permission-system.md`), e a justificativa do split mais
sensível vive num comentário de `infra/modules.yaml`. Este ADR também fecha essa lacuna.

---

## Decisões

### D1 — O discriminador de corte é DETENTOR, não contagem *(fechada)*

Um campo se divide quando **as duas metades têm detentores legítimos diferentes**. Se todo mundo
que deve ter A também deve ter B, o corte adiciona superfície de config sem adicionar decisão —
e superfície de config é custo permanente (tela, seed, preset, backfill, censo).

`contacts.operacao` falha alto: o supervisor **observa**, o operador **atende**.
`config.dashboards` provavelmente passa: quem edita e quem publica são a mesma pessoa.

### D2 — Delegação é do PACOTE, nunca do campo *(fechada)*

O supervisor não escolhe pools; ele coloca a pessoa num pacote que **outra pessoa aprovou**. É o
organograma: o supervisor decide *quem é operador*, o admin decide *o que um operador pode*.
Rotatividade alta é no primeiro eixo.

O veículo é o **template de permissão**, que já existe (tabela, CRUD, UI). Hoje ele **não serve**:
aplicar é *copy-on-create no cliente* — só preenche o formulário, e o `POST /users` sai com
`accessible_pools` no corpo, que `_assert_may_grant` recusa. E `GET /templates` já é gateado por
`config.permissions`, então o supervisor nem lista.

### D3 — Aplicar template é rota própria; a capacidade vem do TEMPLATE, nunca do corpo *(fechada)*

`POST /v1/auth/users/{id}/apply-template/{tid}` (e o equivalente na criação). O corpo **nunca**
carrega campo de capacidade — o servidor os lê da linha armazenada. Isso preserva o
discriminador `model_fields_set`, que é o que separa *"omiti e aceitei o default"* de
*"enviei e concedi"*.

### D4 — `delegable` é do template, e a regra é DERIVADA *(fechada — é o que fecha a escalação)*

Sem esta decisão o desenho inteiro é um caminho de escalação. Três fatos medidos que se somam:

1. `password` **não** é campo de capacidade, de propósito (resetar senha é trabalho de quem
   administra pessoas);
2. `roles`/`accessible_pools` **são** — por isso hoje quem só tem `config.users` não cria um admin;
3. a rota da D3 contorna `_assert_may_grant` **por construção** — esse é o ponto dela.

Somados: `config.users` + aplicar *"Admin Total"* + definir a senha + entrar = **escalação
completa**. Portanto:

- o template carrega `delegable: bool`;
- **marcar `delegable` é ato de `config.permissions`**;
- e um template que conceda `config.permissions` — ou qualquer campo classificado como
  privilegiado — **não pode ser marcado delegável**: o servidor **recusa**, computando, não
  perguntando. Flag que depende de quem clica não é guarda, é armadilha.

### D5 — Quem pode aplicar é campo próprio *(ABERTA — decisão do dono)*

Proposta do dono: configurável nas permissões de Access. Três formas, e elas não são equivalentes:

| | forma | consequência |
|---|---|---|
| a | campo novo `config.users_from_template` | explícito, mais um campo no catálogo |
| b | subir junto com `config.users` (quem administra pessoa pode aplicar bundle delegável) | zero campo novo; amarra os dois fatos |
| c | `access: read_write` em `config.users` habilita; `read_only` não | usa o domínio que já existe |

**Não decidida aqui.** A D4 é o que torna qualquer das três segura; a escolha entre elas é de
produto.

### D6 — Lista de cortes *(ABERTA — decisão do dono, um campo de cada vez)*

Candidatos medidos, na ordem em que o rótulo denuncia:

| # | campo | corte proposto |
|---|---|---|
| 1 | `contacts.operacao` | `contacts.monitorar` (observar) × `contacts.atender` (Console/Agent Assist) |
| 2 | `workflows.operacao` | `workflows.editar` × `workflows.monitorar` (calendário já é `config.calendars`) |
| 3 | `config.resources` | `config.pools` × `config.skills` (Agent Types e Instâncias seguem quem?) |
| 4 | `contacts.visualizar` | recorte de Analytics por superfície — depende da AUT-01, que ainda não tem filtro de pool nos agregados |

Cada corte é uma **migração de dados**, não só de catálogo: todo portador do campo largo precisa
de backfill para os estreitos, senão o corte **rebaixa em silêncio** quem já trabalhava.

### D7 — Todo corte nasce com censo *(fechada)*

O defeito que a costura 1 expõe não é o grant errado; é **não haver mecanismo que confira a
população contra a declaração**. Nenhum corte entra sem um censo re-executável comparando quem
detém o campo no banco contra o que `role_defaults` + seed declaram.

---

## Fases

| fase | conteúdo | depende de |
|---|---|---|
| **G0** | Censo de `config.permissions`: quem detém × quem deveria. Instrumento antes de qualquer mudança | — |
| **G1** | D3 + D4: rota de apply-template + `delegable` + a recusa derivada | G0 |
| **G2** | D5: decidir e implementar quem aplica; UI de criação de usuário por template | G1 |
| **G3** | Revogar `config.permissions` do `supervisor@` (e de quem mais o censo apontar) | G2 — **nunca antes**, sob pena de quebrar a contratação |
| **G4** | D6 corte #1 (`contacts.operacao`) com backfill e censo | G0 |
| **G5** | D6 cortes #2 e #3 | G4 |
| **G6** | D6 corte #4 (Analytics) | AUT-01 |

**A ordem G1→G2→G3 é inegociável.** Revogar antes de existir o veículo tira a contratação do
supervisor sem dar nada em troca — e a operação, não o modelo, é quem paga.

---

## O que este ADR NÃO decide

- **`scopable` de `config.permissions`.** Hoje é `false`: a capacidade é do tenant inteiro, e por
  isso auto-conceder um pool **não é escalação para o detentor legítimo** — ele já manda no escopo
  de todo mundo. Se algum dia existir "admin regional", isso muda e a lista irrestrita de pools na
  tela de Access vira furo de verdade. Registrado como pergunta em aberto (AUT).
- **Escopo derivado de GRUPO** (Arc 9) como alternativa ao template. Sobreviveria à mudança —
  põe um pool no grupo e todos recebem —, mas é greenfield: medido **0 grupos** e **0 de 36 pools**
  declaram `agent_groups`. Fica como direção, não como fase.
- **A obrigatoriedade de pool na criação** (AUT-10/AUT-14): pool sem nenhum usuário não fica
  inacessível — a tela de Access lista todos os pools do tenant —, mas fica **sem vigia**, porque o
  roteamento não consulta `accessible_pools`. Tratado no grupo AUT.

---
---

# Emenda de 2026-09-08 — o guard é de RANK; a delegação por PACOTE sai

**Status da emenda:** aceito pelo dono na sessão de 2026-09-08 · **Escopo:** substitui a D2
(veículo), a D4 (`delegable`) e fecha a D5; preserva D1, D6 e D7 intactas.

> **Por que emenda e não reescrita.** As decisões originais continuam acima, na íntegra. Cada
> uma foi tomada com a medição disponível em 31/08, e reescrevê-las apagaria a evidência de
> *por que* o desenho mudou — mesmo motivo pelo qual `CHANGELOG.md` e `TODO.md` ficam fora das
> varreduras de renomeação. O que muda está aqui; o que não é citado, vale.

## O que a medição de 2026-09-08 derrubou, e como

**(a) A hierarquia de papéis não existe.** Foi proposta como guard e refutada pelo catálogo: o
guard por conteúdo aplicado aos `role_defaults` produz **a diagonal** — cada papel só consegue
criar um clone de si mesmo.

```
aplicador         admin  developer  supervisor  operator  business
admin                OK         OK          OK        OK        OK
developer         X(39)         OK        X(9)      X(4)      X(4)
supervisor        X(34)       X(4)          OK      X(3)      X(4)
operator          X(39)       X(4)        X(8)        OK      X(5)
business          X(38)       X(3)        X(8)      X(4)        OK
```

Nenhum par adjacente da cadeia proposta (`admin > developer > supervisor > operator,business`)
está ordenado: `supervisor` tem 9 campos que `developer` não tem; `operator` tem 3 que
`supervisor` não tem — inclusive `evaluation.contestar`, que é assim **de propósito** (o
operador contesta a própria avaliação; o supervisor revisa). Só `admin` é topo real; os outros
quatro formam uma **anticadeia**. São funções, não níveis — a mesma razão pela qual a
granularidade existe.

**(b) O diagnóstico da diagonal era de DADO, não de modelo.** Os três campos que bloqueavam
`supervisor -> operator` são lacuna de preset, não incompatibilidade: um supervisor de contact
center plausivelmente faz o que o operador faz. E a procedência é frágil por declaração própria
(`arc7-auth.md`): `admin`/`supervisor`/`operator` foram *"levantados do seed_auth.py"*;
`developer`/`business` são *"declarações mínimas que precisam de decisão"*. Nunca foram
desenhados.

**(c) O rank desarma a personificação; a presença a arma.** Sob regra de PRESENÇA (deter o
módulo em qualquer nível autoriza concedê-lo em qualquer nível), o delegado cria um usuário,
concede-lhe `read_write` num campo que ele só tem em `read_only`, define a senha
(`CreateUserRequest.password` é escolhida por quem cria) e entra na conta — **convertendo o
próprio `read_only` em `read_write` por procuração**. Sob RANK a procuração nunca excede o
outorgante em campo nenhum, e o mesmo caminho não ganha nada.

## Decisões da emenda

### E1 — Dois regimes, sobre os DOIS campos que já existem *(fecha a D5)*

| regime | campo | quem | pode conceder |
|---|---|---|---|
| **master** | `config.permissions: read_write` | **só o `admin@`, por seed** | qualquer campo, qualquer nível |
| **delegado** | `config.users: read_write` | quem administra pessoas | **≤ o que detém**, campo a campo (rank) + escopo ⊆ pools próprios |
| leitura | `config.users: read_only` | — | nada |

**Nenhum campo novo.** O "campo que libera todos os módulos dentro do Access" já é o
`config.permissions`, com esta declaração no catálogo: *"Conceder permissões (papéis, módulos,
escopo de pools)"*, `scopable: false`, `role_defaults: {admin: read_write}`. Criar outro seria
segunda casa para o mesmo fato. O split de 08-27 fica **preservado** — a alternativa avaliada
(discriminar por NÍVEL dentro de `config.users`, opção *c* da D5) fundiria os dois fatos de
volta num campo, contra a regra que motivou o split.

O que falta não é o campo, é a **população**: medido em 2026-09-08, **5 portadores** de
`config.permissions`, **3 deles com papel `supervisor`** (contas de probe). É a MOD-01 (censo)
-> MOD-04 (revogar).

### E2 — O guard é de RANK, e vale nos DOIS caminhos de escrita *(substitui a D4)*

`rank(concedido) <= rank(detido)`, campo a campo, **e** `escopo ⊆ escopo`. Três precisões que a
implementação não pode perder:

1. **O guard roda sobre o EFEITO, nunca sobre o literal.** O template carrega `role`, e
   `router.py:383` aplica `apply_role_preset` na criação — logo um template
   `{ role: "admin", module_config: {} }` atravessa intacto qualquer guard que inspecione só o
   `module_config`, concedendo os 44 campos pela porta do preset. Compara-se contra
   **`preset(role) ∪ module_config`**.
2. **O escopo tem semântica INVERTIDA em relação a `accessible_pools`.** Em `abac_can`,
   `scope: []` é **global** (ramo 1). Em `accessible_pools`, `[]` é hoje "todos" e a **AUT-03 o
   inverterá para "nenhum"** — os dois ficarão com sentidos opostos para lista vazia, no mesmo
   formulário. Copiar a lógica de um para o outro inverte a regra sem erro em lugar nenhum: a
   implementação leva a tabela-verdade dos dois lado a lado.
3. **As duas portas, um predicado.** `PUT /users/{id}/module-config` hoje exige
   `config.permissions` e **não compara nada** com o config do chamador. Guard só na rota nova
   seria *"duas portas para o mesmo dado, e só uma trancada"*, que este repositório já pagou
   duas vezes.

**`delegable` SAI.** Ele existia para aprovar um pacote que atravessaria o guard; sem
travessia, não há o que aprovar. E era subespecificado: booleano global não responde *"para
quem"* — todo detentor da capacidade de aplicar veria todos os pacotes delegáveis.

### E3 — Pré-requisito do rank: os presets passam a ser DESENHADOS, com gate

A regra do rank só entrega contratação enquanto valer *"quem contrata ⊇ quem é contratado"*
para os pares legítimos. Isso não pode ficar no zelo de quem editar o catálogo: **gate** que,
para cada par declarado (`supervisor->operator`, ...), asserta `preset(contratado) ⊆
preset(contratante)`. Sem ele, a próxima edição de preset quebra a contratação em silêncio, e o
sintoma não se parece com "preset errado" — parece "a tela não deixa".

Na mesma passada os presets deixam de ser engenharia reversa do seed de demo. **`developer`
vira `devops`**: o preset atual é de *autor de fluxo* (`skill_flows.*` + `config.context_map`),
papel que os DialogForms tornaram obsoleto; o que existe na operação é infraestrutura. Custo
medido: **1 linha** (`admin@`, que também tem `admin` — nada muda para ele) + as 7 listas de
`requireJwtRole` + o `Literal` + `_ROLES_VALIDOS` + locales. **`agente` NÃO é criado**:
identificador é inglês por regra da casa, e `operator` já É o agente humano — renomeá-lo custa
migração ampla para zero mudança de capacidade.

### E4 — O template é preset com PROVENIÊNCIA, nunca referência viva *(substitui a D2)*

Confirmado por medição que hoje é cópia pura: `auth.users` não tem coluna de template; o
`applyTemplate` é client-side; a rota que materializava (`POST /templates/{id}/apply`) saiu em
2026-08-30; e existem **zero templates** na instalação.

O modelo de *"a referência sobrevive se nada foi alterado, exceto pools"* foi **recusado**:
torna o template política viva para um subconjunto imprevisível; a exceção dos pools inverte a
intenção (pool é o eixo mais personalizado, então a referência sobreviveria justamente à edição
que mais quebra a equivalência); e o resultado da comparação é invisível na tela.

No lugar: **`created_from_template_id` + hash da config aplicada, carimbados no nascimento,
imutáveis e nunca consultados para autorizar.** Responde *"quem veio do template X"* — que é o
que o censo da D7 precisa — sem repermissionar ninguém. Reaplicar, se um dia for preciso, é
ação explícita com prévia, pelos mesmos portões. É o padrão do `deploy_version`: carimbo, não
ponteiro.

**O template não carrega pools.** O pacote é capacidade ("Operador"); os pools vêm do aplicador
no ato, escolhidos dentre os dele. Assim um template serve todos os supervisores, a pergunta
*"qual template para qual time"* deixa de existir, e contratar para pool alheio fica impossível
por construção — sem mecanismo novo.

### E5 — Pools são sempre ENUMERADOS e recortados pelo escopo do aplicador

Ninguém alcança o tenant inteiro por regra; o `admin@` alcança por **seed que enumera**. Isso já
é a decisão da AUT-15 — a lápide do `unrestricted` diz, textualmente: *"Para dar alcance total,
envie a lista completa de pools do tenant"*. O que não existe é o cumprimento:

- `admin@` tem `accessible_pools = {}` — e, como a inversão da AUT-03 **já está viva**, isso
  significa **NENHUM pool**: medido no mesmo dia, ele recebe `200` com **0 linhas** de
  `/reports/sessions` enquanto o ClickHouse tem 1 196 sessões em 23 pools (ficha AUT-43);
- a lista de pools no Access vem de `GET /v1/pools` (agent-registry), filtrada **só por
  tenant** — e o recorte tem de ser no servidor: filtrar no cliente não é cerca;
- pool novo nasce invisível. Mitigação derivável: quem **cria** o pool o recebe no escopo; e o
  aviso `orphansAfter`, já presente na tela, passa de cortesia a peça load-bearing;
- **efeito colateral bom:** a AUT-29 dizia não existir jeito de saber *"este chamador alcança o
  tenant inteiro"*. Sob enumeração sempre, vira computável (`escopo ⊇ universo de pools`) — a
  ficha sai de adiada-por-impossibilidade para aberta-por-trabalho.

> ⚠️ **Correção de algumas horas depois, no mesmo 2026-09-08.** Esta seção dizia *"depende da
> inversão da AUT-03; sem ela, enumerar o admin é decorativo"*. **A dependência não existe:** a
> AUT-03 fechou em 2026-08-31 e a inversão está viva. Eu a li como `bloqueado` numa nota de prosa
> do `pending.md` que sobreviveu ao fechamento da ficha por oito dias — o `probe_task_ledger.sh`
> não a alcança porque o ramo A lê id em **célula de tabela**, e aquilo era parágrafo. A conclusão
> inverte: a E5 não espera nada, ela ficou **urgente**. E a cauda que a nota temia existe, na
> direção oposta à prevista — não no consumidor que lê `[]` como "sem filtro", mas no **produtor**
> que escreveu `[]` quando queria "todos" (`seed_auth.py:225`, escrito antes da inversão e nunca
> migrado). Ficha **AUT-43**.

## Achados medidos que entram como trabalho próprio

| # | achado | evidência |
|---|---|---|
| **A1** | **O módulo `audit` não existe no catálogo.** 11 módulos no `infra/modules.yaml` e no `auth.module_registry` vivo; `audit` em nenhum dos dois — mas `Sidebar.tsx:170` gateia `nav.audit` por `audit.sessions` e a analytics-api o enforça de verdade. Sob grant-first: item **invisível para todos**, e a tela de Access não tem como concedê-lo (o formulário renderiza o catálogo). Terceira testemunha: `access.json` tem **12** entradas em `moduleNames`, incluindo `audit` | medido 2026-09-08 |
| **A2** | **Os 44 rótulos de campo não passam por `t()`.** O nome do MÓDULO usa `t('moduleNames.<id>', {defaultValue})`; o rótulo do CAMPO é `{schema.label}` cru do YAML, em português. Não existe `fieldNames` em locale nenhum, e a paridade EN×pt-BR está "perfeita" porque não há o que comparar | `ModulePermissionForm.tsx:131` |
| **A3** | **`write_only` sai — em dois passos, não por deleção.** Zero domínios o oferecem e **zero grants** o usam (128 grants: 106 `read_write`, 22 `read_only`), mas **dois call sites de produção** passam `min_access="write_only"` e `abac_can` levanta `ValueError` para valor desconhecido: removê-lo primeiro vira **500**, não negação. Ordem: migrar `channel-gateway/auth.py:47` (seguro — o domínio de `approvals.decide` é `[none, read_write]`) e `main.py:1621` (**campo dinâmico**, vindo de `session.resume_abac` — medir antes), depois remover. São **7 cópias** da tabela de rank: a canônica, 4 em TS e 2 em `infra/test/`; a da UI (`permissions.ts:23`) é **indexada**, ou seja carrega a "divergência 2" que o `py-authz` dá por fechada. E a prosa de 6 docs afirma a ordem TOTAL que o canônico não implementa | medido 2026-09-08 |
| **A4** | **17 rotas do `mcp-server-plughub` gateiam por PAPEL literal** (`requireJwtRole`), e o gate julga por **`roles[0]`** — autorização dependente da ordem em que os papéis foram digitados (`payload["role"]`, o `??` da frente, é claim que não existe). São as rotas do Console/Agent-Assist. Não são a "cauda de papel" fechada em 08-27 (aquela lista **bypasses**); são portões nunca migrados, e caem entre os três censos (o do verificador é Python; o de menu casa entrada->campo; o de rota mediu a analytics-api). **O alvo da migração já existe e está órfão**: `agent_assist.atender` (15 rotas) e `agent_assist.supervisionar` (`force-complete`, `work_queue/expire`) são campos declarados sem consumidor nenhum | `server.ts:921,928` |
| **A5** | **`config.users` é `scopable: false` e as rotas de usuário não recortam.** `GET /auth/users` lista o tenant inteiro; `PATCH /users/{id}` só protege alvo **privilegiado**; e `password` não é campo de capacidade, por decisão. Somados: um supervisor com `config.users` **lista, edita, desativa e reseta a senha do operador de qualquer outro supervisor** — e entra na conta. Não é escalação (é lateral), e nenhuma ficha cobre: a AUT-22 faz essa pergunta sobre o campo **vizinho** | medido 2026-09-08 |

## Higiene que deixa de ser pré-requisito, e continua devida

**Reset e criação de senha nunca deveriam revelar a credencial a quem administra** (convite ou
senha temporária, com troca no primeiro login). Sob PRESENÇA isso era pré-requisito de
segurança; sob RANK deixa de ser — a procuração não excede o outorgante. Mas o vetor **existe
hoje** contra qualquer alvo não-privilegiado, é anterior a esta discussão, e é o que separa
*administrar pessoa* de *assumir pessoa* — a linha exata que o split de 08-27 quis traçar e não
conseguiu, porque a senha tinha de ficar com quem administra pessoas.

## Fases revisadas

| fase | conteúdo | muda em relação ao original |
|---|---|---|
| **G0** | Censo de `config.permissions` (MOD-01) | inalterada — continua destravando tudo |
| **G1** | Guard de RANK nas duas portas (`apply` e `PUT module-config`), computado sobre `preset(role) ∪ module_config`, com escopo | substitui `delegable` + recusa derivada |
| **G1b** | Presets desenhados + gate do par `⊆`; `developer` -> `devops` | **nova** — pré-requisito de o G1 entregar contratação |
| **G2** | Rota de apply-template (capacidade do template, nunca do corpo) + proveniência carimbada + UI | a D3 sobrevive; a D5 já está fechada pela E1 |
| **G3** | Revogar `config.permissions` de quem o censo apontar | **destravada** — a ordem G1->G2->G3 continua, mas por coerência, não por risco de tirar a contratação |
| **G4–G6** | Cortes da D6 (#1 `contacts.operacao`, #2/#3, #4 Analytics) | inalterados. ⚠️ Se o corte #1 produzir `contacts.atender`, ele e a migração da A4 são **a mesma obra** |

**A ordem G1->G1b é a única inegociável desta emenda:** guard de rank sobre presets não
desenhados bloqueia a contratação mais ordinária do sistema.
