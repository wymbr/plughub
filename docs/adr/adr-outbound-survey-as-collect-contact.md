# ADR: Survey outbound = contato via `collect` (canal `survey`/`web`), não sinal solto

**Status:** Proposto (2026-07-10). Motiva a fatia **J4c** (`TODO.md` § Journey).
O veículo web atual (`survey_link_create` + página tokenizada + `session.signals`) fica
como **legado / caminho do sinal anônimo** até J4c.
**Data:** 2026-07-10
**Componentes:** `packages/channel-gateway` (adapter de canal + `survey_web.py`),
`packages/skill-flow-engine` (step `collect`), `packages/mcp-server-plughub`
(`survey_link_create`), `packages/orchestrator-bridge` (hook `on_process_end`),
`packages/analytics-api` (`session.signals`, `/reports/journeys`)
**Relacionado:** Arc 19 (modelo unificado de sessão; `collect`/suspend-resume),
Journey J4b (hook `on_process_end` + `skill_journey_survey_v1`),
`docs/arcos/customer-surveys.md` §19 (retorno outbound via collect),
`docs/product/journey-3-niveis-implementation-spec.md`

---

## Contexto

O J4b entregou o survey de fim-de-**processo** (N3) pela via **outbound web**: no
`on_process_end`, o agente `skill_journey_survey_v1` chama `survey_link_create`, que
congela o DialogForm publicado num token e expõe a página pública `/survey/{token}`.
O `submit` grava um `session.signal` grain=`journey`, chaveado na **raiz canônica** da
journey. Isso funciona e alimenta a coluna NPS do relatório de Processos.

**Porém**, essa via **não abre sessão/contato**: a resposta do cliente é registrada como
sinal solto. No drill da journey ela **não aparece como contato-membro** — só como número
agregado (NPS). Isso levanta uma inconsistência de modelo.

### Diagnóstico da raiz

O survey web outbound **é um contato outbound**: uma interação separada, posterior,
iniciada pela plataforma (entrega do link) e respondida pelo cliente, com conteúdo
próprio (perguntas + respostas), timestamp e canal. Pelo **invariante fundacional** da
plataforma — "todo contato é uma sala de conferência; o Core cria uma sessão a cada novo
contato" — isso deveria ser uma **sessão**.

A plataforma **já tem** a máquina para materializar outbound assíncrono: o par
**webhook-trigger** (materializa o processo/N3 como sessão headless) + **`collect`**
(Arc 19: o workflow suspende, um adapter de canal *negociado por capabilities* estabelece
o **contato-filho**, o cliente responde — possivelmente horas depois, coberto por
suspend/resume+timeout — e o resultado volta). O contato-filho **herda `root_session_id`**
→ é **membro N1 da journey por construção**, com transcrição, passando por
stream/masking/audit.

Os outros dois veículos de survey já seguem esse modelo: o **runner** roda como
especialista *dentro* da sessão; o **outbound collect** cria sessão-filho. O **link web**
é o **único** veículo que vira sinal e não contato — a assimetria: *mesmo instrumento,
pertença à journey diferente conforme o veículo*.

O `survey_link_create`+página tokenizada, portanto, é um **bypass** do modelo de contato
(pula `collect`, adapter de canal e a sessão). Não é a spec — o **§19 do spec de surveys
já prescreve** retorno outbound "via `collect`/Arc 19". A implementação divergiu; este ADR
recoloca a implementação na spec.

### Ressalva — o caso legítimo do sinal solto

Survey **verdadeiramente anônimo/não-solicitado** (link no rodapé de um site, sem contato
prévio, sem raiz nem cliente conhecido) **não tem journey** a que pertencer — não há
sessão-membro a criar, e o sinal solto é o modelo certo. Esse caso **sobrevive**; é
distinto do survey de processo (N3), atado a uma raiz conhecida.

---

## Decisão

1. **Canal de primeira classe `survey`/`web`** — adapter na channel-gateway que renderiza
   o DialogForm (a página tokenizada atual vira a *superfície de renderização do adapter*,
   não um caminho paralelo).
2. **Survey outbound de processo usa `collect`** — o agente de survey (`on_process_end` ou
   equivalente) faz `collect` targetando o cliente no canal `survey`/`web`. O `collect`
   cria o **contato-filho** que herda `root_session_id` → **membro N1 da journey**, com
   transcrição própria; o **sinal grain=journey é emitido no fechamento** do contato
   (mantém a coluna NPS do relatório). Signal e sessão coexistem.
3. **Assíncrono via suspend/resume** — o "manda e responde depois (ou nunca)" é o caso
   canônico do `collect`/`suspend` com timeout do Arc 19; não é motivo para fugir do
   modelo de sessão.
4. **`survey_link_create` = legado / sinal anônimo** — mantido para o caso sem raiz/cliente
   conhecido (feedback não-solicitado), onde não há journey a que se atar.
5. **Veículo configurável por survey** — leve (sinal solto) vs. contato pleno
   (sessão-membro via collect). Default do survey de processo (N3, com raiz) = **contato**.

---

## Consequências

- **Positivas:** journey completa (o survey aparece como contato N1, com transcrição);
  governança uniforme (stream/masking/audit); fim da assimetria entre veículos; alinhamento
  com o §19 já escrito; reuso do `collect`/suspend em vez de caminho bespoke.
- **A decidir (impacto):**
  - **Metering/billing** — um survey outbound passa a contar como **contato/sessão**
    (dimensões de uso, TMA/SLA). Para outbound isso é **correto** (a plataforma de fato
    fez o contato), mas é mudança de contagem → decisão de produto, não flip silencioso.
  - **Estatística operacional** — formulário self-service passa a contar como "contato" nas
    vistas de Sessions/Pools. A config por-survey (leve vs. contato) endereça quem não quer.
- **Migração:** J4b continua válido (o hook `on_process_end` é genérico e permanece). J4c
  troca o *veículo* do survey de processo de `survey_link_create` para `collect` no canal
  `survey`/`web`; o relatório N3 não muda (o sinal grain=journey segue sendo a fonte do NPS).

---

## Alternativas consideradas

- **Manter só o sinal (status quo).** Rejeitado para o survey de processo: contraria o
  invariante "todo contato é sessão", cria a assimetria entre veículos e subconta os
  touchpoints da journey. Sobrevive apenas para o survey anônimo.
- **`submit` abre uma "sessão leve" ad-hoc (sem `collect`).** Rejeitado: seria um segundo
  caminho paralelo de criação de sessão, fora do modelo `collect`/adapter que a plataforma
  já usa para todo outbound. Reusar `collect` é a opção sem dívida.
