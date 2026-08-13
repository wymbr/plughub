# ADR — Borda única de interceptação MCP: a regra mora no servidor, e o alcance é fato de rede

> Status: **proposto** · 2026-08-13 · nascido ao alinhar o audit do `invoke` ao contrato `mcp.audit`
> (ver `CHANGELOG.md` § *"`invoke` do agente externo: a borda que validava permissão e não deixava rastro"*)
>
> Fecha a decisão deixada em aberto em `TODO.md` § *"As chamadas de domínio do agente NATIVO não passam
> por interceptação nenhuma"*.
>
> Relacionado: [`adr-message-masking.md`](adr-message-masking.md) (o que a borda protege),
> [`docs/arcos/audit-lgpd.md`](../arcos/audit-lgpd.md) (quem consome o rastro).

---

## 1. Contexto

O CLAUDE.md declara um invariante categórico:

> *"All domain MCP calls are intercepted — native agents via `McpInterceptor` (in-process); external
> agents via proxy sidecar on localhost:7422. No MCP call reaches a domain server without permission
> validation, injection guard, and audit."*

E o `docs/product/value-proposition.md:36` o vende a CISO/DPO como diferencial contra as três
plataformas concorrentes. A medição de 2026-08-13 mostra o estado real:

| Borda | Quem deveria usar | Permissão | Injection guard | `AuditRecord` | Estado medido |
|---|---|---|---|---|---|
| `invoke` (mcp-server) | agente `external-mcp` | ✅ | ✅ | ✅ `mcp.audit` | ✅ desde 2026-08-13 |
| proxy sidecar (`sdk/src/proxy/server.ts`) | agente externo que fala direto com o domain server | ✅ | ✅ | ✅ | implementado — **só existe se o operador subir o processo** |
| `McpInterceptor` (`sdk/src/mcp-interceptor.ts`) | agente **nativo** | ✅ | ✅ | ✅ | **nunca instanciado** — a classe só aparece em definição e em comentários |

O caminho real do agente nativo — o de **maior volume da plataforma** — é o `mcpCall` do
`skill-flow-service` (`packages/e2e-tests/services/skill-flow-service/src/index.ts:149`, o que o
orchestrator-bridge executa) e o do `skill-flow-worker` (`engine-runner.ts:150`, legado): `fetch`
JSON-RPC cru. Sem filtro de `permissions[]`, sem guard, sem registro.

### 1.1 Por que ninguém notou

O modo de falha é a **ausência de linhas num relatório**. Nenhuma chamada falha, nada fica vermelho,
e "não houve chamada" é indistinguível de "não foi auditada" para quem só olha a tela. Foi o mesmo
mecanismo que manteve o `invoke` publicando num tópico inexistente por meses — ali havia até uma
exceção sendo lançada pelo broker, e um `catch` mudo a descartava.

### 1.2 O defeito estrutural, não o sintoma

A mesma regra — *permissão → injection guard → `AuditRecord`* — está escrita **três vezes**, em três
linguagens de call site, e as cópias **já divergiram** sem que nada acusasse:

| | `invoke` | proxy sidecar |
|---|---|---|
| curinga `"{server}:*"` | não aceita | aceita (`server.ts:104-110`) |
| `permissions[]` vazia | nega tudo | sem filtro |

Duas implementações da mesma frase, dois resultados diferentes para o mesmo agent-type. Uma terceira
(o `McpInterceptor`) existe e não roda. Uma quarta seria criada se cada novo tipo de agente ganhasse
a sua.

---

## 2. Decisão

**A regra mora no `mcp-server-plughub`. Todo agente — nativo, externo, GitAgent — alcança um domain
MCP server ATRAVÉS dele, e nenhum outro componente reimplementa o veredicto.**

Consequências diretas:

1. **`lib/invoke-audit.ts` é a implementação canônica.** `judgeInvoke()` + `buildInvokeAuditRecord()`
   já são puros e sem I/O exatamente para poder ser consumidos por mais de um call site.
2. **O `mcpCall` do skill-flow-service passa a chamar a plataforma**, não o domain server. É a saída
   (b) do TODO; a saída (a) — instanciar o `McpInterceptor` — fica descartada por criar uma segunda
   implementação viva da mesma regra, em outro processo, com outro ciclo de deploy.
3. **O proxy do agente externo muda de papel**: de *aplicador da regra* (hoje: valida, guarda, audita
   e roteia para o domain server) para **mapeador de vocabulário** — traduz a chamada nativa do
   framework em `invoke(mcp_server, tool, params)` contra a plataforma. Ele deixa de ter regra
   própria, logo deixa de poder divergir.
4. **As duas assimetrias da tabela §1.2 viram uma decisão só**, tomada uma vez, no lugar único.

### 2.1 O que NÃO é decidido aqui

O `McpInterceptor` **não é removido** neste ADR: ele é o que sustenta a promessa de portabilidade do
SDK (agente que roda fora da plataforma e mesmo assim audita). Fica como caminho **opcional do
agente que não usa a plataforma** — não como borda de produção.

---

## 3. O ponto que decide de verdade: a borda é topológica, não de código

**Uma borda que o chamador escolhe instalar é conselho, não controle.** O sidecar de hoje é evitável
por omissão: basta não subir o processo, ou apontar o agente diretamente para
`http://mcp-server-crm:3500`. E mover a regra para o servidor **não conserta isso sozinho** — conserta
se, e somente se:

> **Requisito T — inalcançabilidade.** Um domain MCP server só aceita conexão originada do
> `mcp-server-plughub`. Nenhuma rota de rede alcançável pelo processo do agente (nativo ou externo)
> chega a um domain server.

Sem T, toda a §2 é um caminho *conveniente*, não *obrigatório*, e a frase do
`value-proposition.md` vale por convenção. Hoje **nada no repositório garante T** — é o mesmo
achado da borda do channel-gateway registrado no CLAUDE.md: *a separação é de CÓDIGO, não de
topologia*, e não existe `nginx.conf` nem network policy versionados.

T é requisito de **deploy**, e por isso precisa de um artefato próprio: rede docker/k8s dedicada aos
domain servers, com o mcp-server como único membro autorizado. Enquanto T não existir, a redação
honesta do invariante é *"toda chamada MCP que passa pela plataforma é interceptada"* — que é uma
frase mais fraca, e é a verdadeira.

---

## 4. O que o proxy-mapeador não resolve — e a extensão que fecha o buraco

Traduzir `customer_get` → `invoke(...)` é mecânico. **Receber trabalho não é.** O agente externo
ainda precisa de alguém que faça `agent_login → agent_ready → wait_for_assignment` e o acione quando
o Routing Engine alocar um contato.

É exatamente aí que está o buraco já medido: `AgentFrameworkSchema`
(`schemas/src/agent-registry.ts:461-471`) aceita `langgraph`, `crewai`, `anthropic_sdk`, `azure_ai`,
`google_vertex`, `generic_mcp` — e o dispatch do bridge (`orchestrator-bridge/main.py:15`) trata
todos com *"logged as warning (LangGraph, CrewAI, etc. — NYI)"*. Só `external-mcp`, `plughub-native`
e `human` têm ativação. **O que falta a esses frameworks nunca foi interceptação; é ativação.**

A extensão natural do proxy-mapeador — **proxy como *shell* do agente** — fecha as duas coisas com um
componente só: o proxy se registra na plataforma, bloqueia em `wait_for_assignment`, invoca o agente
estrangeiro com o `context_package` adaptado, mapeia as tool calls dele para `invoke`, e devolve
`agent_done`. O agente estrangeiro não aprende nada sobre PlugHub, e o enum de frameworks deixa de
prometer o que não entrega.

Fica registrado como **direção**, não como escopo deste ADR: é arco próprio, e depende de decidir se
o mapeamento de vocabulário é declarativo (config por framework) ou código por framework.

---

## 5. Custos aceitos

| Custo | Tamanho | Nota |
|---|---|---|
| Hop de rede por chamada de domínio | o sidecar existe justamente para evitá-lo (loopback, < 1 ms) | O nativo já paga um hop hoje (`fetch` ao domain server); para ele o delta é ~0. Quem paga novo é o externo com sidecar. |
| `mcp-server-plughub` vira ponto de estrangulamento | real | Agrava um defeito já anotado no próprio código: `_domainClients` é um `Map` de clientes SSE **sem health-check e sem retry** (`tools/external-agent.ts`, comentário *"Em produção, substituir por pool com health-check"*). Corrigir isso é **pré-requisito**, não follow-up. |
| Latência do audit no caminho quente | nula | A escrita é fire-and-forget e já loga `AUDIT_WRITE_FAILED` quando falha. |

---

## 6. Fases

| Fase | O que | Gate |
|---|---|---|
| **M0 — medir** | Contar chamadas de domínio por caminho (nativo × `external-mcp`) num dia normal. | O número decide a urgência e é o argumento LGPD. Sem ele, "95% do tráfego sem auditoria" e "5%" recebem o mesmo esforço. |
| **B1** | Pool de clientes de domínio com health-check + retry no mcp-server. | Chaos test: derrubar um domain server e provar que a 2ª chamada reconecta em vez de herdar socket morto. |
| **B2** | `skill-flow-service.mcpCall` → `invoke` da plataforma (ou chamada in-process ao mesmo veredicto). | Contagem de `mcp.audit` com `source` do caminho nativo **> 0** num fluxo real. Hoje é 0 — e 0 é o valor que o gate tem de reprovar. |
| **B3** | Unificar as assimetrias da §1.2 numa decisão só. | O teste que hoje pina o comportamento do `invoke` (`__tests__/invoke-audit.test.ts`) fica vermelho de propósito: a mudança tem de ser explícita. |
| **T** | Requisito de inalcançabilidade (rede dedicada aos domain servers). | Probe que tenta alcançar um domain server a partir do container de um agente e **exige** falha de conexão. Enquanto esse probe não existir, o invariante do CLAUDE.md fica com a redação fraca da §3. |
| **P** *(fora deste ADR)* | Proxy como shell do agente externo → fecha o NYI de `langgraph`/`crewai`. | — |

---

## 7. Não-objetivos

- Remover o `McpInterceptor` (§2.1).
- Reescrever o sidecar antes de M0 — ele funciona para quem o sobe; o problema é ser evitável, e isso é T, não código do sidecar.
- Auditar chamadas que não são de domínio (tools da própria plataforma) — escopo do `AuditPolicy` por tool, outro assunto.
