# Journey J4c — Survey outbound como contato via `collect` (spec de implementação)

**Status:** Implementado (2026-07-13). Motiva a fatia J4c (`TODO.md`).
**ADR base:** `docs/adr/adr-outbound-survey-as-collect-contact.md`
**Relacionado:** Arc 19 (`collect`/suspend-resume, modelo unificado de sessão),
Resolvedor de Identidade (Fase A/B — `secondary_keys` + `verification_class`),
Journey J4b (`on_process_end`), `docs/arcos/customer-surveys.md` §19.

> **Nomeação dos skills (rename 2026-07-14).** Os três skills de survey são nomeados pelo
> **papel**, não pelo grão — grão e papel são eixos ortogonais, e nomear pelo grão
> duplicaria a cadeia inteira (3 papéis × N grãos) além de deixar ambíguo qual dos dois
> skills de grão *journey* é o gatilho e qual é o workflow:
>
> | skill | papel | era |
> |---|---|---|
> | `skill_survey_trigger_v1` | **gatilho** — consome o hook de fim, decide *se* pesquisa, dispara o workflow | `skill_journey_survey_v1` |
> | `skill_survey_outbound_v1` | **workflow** — faz o `collect`, suspende esperando o clique | `skill_survey_journey_v1` |
> | `skill_survey_runner_v1` | **runner** — renderiza o DialogForm ao vivo, grava o sinal, retoma o workflow | `skill_survey_collect_v1` |
>
> O **grão** (journey/session/segment) é **parâmetro**, não família de skills — ver S2 no
> `TODO.md`. Enquanto ele não for config, o runner ainda carrega `grain: "journey"`
> hardcoded (o único ponto não-genérico dele).

---

## 1. Objetivo

Fazer a resposta do survey de fim-de-processo (N3) ser um **contato de primeira classe**
(sessão-filho, **membro N1 da journey**), em vez de um sinal solto. Reusa o mecanismo
canônico de outbound assíncrono da plataforma — **`collect` / suspend-resume (Arc 19)** —
mantendo o processo (N3) **cego ao canal** e a escolha de canal (N2) num **resolvedor
único, compartilhado e cego ao processo**.

---

## 2. Modelo de 3 camadas (responsabilidades)

| Camada | Papel ("…") | Componente concreto |
|---|---|---|
| **N3** | processo/journey — "o porquê" (pesquisar o cliente sobre o processo) | workflow de survey (skill-flow). Faz `collect`, **suspende**. **Channel-agnostic.** |
| **N2** | negociação de canal + entrega — "como alcançar" (SMS/e-mail/web) | handler do `persistCollect` na channel-gateway. **Resolvedor único, cego ao processo.** Cria a sessão-filho N1. |
| **N1** | contato — "o que aconteceu" (cliente abriu o link, respondeu) | sessão-filho self-served. Herda `root_session_id` → membro da journey. |

**Invariante-mãe:** N3 nunca conhece o canal; N2 nunca conhece o processo.

---

## 3. Primitivo: `collect`, não `delegate`

Os dois criam sessão-filho herdando `root`, mas a semântica de alvo é **oposta**:

| | `persistDelegate` (`handle_delegate`) | `persistCollect` (a wirar) |
|---|---|---|
| Alvo | **pool fixo** (`pool_id` do chamador) | **cliente**, canal **negociado** |
| Escolha de canal | nenhuma (o pool define) | **é o ponto da escolha (N2)** |
| Uso correto | "delegue a ESTE runner" (reconnect, OTP intake) | "alcance o cliente, você decide o canal" |

`persistDelegate` obrigaria N3 a saber o pool/canal → vazamento. **J4c usa `collect`.**
Reaproveita-se apenas o **mecanismo de criação de sessão-filho** do `handle_delegate`
(gerar `child_session_id`, herdar `root`, semear ContextStore), **não** sua semântica de
alvo fixo.

---

## 4. Contrato declarativo: `channel_policy` no `collect`

N3 declara **intenção**, nunca um canal resolvido. O `CollectStep` ganha (schema
`@plughub/schemas/skill.ts`):

```ts
channel_policy?: {
  allowed_channels?: string[]    // whitelist declarativa (ex.: ["sms","email","web"])
  preferred_order?:  string[]    // ordem de preferência (ex.: ["sms","email"])
  exclude?:          string[]    // blacklist
  urgency?:          "low" | "normal" | "high"
}
```

- N3 **não** seta o campo `channel` fixo do `CollectStep` para outbound-ao-cliente
  (isso seria N3 escolhendo o canal = vazamento). O `channel` fixo sobrevive só para
  transporte fixo de verdade (ex.: collect interno a um sistema).
- Razão negocial de N3 (VIP, campanha só-e-mail, urgência) entra **como dados** neste
  policy — a detecção (ex.: "é VIP") fica em N3; só a **preferência resolvida** cruza.

---

## 5. N2 — resolvedor único, cego ao processo

Um só componente (o handler do `persistCollect`), reusado por **todo** `collect`:

```
select_channel(
  reachable  = IdentityResolver.reachable_channels(customer_id),   # cadastro/verificação
  consented  = ConsentFilter(customer_id, tenant)                  # slot plugável (vazio v1)
  policy     = TenantChannelPolicy(tenant)                         # slot plugável (vazio v1)
  prefs      = collect.channel_policy                              # declarativo de N3
) -> channel
```

- **Inputs cross-cutting (próprios de N2):** alcançabilidade (Resolvedor de Identidade),
  consentimento (compliance), política do tenant (custo/fallback/quiet-hours).
- **Input de N3:** apenas `channel_policy` declarativo.
- **Proibido em N2:** qualquer `if skill_id == … / campaign == …`. N2 importa só
  `CollectChannelPolicy` + Resolvedor + política. **Zero** dependência de identidade de
  processo.

### Guard de invariante

Check no estilo `infra/check_config_invariants.py`: falha o CI se o módulo do resolvedor
N2 referenciar `skill_id`/`campaign_id`/identidade de processo. O vazamento é pego no CI.

---

## 6. N1 — sessão-filho, criação LAZY no engajamento (decisão 2026-07-10)

**Separar o assíncrono (esperar o cliente) do síncrono (o survey em si).** Isso resolve
duas coisas de uma vez: a regra de perfil (`menu`/render é *agent*; `suspend`/`collect` é
*workflow* — **nunca no mesmo skill**) e o custo de capacidade do web assíncrono.

- **(1) `collect` = convite + espera, LAZY.** N3 (workflow) faz `collect`; o handler N2
  **entrega o link + guarda o pending** (`{t}:collect:{collect_token}` com caller/step,
  root, pool de survey, form_id, canal) e **suspende**. **Zero sessão, zero recurso, zero
  metering** enquanto espera. Sem clique até o timeout → só a chave pending expira.
- **(2) clique com token válido = inbound PADRÃO.** Abrir `/survey/{collect_token}` cria
  uma **sessão inbound normal** (o cliente **está presente**), roteada ao pool de survey →
  Routing admite (cota + `max_concurrent_sessions`) → Core abre (metering) — **limites e
  metering só no engajamento real**, não no convite. Herda `root` (→ membro N1). O
  `dialog_runner` (agente único, DialogForm por config) renderiza **ao vivo** (síncrono,
  cliente presente — o `menu` funciona porque não há espera longa).
- **(3) fim do survey → resume.** `session_closed` no submit → **sinal grain=journey no
  close** (keyed na raiz) → `collect.responded` → N3 **resume** (decisão: collect resolve
  **no fim**, N3 sabe se respondeu/abandonou).

Por que isso preserva "agente único que interpreta o DialogForm por config": ao mover a
espera assíncrona **inteira** para o `collect`/suspend, o survey vira **síncrono** (cliente
presente pós-clique) → o `dialog_runner` **pode** ser o interpretador ao vivo. O que
quebrava o princípio era o assíncrono; ele saiu da parte que renderiza.

**"Delega" ≠ step `delegate()`.** O clique cria um **inbound** (contato do cliente, sessão
própria, membro N1) — não um especialista de conferência na sessão do N3.

**Limites e segmentação = pool** (invariante "Routing é árbitro único"): a sessão inbound
passa pela admissão normal; `max_concurrent_sessions` do pool de survey = botão de volume;
metering `sessions` do Core; relatórios filtram por pool. **Sem canal-classe novo, sem
carve-out de metering.**

> **Correções (2026-07-10):** (a) "sessão publicada direto em `conversations.events` sem
> routing" — **descartada** (dropava limites + feria o árbitro único). (b) criação **eager**
> da sessão no `collect` — **descartada** por "runner renderiza E suspende" ser proibido
> (perfil) e por segurar capacidade no limbo assíncrono. Modelo final = **lazy + clique cria
> o inbound**.

---

## 7. Superfície = adapter do `collect`

A página `/survey/{collect_token}` é a **superfície do canal web**. No **primeiro open**
(clique), cria o **inbound padrão** (routed → pool de survey; herda `root`). O
`dialog_runner` renderiza o DialogForm (`form_id` do pending). No **submit**:
1. valida/coleta respostas,
2. **fecha a sessão inbound** (`session_closed`),
3. emite o sinal grain=journey (mesma trilha `session.signals`),
4. publica `collect.responded` (payload = respostas) → resume de N3.

O N3 declara a `interaction` (o DialogForm/menu); **cada adapter renderiza do seu jeito**
(web = página; chat = inline) e devolve via `collect.responded`. N3 recebe igual.

---

## 8. `survey_link_create` = legado / anônimo

Mantido **só** para o caso **sem raiz/cliente conhecido** (survey não-solicitado, sem
journey a que se atar → sinal solto, sem sessão). O survey de processo (N3, com raiz) usa
`collect`.

---

## 9. Fatiamento

| Slice | Entrega | Escopo demo |
|---|---|---|
| **J4c-1** | Contrato: `channel_policy` no `CollectStep` (schemas); `persistCollect` wired no skill-flow-service (hoje só `persistDelegate`). | — |
| **J4c-2** | **N2**: handler `persistCollect` na channel-gateway = resolvedor único. Cria sessão-filho (herda root, carimba pool), negocia canal (alcançabilidade via Resolvedor de Identidade + `channel_policy`; consentimento/política = slots vazios), entrega. Guard de invariante. | web + entrega mock; SMS/e-mail = config futura |
| **J4c-3** | **N1 + superfície**: `/survey/{token}` atada ao `child_session_id`; submit fecha a sessão-filho + emite sinal no close + publica `collect.responded`. | — |
| **J4c-4** | **Resume + agente**: `collect.responded` → resume de N3 via channel-gateway (como `handle_resume`); o survey migra de `survey_link_create` p/ `collect` (com `channel_policy`) — as-built, o `collect` ficou no **workflow** (`skill_survey_outbound_v1`), porque o hook agent não pode suspender; `survey_link_create` = legado/anônimo. | — |
| **J4c-5** | **E2E**: trigger → processo → hook → survey aparece como **sessão-membro N1** no drill da journey, com NPS; provar N3 channel-agnostic (nenhum canal no skill). | — |

---

## 10. Invariantes (nunca violar)

- **N3 é channel-agnostic** — o skill de survey nunca nomeia canal; só `channel_policy`
  declarativo. Setar `channel` fixo em collect-ao-cliente = vazamento.
- **N2 é cego ao processo** — resolvedor único, sem ramo por `skill_id`/`campaign_id`
  (guard de CI).
- **Escolha de canal fora de N3** — política de canal é concern cross-cutting reutilizável,
  nunca no skill.
- **Sessão-filho herda `root`** — membro N1 da journey por construção (não derivar de novo).
- **Sinal grain=journey emitido no close da sessão-filho**, keyed na raiz canônica.
- **Segmentação/billing por pool** — sem canal-classe novo, sem carve-out de metering.

---

## 11. Escopo honesto (o que a demo NÃO cobre)

- Provedor real de SMS/e-mail (entrega é mock/dev); multi-canal real = fase posterior por
  config, sem tocar N3.
- `ConsentFilter` e `TenantChannelPolicy` = **slots plugáveis vazios** na v1 (a forma já é
  a certa; os stores entram depois).
- Renderização inline em canais de chat (WhatsApp/webchat) = futura; v1 valida a abstração
  com o canal web.
