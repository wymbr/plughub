# skill-flow-worker — PACOTE FÓSSIL (quarentena, 2026-09-30)

> **Não está deployado em compose nenhum desde a WFL-01.** Mantido no repositório
> em quarentena documentada, não apagado, pelo mesmo critério de
> `conversation-writer` e `clickhouse-consumer`: o erro fica visível e reversível.
> **Não religar sem ler esta página.**

## Por que fóssil

Era o consumidor de `workflow.events` que rodava o SkillFlowEngine para instâncias de
workflow do Arc 4. Medido em 2026-09-30:

| Evidência | Resultado |
|---|---|
| Produtor de `workflow.events` | só a `workflow-api`, sem rota de produto desde a AUT-64 (e fóssil também) |
| Chamadas que o worker fazia | `persist-suspend`, `collect/persist` e demais rotas de instância da workflow-api: **removidas** na AUT-64 (antes, 410 desde o Arc 19) |
| Chamada ao mcp-server | `POST /mcp`, rota que o mcp-server nunca expôs (só `/sse` e `/messages`) |
| Cenário e2e que dependia dele | o 18, removido na AUT-64 |

Se alguém o subir, ele se conecta ao Kafka e espera um tópico que ninguém produz.
Não haveria erro; haveria um processo ocioso que parece infraestrutura viva.

## O que substituiu

O Arc 19: workflow é canal `webhook` do channel-gateway, o pool aloca uma instância
de skill-flow pelo orchestrator-bridge, e o `skill-flow-service` (`/execute`) executa
o fluxo. Suspend/resume é da sessão.

Ver `CHANGELOG.md` § 2026-09-30 (5) e `packages/workflow-api/README.md`.
