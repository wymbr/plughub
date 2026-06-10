# Fluxo de Intake — Nível (c) · Especificação

> **Contexto:** *Business in Any Media*, framework de loja. O **intake** é a skill publicada no **pool de entrada** (nível c, perfil `agent`) que recebe todo contato: saúda, **resolve a identidade**, **oferece retomada** de fluxo pendente e, se não houver, **roteia para a intenção** (vendas/suporte) — sempre delegando **por pool**.
> **Prior art (versão atual):** `agente_portabilidade_intake_v1.yaml` (coleta → `pending_workflow_get` → oferta → `delegate`/`workflow_resume` vs. novo) e `agente_auth_ia_v1.yaml` (gate de verificação: masked input + `invoke validate_pin` no MCP, só o veredito persiste). Este doc generaliza os dois com as decisões do resolvedor de identidade e do contrato de delegação.
> **Status:** especificação. **Data:** Junho 2026.

---

## 1. O que o intake faz (e o que NÃO faz)

**Faz:** saudação → resolução de identidade (Lookup 1) → checagem de pendência (Lookup 2) → oferta de retomada (policy) → retomada (`delegate`+`resume`) **ou** roteamento da intenção (`delegate` por pool). Aciona o **gate de identificação** quando preciso reter algo cross-canal.

**Não faz:** lógica de negócio (isso é (a), no pool de destino), render por canal (adapter), nem autenticação/verificação por conta própria (sempre via retaguarda do contratante).

O intake é **channel-aware** só no ponto de origem das âncoras: em canais com ANI (WhatsApp/voz/SMS) o handle já é âncora; em webchat anônimo, vira prospect efêmero e o gate pede âncora quando necessário.

---

## 2. Intake-alvo (YAML anotado)

Tools: `pending_workflow_get` **existe** (estendido p/ `customer_id`); `customer_resolve`/`identity_verify` **propostos** (resolver §13); `delegate`/`menu`/`notify`/`choice`/`complete` **existem**.

```yaml
id: skill_intake_loja_v1
name: "Intake — Business in Any Media (loja)"
classification: { type: orchestrator, domain: loja }
entry: saudacao

steps:
  - id: saudacao
    type: notify
    message: "Oi! Bem-vindo 👋 Já te ajudo."
    on_success: resolver_identidade
    on_failure: resolver_identidade

  # ── Lookup 1 — resolve/provisiona customer_id a partir da ORIGEM do canal ──
  # O channel-gateway (genérico) entrega session.origin_identity (ANI/from/ids).
  # A IDENTIFICAÇÃO é função deste fluxo. ANI → âncora forte; webchat anônimo →
  # provision=true cria prospect efêmero (Redis).
  - id: resolver_identidade
    type: invoke
    tool: customer_resolve            # PROPOSTO (resolver §13.1/§13.6)
    input:
      tenant_id:       "$.tenant_id"
      origin_identity: "$.session.origin_identity"   # envelope do channel-gateway
      provision:       true
    output_as: cliente                # { customer_id, status, confidence }
    on_success: checar_pendencia
    on_failure: checar_pendencia

  # ── Lookup 2 — pendências do cliente (retomada cross-canal) ──
  - id: checar_pendencia
    type: invoke
    tool: pending_workflow_get        # EXISTE (estendido: por customer_id)
    input:
      tenant_id:   "$.tenant_id"
      customer_id: "$.pipeline_state.cliente.customer_id"
    output_as: pendencia              # { found, count, pendings[] }
    on_success: avaliar_pendencia
    on_failure: rotear_intencao       # fallback: segue sem retomada

  - id: avaliar_pendencia
    type: choice
    conditions:
      - field: "$.pipeline_state.pendencia.found"
        operator: eq
        value: true
        next: oferta_retomada
    default: rotear_intencao

  # ── Oferta de retomada (resume_policy = offer) ──
  - id: oferta_retomada
    type: menu
    prompt: "Vi que você tem **{{$.pipeline_state.pendencia.pendings.0.intent}}** em aberto. Quer continuar de onde parou?"
    interaction: button
    options:
      - { id: continuar, label: "✅ Continuar" }
      - { id: novo,      label: "🆕 Começar outro" }
    output_as: decisao
    timeout_s: 300
    on_success: processar_decisao
    on_timeout: rotear_intencao
    on_disconnect: finalizar

  - id: processar_decisao
    type: choice
    conditions:
      - field: "$.pipeline_state.decisao"
        operator: eq
        value: continuar
        next: retomar
    default: rotear_intencao          # "novo" → roteia intenção nova

  # ── Retomada — delegate ao POOL dono do pendente, com o resume_token ──
  - id: retomar
    type: delegate
    pool: "{{$.pipeline_state.pendencia.pendings.0.pool}}"
    context:
      workflow_resume_token: "$.pipeline_state.pendencia.pendings.0.resume_token"
      customer_present: "true"
    timeout_hours: 1
    on_resume:  { next: finalizar }
    on_reject:  { next: finalizar }
    on_timeout: { next: finalizar }

  # ── Sem pendência / "novo" — roteia a intenção, DELEGANDO POR POOL ──
  # A skill publicada em loja_vendas_io conduz a loja (catálogo/cesta/checkout).
  - id: rotear_intencao
    type: delegate
    pool: loja_vendas_io
    customer_resumable: true          # registra pendência por customer_id
    resume_policy: offer
    requires_identity:                # GATE: âncora durável p/ reter cross-canal
      min_anchor: [phone, email, cpf, princ]
      on_missing: coletar_identidade
    on_resume:  { next: finalizar }
    on_reject:  { next: finalizar }
    on_timeout: { next: finalizar }

  # ── GATE de identificação (on_missing) — coleta + verifica na RETAGUARDA ──
  # Padrão do agente_auth_ia_v1: a plataforma captura e repassa; quem decide é o backend.
  - id: coletar_identidade
    type: menu
    prompt: "Pra guardar seu progresso e te reconhecer em qualquer canal, me confirma seu WhatsApp ou CPF:"
    interaction: text
    output_as: ancora
    timeout_s: 180
    on_success: verificar_ancora
    on_timeout: rotear_intencao_degradado    # sem âncora → segue, só intra-canal

  - id: verificar_ancora
    type: invoke
    tool: identity_verify             # PROPOSTO — retaguarda do contratante decide
    input:
      tenant_id:   "$.tenant_id"
      customer_id: "$.pipeline_state.cliente.customer_id"
      captured:    "$.pipeline_state.ancora"
    output_as: verificacao            # { ok, external_id, confidence } → (b) faz write-back/promove
    on_success: rotear_intencao       # âncora válida → requires_identity satisfeito
    on_failure: rotear_intencao_degradado

  # Reentrada degradada: identidade ainda fraca → segue SEM customer_resumable
  # (retomada só intra-canal). Evita loop no gate (§3).
  - id: rotear_intencao_degradado
    type: delegate
    pool: loja_vendas_io
    customer_resumable: false
    on_resume:  { next: finalizar }
    on_reject:  { next: finalizar }
    on_timeout: { next: finalizar }

  - id: finalizar
    type: complete
    outcome: resolved
```

> ⚠️ **Nota sobre o exemplo:** o campo `requires_identity` e o step `rotear_intencao_degradado` acima são *ilustrativos*. Pela decisão (§3/§6.3), o gate **não é campo do `delegate`** — a forma canônica wira o gate como steps do próprio fluxo (`choice` de identidade → `coletar_identidade`/`verificar_ancora` → `delegate` com ou sem `customer_resumable`), e o pool do `rotear_intencao` pode ser fixo **ou** dinâmico via triagem (§6.4). Mantido assim só para legibilidade.

---

## 3. Gate de identificação — é PADRÃO DE FLUXO, não primitiva de engine

O gate é **lógica do fluxo**, não um campo do `delegate`. O fluxo o expressa wirando `menu`(coleta) → `invoke identity_verify` → `choice` → e então delega de um jeito ou de outro: com `customer_resumable: true` se a identidade ficou forte, ou **sem** (degradado, só intra-canal) se não. O **anti-loop é a própria estrutura do fluxo** — o ramo de falha não volta ao gate (por isso os dois `delegate` no YAML da §2 são legítimos: é o fluxo dizendo "com identidade vs. sem").

Primitiva de plataforma aqui são só `customer_resumable`/`resume_policy` (parametrizam o registro da pendência por (b)) + as tools (`identity_verify`, masked transaction, customer base). Para nenhum fluxo reinventar o anti-loop, a plataforma oferece um **snippet/sub-skill de gate reusável** — opcional, não imposto.

> O YAML da §2 é baseline ilustrativa. Diversidade de fluxo é esperada: cada intake é customizado.

---

## 4. Delta vs. o intake atual (`agente_portabilidade_intake_v1`)

| Hoje (portabilidade) | Alvo (intake de loja) |
|---|---|
| `pending_workflow_get(contact_identifier)` — âncora única em claro | `customer_resolve` (Lookup 1) → `pending_workflow_get(customer_id)` (Lookup 2) — multi-âncora, hasheada |
| Sempre coleta `contact_identifier` (webchat) | Resolve do handle (ANI) **sem perguntar**; só pede no gate quando precisa reter e a identidade é fraca |
| Pendência keyed pelo handle | Pendência keyed por `customer_id` nativo |
| Pool de destino fixo no YAML | Retomada usa o `pool` do **próprio pendente**; intenção nova delega ao pool de vendas |
| Sem verificação na retaguarda | Gate `identity_verify` (capture-and-relay; só veredito persiste) + write-back contínuo |
| Sem provisionamento de prospect | Prospect efêmero no Redis; promove ao PG no gatilho concreto |

O **esqueleto é o mesmo** (coleta → checa pendência → oferta → resume/novo) — o que muda é a identidade virar `customer_id` resolvido/provisionado e o gate ser condicional e verificado pela retaguarda.

---

## 5. Onde isto roda e reuso

- É a skill publicada no **pool de entrada** (ex.: `loja_intake`), perfil `agent`. Deploy-driven: troca de versão = um deploy no pool, sem tocar nos fluxos de negócio (a).
- **Reusável** entre loja e suporte: o que muda é o pool de destino do `rotear_intencao` (vendas vs. triagem) e o copy — configurável.
- O **agente de fila** (role=queue) pode compartilhar a oferta de retomada e os commerce-cards (oferta enquanto espera).
- `customer_present: "true"` no `delegate` de retomada sinaliza que o cliente está conectado neste reconnect (o specialist faz I/O ao vivo) — padrão já usado no exemplo de portabilidade.

---

## 6. Decisões (fechadas)

1. ~~`session.channel_anchors`~~ **Resolvido:** o channel-gateway (genérico) expõe `session.origin_identity` (envelope padronizado: ANI/`from`/ids passados) e roteia; a **identificação é função deste fluxo** (resolver-spec §4.4, §2.9). Confirmar só o nome do tag (`session.origin_identity`).
> **Lente (decisiva):** separar **primitiva de plataforma** (decidida uma vez, mínima, reusável: tools, schema da base, campos `customer_resumable`/`resume_policy` do `delegate`) de **escolha de autoria de fluxo** (diversa: pool fixo vs. por intenção, quais tools chamar, como wirar o gate, copy). Fluxos de entrada/agentes/workflows são **sempre customizados** — o diverso fica no fluxo, não vira política de engine. Por isso 2 e 4 deixam de ser "decisões".

2. **Não é decisão de plataforma:** as duas tools existem; **cada fluxo escolhe** (combinada `pending_workflow_get(origin_identity, provision)` p/ economizar round-trip; `customer_resolve` separada p/ identidade-sem-pendência). Diversidade esperada.
3. **Padrão de fluxo, não primitiva de engine (§3):** o gate é wirado no fluxo (não é campo do `delegate`). Plataforma dá os blocos seguros (tools + snippet de gate reusável); o fluxo compõe; anti-loop = estrutura do fluxo.
4. **Escolha de fluxo:** valem os dois — **pool fixo** (intenção única) **ou** triagem `reason` → **pool dinâmico** (multi-intenção, padrão `agente_triagem_v2`). Cada fluxo customizado decide; a plataforma suporta ambos.
