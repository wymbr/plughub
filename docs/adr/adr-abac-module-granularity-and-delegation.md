# ADR — Granularidade dos módulos ABAC e delegação por template

**Status:** proposto · **Data:** 2026-08-31 · **Demanda do dono:** rotatividade alta de
funcionários exige que o supervisor contrate sem que ele possa reescrever a própria fronteira.

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
