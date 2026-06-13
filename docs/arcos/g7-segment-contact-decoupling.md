# G7 — Decoupling Segment-End × Contact-Close (+ NPS como hook de contato)

> **Status**: arco aberto (planejamento). Primeira fatia entregue como branch cirúrgico do **transfer**
> (ver `docs/guias/conference-mechanics.md` § Mudança 9 + CHANGELOG 2026-06-12). O restante é dívida.

## 1. Problema

O modelo de 3 camadas (CLAUDE.md § Session & Conference Lifecycle) define:

1. **Contato** (perspectiva do cliente) — estatísticas congelam quando o cliente sai.
2. **Segmento do agente** — a janela de cada participante; o recurso do pool é liberado no `agent_done`.
3. **Conferência** (a sala) — destruída só quando todos saem.

A implementação **conflaciona camadas 1 e 3**: `on_human_end` (fim de segmento humano) está **acoplado** ao
`_trigger_contact_close()` (fim de contato). Hoje "último humano saiu" ≡ "fechar contato", e os hooks
`on_human_end` (wrap-up **e** NPS) disparam juntos, no fim. Isso gera os gaps G1–G6 e impede cenários
legítimos onde um segmento termina **mas o contato continua** (transfer, handoff, multi-humano).

## 2. O que já foi feito (fatia transfer — Mudança 9)

Branch cirúrgico em `process_contact_event` para `reason==agent_transfer`: a origem sai como **fim de
segmento** (restore + `participant_left outcome=transferred` + `agent_done` lifecycle DECR + SREM
`human_agents`), **sem** `_mark_contact_ended`, **sem** `on_human_end`, **sem** close, **sem** marcar
`session:{id}:closed`. O contato segue pela re-rota. Validado E2E (2 segmentos humanos num contato).

Isso resolve **só o caso transfer**. A semântica geral continua acoplada.

## 3. Modelo-alvo

| Conceito | Camada | Quando dispara | Hoje |
|---|---|---|---|
| **wrap-up** (notas do agente) | segmento | **todo** fim de segmento humano (transfer, saída não-última, final) | só no último humano |
| **NPS / pesquisa do cliente** | contato / journey / segmento | via **outbound** por grão (ver §5), não inline no último `on_human_end` | inline, 1× no fim |
| **contact-close** | contato | quando o fim de segmento **também** é fim de contato (sem continuação: sem transfer/re-rota pendente, sem outro humano ativo) | acoplado ao `on_human_end` |

**Regra de fechamento (alvo)**: o contato fecha quando um segmento termina **E** não há continuação. Sinais
de continuação: `outcome=transferred`/re-rota pendente, outro humano primário ativo, specialist ativo. Caso
contrário → fim de contato → fecha.

## 4. Componentes a mudar

- **`orchestrator-bridge` `process_contact_event`**: generalizar o branch `agent_transfer` para um conceito
  de **continuação** (não só transfer explícito — também re-fila, handback IA). Quando um humano sai:
  sempre rodar o **wrap-up do segmento**; decidir o contact-close por "há continuação?".
- **`fire_pool_hooks`**: separar **wrap-up** (side=agent, por segmento, **não** arma `hook_pending`/posatt de
  fechamento) de **NPS** (side=customer, hoje acoplado). Hoje a conclusão do hook decrementa
  `posatt:active`/`hook_pending` → `_destroy_conference`/`_trigger_contact_close`; o wrap-up por segmento
  **não** pode disparar fechamento.
- **NPS como hook de contato de 1ª classe**: tirar o NPS da carona do `on_human_end` do último humano; passar
  a um disparo no **fim de contato** (ou, melhor, ao modelo outbound multi-grão — §5).
- **Multi-humano**: um humano que **não é o último** a sair (set `human_agents` não esvazia) hoje não dispara
  `on_human_end` → seu segmento não ganha wrap-up. Corrigir para wrap-up por segmento independente do "último".
- **Marcador `session:{id}:closed` (conflação multi-humano — Fase 3)**: hoje o `/api/agent_done`
  (mcp-server, `server.ts` ~1475) seta `session:{id}:closed=agent_closed` **incondicionalmente**, e o bridge
  também (`process_contact_event` ~3994, hoje só exceção `agent_transfer`). Com **2 humanos**, quando um sai
  (`remaining>0`, caminho "Agent dropped, N still active") o segmento termina e o contato segue — **mas o
  marcador é setado mesmo assim**, e o Routing Engine o lê (`is_closing` guard / `_drain_queue_for_agent`)
  para descartar re-rotas/reconexões da sessão ainda ativa. Resultado: **não há saída limpa de "deixar só 1
  humano"** — o botão Close/Hang up (ambos `→ /api/agent_done`, diferindo só no `outcome`: `resolved` vs
  `abandoned`) marca a sessão como fechando. **Fix Fase 3**: condicionar o `session:closed` a
  `_has_continuation()==False` no bridge **e** repensar o set incondicional do mcp-server (que não conhece o
  `remaining` — opção: o bridge **desfaz** o marcador quando detecta continuação `other_human_active`, ou o
  mcp-server para de setar e delega ao bridge). O Console do agente que sai também marca `sessionClosed=true`
  local (`AgentAssistPage.handleClose`) — revisar para o caso "saí do segmento mas o contato segue".

**Absorve o "Estágio 3" do transfer** (wrap-up transfer-aware coletando o motivo via `escalation_reasons` →
`survey_record`): vira caso particular de "todo fim de segmento gera wrap-up", não um caminho dedicado.

## 5. Relação com o modelo de pesquisa multi-grão (F11 / outbound)

A avaliação do cliente **não é inline** — é **outbound**, e pode ocorrer em **até 3 grãos** (taxonomia já
existente em `SignalGrainSchema`: `segment | session | workflow | journey`), **configurável por fluxo**:

- **journey** — avalia o relacionamento multi-sessão como um todo.
- **session** (contato) — avalia cada contato.
- **segment** — avalia cada segmento (agente) dentro de cada contato.

Cada grão é uma **survey outbound** (sessão própria que religa à origem e grava via `survey_record` com o
`grain` e o alvo) — `captured_at ≠ session_at`. Base já parcialmente construída (F10.2b:
`survey_collector_ia`/`survey_reconnect_ia` + `survey_record grain=segment`). O que falta é o **planejamento
da orquestração**: quando/como o fluxo dispara cada grão (1 ao fim do contato/journey + N por segmento,
diferidas). Isso é a **F11** (grão journey ponta-a-ponta + survey diferida) — arco de evaluation, **separado**
do G7 (que é ciclo de vida da conferência). G7 garante que cada **segmento** existe e é atribuível; F11
decide **quais** pesquisas rodam e quando.

> Implicação: o "F5 multi-humano / 1 NPS por agente" **não é trabalho inline** — é F11/outbound. O F5 inline
> (grão segmento, NPS/wrap-up atribuídos ao segmento que dispara o hook) está **concluído e validado**.

## 6. Gaps cobertos quando G7 fechar

G1 (AHT inflado por wrap-up — separar o congelamento de AHT do wrap-up), G2 (`remaining` ignora specialists
IA), G3–G6 (restore/supervisor/close), e o próprio G7 (decoupling geral). Tratar com cuidado: é o nó mais
frágil do sistema; fazer com gates de teste E2E entre fatias (como foi feito no transfer).

## 7. Conferência multi-humano — gaps expostos (2026-06-12) + reordenação do plano

A Fase 0 do G7 ✅ está **validada nos 3 casos** (`no_continuation`, `transfer`, `other_human_active` —
este último confirmado em log: `G7-decision: ... remaining=1 → continuation=True (other_human_active)`).
Para reproduzir o `other_human_active` foi habilitado o **convite de humano como specialist** (endpoint
`mentionable-agents` passou a incluir pools `agent_kind=human`; o dispatch do @mention já era agnóstico a
kind). Isso **destravou 2 humanos simultâneos numa conferência pela 1ª vez** — e expôs **gaps pré-existentes
da conferência multi-humano** (não causados pelo G7; nunca apareciam porque multi-humano não era alcançável):

1. **Sem fan-out humano↔humano**: mensagem de um humano vai só ao **cliente** (visibility=all) e a quem
   enviou; os **outros humanos da conferência não recebem**. O bridge entrega "ao humano" (singular).
2. **Roteamento do menu do wrap-up quebrado em multi-humano**: a resposta do humano ao menu do wrap-up é
   roteada como mensagem normal (`is_human=True, menu_waiting=False → "Forwarded text to human agent"`) e vai
   pra conferência/cliente em vez de casar com o `menu:result` do wrap-up. O bridge **não desambigua qual
   input de qual humano alimenta qual menu suspenso**.
3. **NPS não dispara em multi-humano**: o fluxo não chega a um "último humano fecha → on_human_end" limpo
   (closes caem em `other_human_active`); o disparo customer-side do NPS se perde.

**Dependência**: o gap (2) é **pré-requisito da Fase 1** (wrap-up por segmento do humano não-último) — essa
fase dispara wrap-up por segmento e precisa do roteamento de menu por participante funcionando em
multi-humano. (1) e (4/NPS) são da conferência multi-humano ampla.

**Reordenação do plano (decisão 2026-06-12)**: a Fase 1 (wrap-up do não-último) **depende** de conferência
multi-humano sólida → fica **adiada**. Prioriza-se o maior valor do G7 que **não** depende de multi-humano:
o **decoupling de close + NPS-como-hook-de-contato** para os casos **single-humano e transfer** (a antiga
Fase 3). Nova ordem sugerida: **Fase 2 (wrap-up no transfer)** → **Fase 3 (close por continuação + NPS de
contato, single/transfer)** → [sub-arco **Conferência multi-humano**: fan-out de msg + roteamento de menu por
participante + NPS multi-humano] → **Fase 1 (wrap-up do não-último)** → **Fase 4 (limpeza + docs)**.

> **Sub-arco "Conferência multi-humano"** (novo, ligado ao G7): fan-out de mensagem humano↔humano;
> roteamento de `menu:result` por participante (desambiguação de input por humano); NPS/close coerentes
> com N humanos. É frente substancial — provavelmente arco próprio. Habilitado pela capacidade nova de
> convidar humano como specialist (`mentionable-agents` + dispatch agnóstico a kind).

## 8. Slice A ✅ — wrap-up multi-humano (identidade de participante por-segmento, 2026-06-12)

Resolve o **gap (2)** do §7 (roteamento de menu por participante) e o report "wrap-up só funciona no
segmento final". Causa-raiz: identidade do humano vivia num único campo de SESSÃO
(`session.human_agent_participant_id`), lido por 4 componentes e sobrescrito a cada humano. Movida
para `segment.{segId}.served_human_participant_id` (fonte única por escopo — ver
[`docs/adr/adr-participant-identity-single-source.md`](../adr/adr-participant-identity-single-source.md)).
Três partes: (a) endereçamento por-segmento (bridge `fire_pool_hooks`+join, YAML `@segment.*`);
(b) entrega isolada (filtro de array-visibility no `forward()` do mcp-server); (c) entrada pelo
remetente real (conexão no texto WS; `agent_key`/`source_instance` no botão). Detalhe em
`docs/guias/conference-mechanics.md` § Mudança 10 + CHANGELOG. **Habilita a Slice B** (wrap-up no
transfer): o wrap-up por-segmento agora funciona com origem+destino simultâneos.

**Ainda aberto no sub-arco multi-humano**: gap (1) fan-out de mensagem humano↔humano e gap (3) NPS em
multi-humano. **Falta do G7 geral**: Slice B (wrap-up no transfer = fim-de-segmento) + Fase 3 (close
por continuação + NPS de contato).

### 8.1 Achado (2026-06-13) — segmento primário travado "live" → contato não fecha (sub-arco multi-humano)

Validação da Slice A com **2 humanos `primary` em pools distintos** (`admin`/`retencao_humano` +
`operator`/`humanoxxx` convidado por @mention; ambos os pools com `on_human_end` = `wrapup_ia`+`nps_ia`)
expôs um bug **upstream da Slice A**, no lifecycle de close multi-humano. Evidência (Analytics/Segments
da sessão `…bcbc419b9f7f`):

```
primary  skill atendimento sac   escalated_human  closed
queue    agente fila             escalated_human  closed
primary  admin@…   22:18:41 …     in progress      ● LIVE   ← nunca encerra
primary  operator@… 22:19:11→20:29 resolved        closed
primary  operator@… 22:21:09→09    resolved        closed
```

O segmento do **primeiro** primário (admin, handoff#1) fica **`in progress` indefinidamente** — o
`agent_done`/handoff não encerra o segmento quando há outro primário ativo. Consequência em cadeia:
o contato nunca atinge "todos os primários encerraram" → **`on_human_end` não dispara** (sem wrap-up
nem NPS) → **não fecha** → **re-enfileira** (ContextStore da sessão só tinha `session.pool.id`+
`session.queue.position=1`, nenhum `served_human`/`human_agent_participant_id`/`close_origin`).

**Conclusão**: o wrap-up/NPS ausentes nesse cenário são **sintoma**, não causa. A causa é o
encerramento de segmento do primário não-último (segment-end no handoff/close com N primários). A
Slice A (identidade por-segmento) está **correta e provada** isoladamente (sessão `24134a34`:
`served_human_participant_id` gravado certo). O conserto ponta-a-ponta deste cenário pertence a
**Fase 3 (close por continuação)** + ao **sub-arco multi-humano** (encerrar o segmento de cada
primário no seu `agent_done`, independente de ser o último). Slice A é pré-requisito.

## 9. Slice B ✅ — wrap-up no transfer (hook type `segment_wrapup`, 2026-06-13)

Primeira aplicação do modelo-alvo "todo fim de segmento gera wrap-up" — para o caso **transfer**
(continuação `transfer` do `_has_continuation`). Hook type novo **`segment_wrapup`**: dispara só o
wrap-up `side=agent` para o segmento da origem, **sem** armar `posatt:active`/`hook_pending` e **sem**
NPS (NPS é fim-de-CONTATO, Fase 3). O branch `agent_transfer` troca o `return` seco por esse dispatch;
a conclusão aplica a disposição ao segmento (`seg_signal`→re-publish) e **não** fecha o contato (segue
pelo destino). Console: a origem entra em modo wrap-up (não larga o contato). Detalhe em
`conference-mechanics.md` § Mudança 11 + CHANGELOG. Generaliza-se naturalmente para a **Fase 1**
(wrap-up do não-último: mesmo `segment_wrapup` no branch `other_human_active`) quando o sub-arco
multi-humano fechar o encerramento de segmento do não-último (o achado §8.1).
