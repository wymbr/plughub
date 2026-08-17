# Kickoff — triagem do backlog contra a linha mestra n8n

> **Uso:** abrir sessão NOVA com Opus e este arquivo como primeira leitura.
> **Tarefa:** reavaliar todo o trabalho em aberto contra a decisão de direção de 2026-08-17, e
> classificar cada item.
> **Não é tarefa desta sessão:** redesenhar a integração (já está fechada em
> `docs/product/n8n-interop-boundaries-and-seams.md`), nem implementar nada.

---

## 1. O que carregar

| Carregar | Por quê |
|---|---|
| `docs/product/n8n-interop-boundaries-and-seams.md` | A linha mestra. Ler §5, §10, §11 com atenção; o resto sob demanda |
| `TODO.md` | O que será triado |
| `CLAUDE.md` § `Pending (Next Iteration)` | Itens que vivem lá e não no TODO |

**Não carregar** a árvore `docs/` inteira, nem `CHANGELOG.md`, nem `plughub_spec_v1.docx`. Leitura
seletiva vale mais aqui do que em qualquer outra tarefa — a triagem decide abortar trabalho real, e
é onde qualidade degradada por contexto inchado custa mais caro.

---

## 2. A linha mestra em dez linhas

1. Medição: ~12% do código de produção é território que o n8n cobre melhor; ~45% é fosso sem
   equivalente no mercado.
2. Decisão: parar de competir nos 12% e integrar.
3. **Alvo declarado: eliminar o editor de fluxo local.** Só o bloco `flow:` do skill sai.
4. **O skill sobrevive como envelope de configuração** — `config_params`, `interface_schema`,
   masking, perfil, `mention_commands`. O modelo de slot/`promote`/`deploy_version` fica inalterado.
5. Regra de fronteira: **n8n toca sistemas; PlugHub toca pessoas.** Todo contato atravessa o
   channel-gateway; é a travessia que produz `journey`/`session`/`segment`.
6. n8n é **recurso chamado** (canal na entrada, domain MCP server na saída), nunca pool, skill ou
   agent type.
7. Padrão único: **pool com skill nativo que CHAMA o n8n** — a instância, a vaga e o segmento
   continuam do PlugHub.
8. Quatro costuras: A (webhook) + E (Kafka Trigger) = fase 0; B (cliente MCP) = principal externo;
   C (domain MCP server) = maior retorno; D (node/template) = fase 5.
9. **Resíduo não-movível, dois itens:** evidência de execução para avaliação tier-2, e hook de
   cliente inline (+ `begin_transaction`/`end_transaction`).
10. Gate empírico: instrumentar latência de turno **antes** de migrar o perfil `agent`.

---

## 3. Taxonomia — quatro baldes

"Continuar ou abortar" perde os dois casos mais comuns. Classificar cada item em:

| Balde | Critério |
|---|---|
| **Segue** | Está no fosso, ou é pré-requisito de uma fase do §11 do doc |
| **Escopo reduzido** | Parte sobrevive — nomear qual parte e qual morre |
| **Congela** | Não morre, mas não investir até o gate da fase 3 |
| **Aborta** | O n8n cobre, ou depende do editor de fluxo que morre |

**Critério permanente** (§10 do doc): *se o item não produz nem consome fronteira de
`journey`/`session`/`segment`, e não é governança de contato com pessoa, é candidato a sair.*

Para cada item, registrar **uma linha de justificativa** amarrada ao critério. Item classificado sem
justificativa é item que será reaberto daqui a dois meses.

---

## 4. A triagem também PROMOVE — não só corta

Enquadrar como "o que abortar" perde o efeito inverso. Três itens saem de baixa urgência para
caminho crítico:

- **`adr-mcp-interception-single-border.md`, fase B2** (`mcpCall` nativo roteando por
  `mcp_server`) — é literalmente a costura C. Sem ela o n8n não pode ser domain server.
- **`adr-a2a-server-binding.md`, fase A2** (principal externo) — é o mesmo mecanismo que a costura
  B exige. **Avaliar fusão** em vez de dois esforços paralelos; o doc é explícito em não criar dois
  mecanismos de principal externo.
- **Usage Metering / integração metering × pricing** — o levantamento achou que `llm_tokens_*` **não
  é emitido** no `POST /v1/reason`, que é o caminho dos skill flows. Se alguma cota ou fatura lê essa
  dimensão, o item deixa de ser *deferred*.

---

## 5. Armadilha de sequenciamento — triar por função, não por pacote

Abortar por nome de componente erra. Exemplo concreto: *"abortar `workflow-api`"* parece seguro, mas
o step `collect` vive no perfil workflow e **é produtor de fronteira** (cria sessão-filho de
contato). Aborta-se o motor de workflow; não se aborta o `collect`.

Antes de mandar qualquer item para **Aborta**, responder: *isso produz ou consome fronteira? isso é
pré-requisito de alguma fase do §11?* Se sim para qualquer uma, o balde certo é outro.

Segunda armadilha: **não abortar agora o que uma fase posterior vai precisar.** As fases 4 e 5
dependem de coisas construídas nas fases 0–3.

---

## 6. Hipóteses a testar (não são conclusões)

Levantadas sem leitura completa do `TODO.md`. Confirmar ou derrubar:

| Item | Hipótese |
|---|---|
| Revisão do editor de diálogos | **Sobe** de prioridade — o editor de DialogForm sobrevive e ganha importância (§5.2 do doc) |
| Record/Replay Harness | **Reexaminar** — fica mais difícil com runtime externo, e mais valioso como gate de contrato |
| Refinamentos de Outbound (`responded` por-delivery, auto-alimentar mailing) | **Congela** — o drain/pacing é candidato a sair |
| Cliente 360 (H3, HJ, C1a/C1b, H5) | **Segue** — fosso puro |
| Journey J4 (N3 no drill), J5 | **Segue** — fosso puro |
| Audit LGPD fases 2–5 | **Segue**, e o n8n **aumenta** a urgência (retenção de PII fora do regime de masking) |
| Quality Ingest, concerns abertos | **Segue** — e o §5.3 do doc encosta nele (grau-transcript) |
| Arc 15 WebRTC / bridge PSTN | **Segue** — canal é fosso |
| Customer Surveys S1–S10 | **Segue com atenção** — o instrumento é DialogForm, que sobrevive |

---

## 7. Entregável esperado

1. Tabela de triagem completa: item → balde → justificativa de uma linha.
2. `TODO.md` atualizado — itens abortados **saem** (a casa do concluído é o CHANGELOG; a casa do
   abortado é uma nota curta com a razão, não uma seção mantida viva).
3. Lista curta de itens **promovidos**, com a nova dependência explicitada.
4. Se a triagem revelar conflito com o doc da linha mestra, **corrigir o doc** — a triagem pode
   descobrir que a especificação pediu a coisa errada, e isso é resultado válido.

---

## 8. Regras de método para a sessão

- **Nada de veredicto por prosa.** Antes de abortar um item alegando que "o n8n cobre", confirmar no
  código o que o item realmente faz. Doc que descreve config não é a config.
- **Desconfiar do item que parece razoável**, não do que parece errado.
- Se um item não puder ser classificado sem medir algo, o balde é **Congela** com a medição nomeada
  — nunca *Aborta* por conveniência.
- `/compact` ao fechar cada bloco de itens; não esperar estourar.

---

## Referências

- `docs/product/n8n-interop-boundaries-and-seams.md` — a linha mestra
- `TODO.md` § "Interop com n8n — alvo: eliminar o editor de fluxo local"
- `docs/adr/adr-mcp-interception-single-border.md` · `docs/adr/adr-a2a-server-binding.md`
- `docs/adr/adr-dialog-conditional-skip-logic.md` — a guarda que passa a ser load-bearing
