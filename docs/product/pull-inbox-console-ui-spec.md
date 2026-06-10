# Inbox de Pull no Console — UI · Especificação

> **Contexto:** a interface humana do **dispatch pull genérico** (`routing-pull-dispatch-spec.md`). **Integrada ao Console** (não é tela à parte): reusa o layout de atendimento push — lista à esquerda, conteúdo no meio, action bar — para minimizar UI nova e manter a memória muscular do operador.
> **Genérica:** lista qualquer contato de pool pull (e-mail, back-office, webhook/aprovação). A **aprovação** é uma especialização (pacote/decisões por cima).
> **Status:** especificação de UI. **Data:** Junho 2026.

---

## 1. Fluxo (como o operador usa)

1. **Filas piscando (rail esquerdo).** Cada fila pull em que o agente está logado aparece no rail. Quando chega um contato claimável, a fila **pisca/badge** — aviso a **todos os agentes logados com capacidade** (§4). 
2. **Clica na fila piscante** → abre, no painel esquerdo, a **lista de contatos** daquela fila (claimáveis, ordenados por peso; claimados por outros aparecem esmaecidos).
3. **Seleciona um contato** → o **painel central** exibe o contato **igual ao atendimento push** (conversa/contexto/anexos), em **modo preview** (read-only — sem claim).
4. **Action bar → "Pull (Atender)"** → solicita o claim ao Routing Engine. 
   - **Ganhou:** o contato vira "meu" — vira atendimento normal; a action bar troca para as ações de atendimento (responder; em aprovação: decidir/encaminhar; devolver).
   - **Perdeu** (outro pegou antes): toast "esse contato acabou de ser pego" e o contato **some da lista**.

É o mesmo esqueleto do push; a diferença é o passo de **preview + Pull** antes de assumir.

---

## 2. Layout (três zonas — reusa o atendimento)

```
┌────────────┬───────────────────────────┬──────────────────────────────┐
│ RAIL FILAS │ LISTA DE CONTATOS         │ PAINEL CENTRAL (preview/atend.)│
│ (esquerdo) │ (da fila selecionada)     │                                │
│            │                           │  [contexto / conversa / anexos]│
│ ● Vendas ⦿ │ ▸ João — carrinho R$1.2k  │                                │
│ ● Suporte  │   2min · SLA 8min · ★alta │  modo PREVIEW (read-only)      │
│ ● Aprov. ⦿ │ ▸ Ana — troca pedido      │                                │
│            │   5min · SLA 3min         │                                │
│ cap: 2/3   │ ▸ (esmaecido) Léo — claim │  ┌──────────────────────────┐  │
│            │     por Maria            │  │ ACTION BAR               │  │
│            │                           │  │  [ Pull (Atender) ]      │  │
└────────────┴───────────────────────────┴──┴──────────────────────────┴──┘
```

- **Rail de filas:** nome + badge de contagem; **dot piscando** quando há claimável novo **e** o agente tem capacidade. **Cor do dot = saúde de SLA** (verde/amarelo/vermelho por `oldest-wait` vs `sla_target`). Indicador de **capacidade** (`cap: 2/3` — sessões push+pull).
- **Lista de contatos:** resumo (do `queue_contact`), **idade**, **SLA** (texto colorido verde/amarelo/vermelho), **indicador de peso/prioridade** (★). Ordenada pelo peso da fila (§3.1 do routing spec). Claimados por outros = esmaecidos (visível, não clicável para claim).
- **Painel central:** em **preview** mostra o contato como no push (read-only); após o Pull, vira atendimento pleno.
- **Action bar:** em preview → **Pull (Atender)**; após claim → ações de atendimento (responder / decidir / **Devolver à fila**).

---

## 3. Estados de um item

| Estado | Visual | Ações |
|---|---|---|
| Claimável | normal na lista; fila pisca | selecionar (preview) → Pull |
| Em preview (por mim) | aberto no centro, banner "pré-visualização" | Pull, ou selecionar outro |
| Claimado por mim | atendimento pleno no centro | responder / decidir / **Devolver** |
| Claimado por outro | esmaecido na lista | — (read-only) |
| Perdido no claim | toast + remove da lista | — |
| Devolvido / auto-released | reaparece como claimável (fila volta a piscar) | — |

---

## 4. Capacidade e notificação (§10.5 do routing spec)

- O **aviso de chegada** (piscar) vai só a agentes logados na fila **com capacidade** (`instance_has_capacity`, push+pull combinados).
- Com capacidade **esgotada**: as filas **não piscam**, o botão **Pull** fica **desabilitado**, e a lista pode ficar read-only (o agente vê, mas não pega). Ao liberar uma sessão, volta a ser notificado.
- O indicador `cap: usadas/máx` no rail dá o porquê de o Pull estar bloqueado.

---

## 5. Concorrência preview→claim (§10.4)

Preview é **soft** (read-only, sem `ZREM`) — vários agentes podem pré-visualizar a mesma task. O **Pull** resolve por `ZREM` atômico no Routing Engine: **um vence**. O perdedor recebe "esse contato acabou de ser pego" e a task **some da lista** (atualização via o mesmo canal de eventos que faz a fila piscar).

---

## 6. Auto-release pela conexão (§10.1)

A inbox vive no Console (WS). Se a conexão cai (crash/fechou o navegador), o **crash_detector** detecta e o Routing Engine **re-enfileira** o que o agente tinha claimado (volta a piscar para os demais). A **lease TTL** cobre "conectado mas ocioso". O agente também pode **Devolver à fila** manualmente.

---

## 7. Especialização: aprovação

Quando o item é uma **sessão de workflow suspensa** (pool pull de aprovação), o painel central, após o Pull, renderiza o **pacote de aprovação** (form padrão + campos editáveis + anexos) e a action bar mostra os **botões de decisão** (do `decisions` declarado pelo workflow) + **Devolver**. Tudo o mais (rail, lista, preview, claim, capacidade) é o mecanismo genérico. Ver `human-work-queue-aprovacao-spec.md`.

---

## 8. Permissões (ABAC)

Rail e listas filtrados por `accessible_pools`; todos os logados num pool têm acesso pleno à fila dele. Decisões de aprovação podem exigir `approvals.decide`. Visualização de contexto/anexos respeita masking por role.

---

## 9. Eventos de UI (tempo real)

A inbox precisa de um canal de eventos para: fila piscar (chegou claimável), item sumir (claimado/resolvido), item reaparecer (release/auto-release), capacidade mudou. Reusa o barramento de eventos do Console (o mesmo que entrega contatos push) — `queue.position_updated` + um evento de "fila pull mudou" por pool. (Definir o nome/forma do evento na implementação.)

---

## 10. Decisões (fechadas)

1. ~~Evento de "fila pull mudou"~~ **Resolvido:** **sem evento dedicado** — o **ciclo do heartbeat** carrega o snapshot das filas pull do agente (contagem claimável por pool, do pool snapshot/fila). Já atrelado a capacidade e liveness. Latência do piscar = intervalo do heartbeat (ok para async); cadência opcionalmente mais curta com a inbox aberta.
2. ~~Mostrar peso/posição~~ **Resolvido:** indicador **★/SLA**, **sem** número de posição; lista ordenada por peso.
3. ~~Multi-fila piscando~~ **Resolvido com código de cor:** **cor = urgência de SLA** (verde/amarelo/vermelho por `oldest-wait` vs `sla_target`), no dot da fila e nas linhas; **piscar = há claimável + tenho capacidade**. Entre várias filas piscando, a **vermelha** tem precedência visual.
