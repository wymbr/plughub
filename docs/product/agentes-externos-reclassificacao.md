# Agentes externos — reclassificação: exportar em vez de importar

- **Status:** proposto
- **Data:** 2026-08-13
- **Relacionado:** [`docs/adr/adr-a2a-server-binding.md`](../adr/adr-a2a-server-binding.md) ·
  [`docs/adr/adr-mcp-interception-single-border.md`](../adr/adr-mcp-interception-single-border.md) ·
  [`docs/arcos/quality-ingest.md`](../arcos/quality-ingest.md)

---

## 1. A pergunta

Com o binding A2A servidor (expor pools da plataforma como agentes A2A padrão), ainda faz
sentido manter a linha de **importar agentes externos** — rodar um LangGraph/CrewAI de
terceiro como pool da plataforma, com SDK de certificação e proxy sidecar?

## 2. O princípio

> Importar agentes resolve a integração **dissolvendo a fronteira**.
> A2A resolve **padronizando a fronteira**.
> **Padronizar ganha sempre que há propriedades a garantir.**

E há. Capacidade por recurso, heartbeat, pausa, contrato
`agent_login → ready → busy → done`, `issue_status` obrigatório, interceptação MCP com
auditoria não-optável (LGPD), substrato de avaliação. Trazer um agente de terceiro para
dentro como pool é pedir que a plataforma **garanta essas propriedades sobre código que não
controla**. O modelo de importação não é apenas caro: ele corrói justamente a camada de
governança que é o diferencial defensável.

## 3. O estado medido (2026-08-13)

Não se trata de cortar capacidade em produção. A importação é, hoje, majoritariamente
promessa:

| Borda | Estado |
|---|---|
| Native agent (SDK), `McpInterceptor` in-process | ⚠️ **nunca instanciado** — o caminho real (`skill-flow-service.mcpCall`) faz `fetch` cru, sem gate |
| External agent (LangGraph/CrewAI), proxy sidecar `:7422` | implementado, mas **só existe se o operador subir o processo** |
| Agent `external-mcp`, tool `invoke` do mcp-server | ✅ em vigor |

E o ADR de borda única já concluiu o que generaliza o problema: **borda é fato de REDE, não
de código.** Enquanto um domain MCP server for alcançável a partir do processo do agente,
qualquer borda é evitável por omissão. Um agente importado está, por construção, do lado de
dentro dessa alcançabilidade.

## 4. Evidência de convergência (não é conveniência)

Outro arco chegou ao mesmo princípio de forma independente. O **quality-ingest** (R13a–R13d)
resolveu *"avaliar agente externo"* **ingerindo a transcrição** — contrato de borda
versionado, masking no ingest, grau-transcript — em vez de rodar o agente por dentro para
poder medi-lo. Dois arcos, decididos em momentos diferentes, escolheram *contrato na borda*
sobre *runtime compartilhado*. Isso é sinal.

## 5. As três coisas que estavam no mesmo pacote

O erro a evitar é aposentar o pacote inteiro por associação. São três, com destinos
diferentes:

| # | Coisa | Do que se trata | Destino |
|---|---|---|---|
| 1 | **`external-mcp`** | expor **tool**, não agente. É a **única** borda de interceptação em vigor hoje | **Fica.** Não encostar |
| 2 | **Portabilidade** (`certify`, `verify-portability`, `skill-extract`, `regenerate`) | responde *"posso sair daqui?"* — anti-lock-in | **Fica, explicitamente separada.** A2A **não** cobre isto: ele torna os agentes alcançáveis, não extraíveis |
| 3 | **Runtime importado** (agente de terceiro rodando como pool + `plughub-sdk proxy`) | responde *"rode o meu agente aí dentro"* | **Rebaixado** a sob-demanda-de-negócio-real. Não deletar; parar de tratar como dívida de roadmap |

**Portabilidade ≠ importação.** É objeção de venda, custa uma CLI e disciplina de não colocar
gancho proprietário no skill. Some junto por associação se o pacote for aposentado em bloco —
e é a metade que sustenta *"sem criar lock-in"*.

## 6. Efeito na afirmação de posicionamento

A primeira linha do `CLAUDE.md` prometia *"connects agents — human and AI, **from any
origin**"*, enquanto a tabela de interceptação, três seções abaixo, dizia que as duas bordas
de agente importado não estão em vigor. **A lacuna entre as duas frases era o passivo real** —
maior que a decisão de escopo, porque uma afirmação de produto que o código não sustenta é
exatamente o "valor plausível" que a Postura de Engenharia manda caçar.

O A2A export fecha a lacuna **por outro caminho, e a torna verdadeira**: qualquer agente, de
qualquer origem, **fala** com os agentes da plataforma por protocolo aberto — sem precisar
virar um deles. A frase foi corrigida nesse sentido (ver `CLAUDE.md` §abertura).

## 7. O que se perde — para a saída ser escolha, não deriva

O cliente que quer *"roda o meu agente, mas me dá a sua fila, o seu roteamento e a sua
capacidade"* fica **sem resposta**. Isso é **hospedagem de agente** — outro produto, adjacente
mas distinto de orquestração e governança de contato. Nomear a perda é o ponto: se essa
demanda aparecer com um contrato atrás, a decisão se reabre com dado, e o item 3 sai do
rebaixamento.

Perda secundária: sobre A2A export, a plataforma avalia **os próprios** agentes. Se o trabalho
é do agente do cliente, não há substrato — mas a resposta a isso já existe e é mais barata
(quality-ingest, §4), então não é perda líquida.

## 8. Sequenciamento

**Não anunciar a aposentadoria antes da fase A1** do arco A2A (AgentCard read-only). Até lá
haveria uma janela sem nenhuma das duas histórias. A1 é barata justamente porque não executa
nada — só prova que o descritor do pool é honesto.

## 9. Ações

- [x] Corrigir a afirmação de abertura do `CLAUDE.md` e a seção de interceptação.
- [x] Emendar `docs/adr/adr-a2a-server-binding.md` §8 com a reclassificação (item 3 fora de
      escopo por decisão de produto, não por custo).
- [ ] Separar, na documentação do SDK (`docs/pacotes/sdk.md`), **portabilidade** (item 2) de
      **runtime importado** (item 3) — hoje aparecem como uma coisa só.
- [ ] Backlog condicional: reabrir o item 3 apenas com demanda comercial nomeada.
