# ADR — Política de desfecho do pool sem recurso: enfileirar ou recusar

- **Status:** proposto
- **Data:** 2026-08-18
- **Escopo:** desfecho do roteamento quando o pool não tem recurso disponível. **Não** toca o
  gate de admissão, o modelo de capacidade nem o roteamento.
- **Substitui:** o D11 do [`adr-a2a-server-binding.md`](adr-a2a-server-binding.md) (graça de
  espera do caller A2A) — ver D6.

---

## 1. Problema

Hoje um contato que chega a um pool sem recurso disponível tem **dois** desfechos possíveis, e
nenhum deles é *recusar*:

| Configuração do pool | Desfecho |
|---|---|
| **com** `queue_config` | fila atendida (agente de fila entretém, posição/ETA) |
| **sem** `queue_config` | fila muda (`{t}:queue:unadmitted`) |

Para um cliente humano isso está certo: esperar é melhor que ouvir "não". Para um **chamador
programático** — webhook interno, caller A2A, agente externo — está errado por três motivos:

1. Ele não abandona por tédio. Reenvia, de graça, em laço. A fila cresce sem teto natural.
2. Ele frequentemente **prefere** o erro: `503` + `Retry-After` é acionável; ficar pendurado
   numa fila de minutos não é.
3. A fila enche de itens que ninguém quer atender, e o relatório do pool passa a descrever uma
   espera que não é espera de cliente.

Não existe hoje um terceiro modo, e ele não é derivável dos dois existentes: "sem
`queue_config`" já significa *fila muda*, não *recusa*.

## 2. Medição — o que o código faz hoje

> Levantado em 2026-08-18. É o que torna esta decisão pequena: metade do mecanismo já existe.

**O débito de licença de IA é na PORTA, não na alocação.** `routing-engine/…/main.py:256-261`
chama `admission.admit()` **antes** de `router.route()` (`:295`). O docstring de
`admission.py` confirma: *"Roda em TODA requisição de roteamento"*, *"Rejeição só na PORTA"*.

**Consequência:** sessão enfileirada em pool de IA **com** `queue_config` continua debitando
`{t}:admission:kind:ai` durante toda a espera.

**Já existe uma isenção, e só uma.** `_persist_queued_contact` (`main.py:776-778`, `:825-827`)
chama `admission.release()` quando a fila é **muda** — e a fila muda tem uma única origem desde
a fatia 3: **pool sem `queue_config`**.

| Pool de IA | Sessão enfileirada |
|---|---|
| com `queue_config` | **debita** `C_ai` enquanto espera |
| sem `queue_config` | **isenta** de `C_ai` (`admission.release`) |

**A recusa por cota já existe e já não enfileira.** `main.py:269-277`: `decision.admitted ==
False` → `_emit_outage` → `cause="quota"`, e o contato **não** entra em fila. O comentário
registra que o `_try_overflow_enqueue` (overflow para fila muda) foi **removido de propósito**
na fatia 3, porque virou ramo inalcançável quando humano deixou de ser gateado por sessão.

**O drain não faz churn.** `main.py:1388` e `kafka_listener.py:674` consultam
`admission.has_headroom` antes de re-publicar sessão não-admitida.

⇒ **O caminho de recusa-na-porta já está construído e testado.** Falta apenas uma segunda
entrada para ele: a ausência de recurso no pool, hoje sempre tratada como "enfileira".

## 3. Decisões

### D1 — Campo novo no pool, **irmão** de `queue_config`, nunca dentro dele

```ts
on_no_resource: z.enum(["queue", "reject"]).default("queue")
```

Colocar dentro de `queue_config` seria erro de escopo: o caso mais interessante é justamente o
pool **sem** `queue_config`, e um campo que só existe dentro do bloco que o caso não tem é um
campo que o caso não alcança.

O nome diz o fato — *o que fazer quando não há recurso* —, não a intenção (`strict_mode`,
`programmatic`) nem o consumidor (`a2a_policy`). Consumidor não é fato de pool.

### D2 — Default é `queue`

Comportamento atual preservado byte a byte para todo pool existente. Um default `reject`
mudaria silenciosamente pools de clientes reais, e o modo de falha seria **recusar contato de
pessoa** — a mesma classe de defeito que a fatia 3 corrigiu (`shared_full` → outage com humano
ocioso).

### D3 — `reject` recusa **na porta**, pelo caminho que já existe

Simétrico à recusa por cota: `_emit_outage` + `return`, antes de `router.route()`. Não é
"enfileira e desiste depois" — isso seria uma máquina de estado nova, e a fatia 3 removeu a
anterior por bons motivos.

### D4 — `cause` própria, **nunca** reusar `"quota"`

`AdmissionDecision.cause` tem hoje um único valor, `"quota"`, e ele significa *teto contratado
esgotado* — que é o que a demanda reprimida mostra como "Teto contratado". Recusa por ausência
de recurso é fato **diferente**: a licença pode estar sobrando e o pool estar vazio de agentes.

Fundi-las produziria exatamente o defeito que a Postura de Engenharia manda caçar: um valor
plausível ("faltou cota") escondendo outro ("faltou gente"), sem nada ficar vermelho.

Valor novo: `no_resource`.

### D5 — `close_reason` reusa `no_resource`, **se** a medição de P0 permitir

O domínio já tem `no_resource` = *"no agents available and no queue configured"*, que é
literalmente este desfecho. Não inventar valor.

⚠️ **Condição, não suposição.** É preciso medir se a **fila muda que expira** já está gravando
`no_resource` hoje. Se estiver, os dois desfechos ficam indistinguíveis no relatório —
*"recusei na porta"* e *"esperou mudo e o prazo estourou"* viram a mesma linha, e a taxa de
recusa do pool passa a somar duas coisas. Nesse caso, um dos dois precisa de rótulo próprio.
**Isto é a fase P0 e precede o schema.**

### D6 — Substitui o D11 do ADR de A2A

O D11 resolvia isto **dentro do binding A2A**: graça de ~5 s, depois retirada da fila e `503` +
`Retry-After`. Passa a ser desnecessário. Razões:

- **É política de pool, não de protocolo.** Serve a qualquer chamador programático — webhook
  interno, MCP, A2A —, não só ao A2A.
- **Satisfaz o invariante de configuração** *"todo campo de config é editável na UI"*, em vez de
  enterrar a política num adaptador de borda.
- **O binding encolhe de mecanismo para tradução:** `on_no_resource: reject` → `503` +
  `Retry-After` na criação, ou `TASK_STATE_REJECTED`, que a spec A2A autoriza explicitamente
  (*"may be done during initial task creation"*).

A graça de 5 s some junto — era um relógio inventado para adiar uma decisão que agora é
declarada. Os **modos de presença** do D11 (blocking × `message/stream` × `returnImmediately`)
**sobrevivem inalterados**: eles governam quanto tempo o caller espera na fila quando
`on_no_resource: queue`, que continua sendo a configuração de um pool que serve gente.

### D7 — Isto **não** é reserva de capacidade por pool

Registro explícito, porque a semelhança superficial é perigosa. `{t}:admission:reserved:{pool}`,
`{t}:admission:shared`, `{t}:admission:member:{sid}` e a coluna `pools.session_reservation`
foram removidos na fatia 3 (migration `20260802000000_drop_pool_session_reservation`) e estão
na lista de **não reviver**, porque fatiavam por pool um recurso que é do RECURSO.

`on_no_resource` é **política de desfecho**, não fatia de capacidade. Não cria balde, não conta
sessão, não tem aritmética. Quem decide se há recurso continua sendo o routing; este campo só
decide o que fazer com o "não".

### D8 — A sessão já nasceu, e isso é dito, não escondido

A borda cria a sessão antes do routing. `reject` **fecha** com `no_resource`; não impede a
criação. O contato existiu, contou em `sessions` e aparece na analytics — igual ao outage por
cota hoje.

O campo resolve **fila indesejada**, que é o problema declarado. Não resolve inflação de
contagem por chamador em laço; para isso o instrumento é `Retry-After` respeitado pelo caller,
e — se um dia for necessário — rate limit na borda, que é outro escopo.

## 4. O que **não** muda

`AdmissionController` e o gate `{t}:admission:kind:ai ≤ {t}:quota:capacity:ai_agent` ·
modelo de capacidade (capacidade é do recurso) · `deployed_max_concurrent_sessions` como
provisionamento de instâncias · algoritmo de scoring e alocação · `ChannelSchema` ·
`queue_config` e a fila atendida · a isenção de `C_ai` da fila muda.

## 5. Fases

| # | Fase | Entrega | Nota |
|---|---|---|---|
| **P0** | Medição de `close_reason` | Qual valor a fila muda expirada grava hoje | **precede o schema** (D5) |
| **P1** | Schema + registry | Campo no `PoolRegistrationSchema`, migration, aceite no agent-registry | default `queue` (D2) |
| **P2** | Enforcement | Ramo `reject` no routing, `cause="no_resource"`, `_emit_outage` | simétrico à cota (D3/D4) |
| **P3** | UI | Campo na tela de pool, ao lado de `queue_config` | invariante de config |
| **P4** | Mapeamento A2A | `reject` → `503`+`Retry-After` / `TASK_STATE_REJECTED`; remover o D11 do ADR | só quando o binding existir |

P1–P3 são independentes do arco de A2A e valem por si: fecham um desfecho que hoje não existe
para nenhum chamador programático, incluindo os internos (agenda, hook destacado, fan-out de
campanha).

## 6. Riscos

| Risco | Sinal de que aconteceu |
|---|---|
| Default trocado em pool de gente | Taxa de `no_resource` sobe num pool humano; contato real recusado com agente prestes a liberar |
| `no_resource` somando dois fatos | Relatório não distingue recusa-na-porta de espera-muda-expirada (P0 existe para impedir) |
| Campo virando reserva de capacidade por tabela lateral | Aparece contador, SET ou aritmética pendurada no `on_no_resource` — é o D7 sendo violado |
| Caller ignorando `Retry-After` | Contagem de sessão cresce sem fila crescer; o campo funcionou e o problema mudou de lugar (D8) |

## 7. Fora de escopo

- **Rate limit na borda por chamador.** Instrumento diferente, problema diferente (D8).
- **Cota por `a2a_client`** (D9 do ADR de A2A). Com pool dedicado + `on_no_resource: reject` +
  `allowed_pools`, a contenção existe sem dimensão de metering nova. Reabrir só se aparecer um
  segundo caller disputando o mesmo pool.
- **Reviver reserva de sessão por pool** (D7).
- **Mudar o momento do débito de admissão.** Medido e correto onde está; recusar na porta é
  justamente o que torna o débito-na-porta inofensivo para o caso programático.

## 8. Referências

- Código: `routing-engine/src/plughub_routing/main.py:256-277, 776-778, 825-827, 1388` ·
  `admission.py` (docstring de módulo, `admit`, `release`, `has_headroom`) ·
  `kafka_listener.py:674`
- [`adr-a2a-server-binding.md`](adr-a2a-server-binding.md) — D9, D11, §9
- `CLAUDE.md` § "Admissão de sessão — UM gate, na moeda certa" (lista de *não reviver*)
- `docs/arcos/queue-attended-model.md` — fila muda, `first_queued_ms`, drain
- Migration `20260802000000_drop_pool_session_reservation`
