# ADR: OTP como workflow negocial + especialistas de canal, e primitivo de diálogo genérico (survey + OTP)

**Status:** Proposto (desenho travado 2026-07-06; implementação pendente). OTP MVP atual (Fase B, tool-based)
permanece funcional até a migração.
**Data:** 2026-07-06
**Componentes:** `packages/channel-gateway` (`identity/otp.py`), `packages/skill-flow-engine` (engine + skills),
`packages/mcp-server-plughub`, `packages/evaluation-api` (form store), `packages/platform-ui` (editor + config)
**Relacionado:** `docs/adr/adr-identity-channel-possession.md` (OTP/verification_class), `docs/arcos/customer-surveys.md`
(§16/§17 interpretador+editor, §19 retorno outbound), `docs/arcos/delegate-workflow-io.md` (delegate pattern),
Arc 19 (unified session / collect / channel-abstract)

---

## Contexto

O OTP foi entregue na Fase B como um serviço tool-based (`OtpService` + tools `otp_challenge`/`otp_verify`),
consumido por um sub-fluxo hardcoded no intake. Funciona, mas ficou "parafusado": (1) entrega mockada (item 1,
adiado até termos canais); (2) tuning env-only (deveria ser config-api); (3) textos hardcoded no YAML (sem
editor/i18n); (4) sem reenvio/UX; (5) sem auditoria; (6) sem generalização como step-up reusável; (7) sem
lockout; (8) cobertura de teste parcial.

Em paralelo, o módulo de surveys já decidiu (§16/§17, aprovação B) um **interpretador genérico** (1 skill que
renderiza `menu.options/fields` dinâmicos a partir de um **form JSON versionado** na evaluation-api) + um
**editor (form-builder)** no platform-ui. Essas peças são genéricas, não específicas de survey.

A discussão convergiu para três decisões complementares, ancoradas em **dois consumidores concretos** (prompts
de OTP + surveys), o que evita generalização especulativa.

## Decisões

### D1 — OTP é um workflow negocial que delega I/O de canal a especialistas

O OTP passa a ser modelado com o padrão **`delegate-workflow-io`** (Arc 19), não como serviço avulso:

- **Workflow negocial (channel-abstract):** gerar → enviar → coletar retorno → verificar. Não conhece o canal.
  Exposto como **sub-workflow/step-up reusável**: qualquer fluxo sensível (pagamento, revelar dado mascarado,
  retomada cross-canal de `customer_resumable`) **delega** e recebe `{verified}` de volta. (Resolve item 6.)
- **Especialista de canal (Tier 3):** dono do **canal e da coleta da resposta**. Decide/coleta qual canal
  (escolha do usuário, via menu — não escalonamento automático), dispara o envio, apresenta "digite o código
  que você recebeu", coleta o que o **cliente digitou** e devolve ao workflow. "Enviar por outro canal" =
  escolha do usuário coletada no diálogo + re-disparo pelo fluxo (não lógica embutida no interpretador).
  A entrega real (item 1) vira o **`collect`/outbound** do especialista — unifica com a máquina de canais.
- **Caminho leve:** quando a âncora == canal da sessão (ex.: cliente no WhatsApp, código no WhatsApp), o
  workflow pode dispensar o especialista/sessão-filho e fazer o tool call direto. O caminho pesado
  (especialista + sessão-filho Arc 19) se justifica no **cross-canal** — que é o caso de posse de verdade.

### D2 — Primitivo de diálogo genérico (conteúdo), compartilhado survey + OTP

Construir **genérico primeiro** o interpretador/editor previsto para survey, e depois **atualizar o spec de
survey para consumi-lo**:

- Reenquadrar "form-builder" como **script/dialog-builder**: além de perguntas, suporta **statements sem
  resposta** (notify/fala) e **retry na mesma superfície** (re-perguntar) — é isso que o faz servir OTP e
  diálogos em geral, não só surveys.
- Form/dialog **JSON versionado** (draft/published), i18n, `form_get` genérico (em vez de `survey_form_get`),
  home neutra (não `/config/surveys` fechado). O editor de survey vira uma **visão** sobre essa biblioteca.
- Reusa as 2 extensões de engine já previstas (§17.3): `$.config.*` (slot → flow) + `menu.options/fields`
  dinâmicos (array | ref).

### D3 — Tela de OTP em Configurations (comportamento + bindings, não texto)

Config estruturada (config-api, namespace `identity`/`otp`): TTL, tentativas, rate-limit, dígitos, canais
aceitos para posse, e os **bindings** (qual `form_id` de diálogo para os prompts, qual `template_id` de
entrega). O **texto** vive na biblioteca de diálogo (D2); a tela referencia ids. (Resolve item 2; complementar,
não concorrente ao editor.)

## Invariantes / as quatro costuras

A separação que mantém tudo limpo (e segura):

| Camada | Dono | O quê |
|---|---|---|
| **Conteúdo** | biblioteca de diálogo (D2) | texto, opções, i18n — **dado** versionado, não lógica |
| **Controle** | workflow/skill | branches, chamadas de tool, orquestração — **nunca** no JSON (senão vira linguagem em JSON) |
| **Canal** | especialista (Tier 3) | seleção + coleta da resposta na superfície do canal |
| **Segredo** | serviço confiável (`OtpService`/channel-gateway) | gerar + **enviar direto ao canal** + verificar + rate-limit |

**Costura inegociável do OTP:** o **código nunca passa pela mão de um agente.** Gerar/enviar/verificar ficam no
serviço confiável (envio direto ao canal da âncora, não via mensagem de agente). O especialista — que pode ser
**IA** — só orquestra canal e carrega o que o **cliente** digitou; nunca vê o código. Violar isso mata o OTP.

## Reuso pelo survey — o padrão geral "interação scriptada delegada"

OTP e survey são **duas instâncias do mesmo padrão**: um *workflow negocial* que **delega a um especialista
channel-aware** a execução de um *diálogo versionado* e a *coleta de input real do cliente*, retornando um
resultado estruturado. Diferem em pouca coisa:

| | OTP | Survey |
|---|---|---|
| Workflow negocial | gerar/enviar/coletar/verificar | selecionar instrumento/apresentar/coletar/registrar |
| Especialista de canal | coleta o código digitado | coleta as respostas (o `skill_survey_runner_v1` já é isso) |
| Diálogo (conteúdo) | prompts (form JSON) | perguntas do instrumento (form JSON) — **mesmo editor** |
| Resultado → | `otp_verify` (serviço confiável) | `survey_record` (trilha confiável) |
| Costura extra | segredo (código) | — (mas há a análoga: resposta é do cliente, não fabricada por IA) |

Consequências do reuso:

- O **`skill_survey_runner_v1`** e o **especialista de coleta do OTP** convergem para **um "dialog-runner"
  genérico** (Tier 3): channel-aware, renderiza um `form_id` via o interpretador, coleta input do cliente,
  devolve as respostas cruas. Parametrizado por `(form_id, política de canal, sink do resultado)`.
- O **retorno outbound do survey (§19)** — que já usa `collect`/Arc 19 — passa a ser o **mesmo** mecanismo de
  "especialista contata no canal" do OTP. Formaliza o survey como "workflow negocial delega diálogo ao
  especialista", alinhando com o OTP.
- **Mantém-se distinto** o *result-handling* (verify × record) e o segredo do OTP — o dialog-runner só coleta
  e devolve input cru; o que se faz com ele fica no workflow/tool de cada domínio. Não unificar isso à força
  (senão o runner vira um `if` gigante).

Ou seja: **build OTP assim primeiro estabelece o padrão**; o survey adota (atualiza §17/§19 para consumir o
dialog-runner + primitivo). Ativos compartilhados: interpretador/editor de diálogo (conteúdo), dialog-runner
specialist (canal+coleta), orquestração delegate-workflow-io (controle). Ativos distintos: verify × record,
e a costura de segredo do OTP.

## Consequências

- OTP vira **capacidade de primeira classe** reusável; entrega (item 1) e generalização (item 6) saem nativas.
- Survey e OTP param de ter runners bespoke → um dialog-runner + uma biblioteca de forms.
- **Acopla** o texto-config do OTP ao arco de surveys (o interpretador é decidido mas não construído). Mitiga:
  construir o primitivo genérico ancorado nos 2 consumidores, não survey-branded.
- Migração faseável: o OTP tool-based atual segue funcionando durante a transição.

## Fora de escopo / adiado

Entrega real do OTP (item 1 — provedor SMS/e-mail, canais); auditoria (item 5, Kafka/`mcp.audit`); lockout
crescente (item 7); merge/external_refs de identidade (Fase C). Estes seguem trilhas próprias — não são
resolvidos por D1–D3.

## Alternativas descartadas

- **OTP como serviço tool-based permanente** (o MVP atual): simples, mas não reusa a máquina de canais (entrega
  fica hack), não generaliza como step-up, e duplica UX de texto. Mantido só como ponte de migração.
- **Interpretador carregando controle/tool-calls no JSON** (form como programa): reinventa o engine de flow em
  JSON. Rejeitado — conteúdo é dado, controle fica no skill (costura D2/invariantes).
- **Unificar result-handling de OTP e survey num runner só:** vira condicional gigante e mistura a costura de
  segredo. Rejeitado — runner coleta cru; domínio trata o resultado.
