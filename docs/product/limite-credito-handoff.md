# Handoff — cenário de aumento de limite (sessão de 2026-08-11)

> Cole isto no início da próxima sessão. Objetivo: retomar sem redescobrir o que já foi medido.

## Contexto em três frases

Estamos preparando uma **demo de 30 min + 30 min de discussão** para plateia **técnica**
(arquitetos/devs). O roteiro está em [`demo-roteiro-30min.md`](demo-roteiro-30min.md), montado sobre
`retencao_humano` (contato único) e **portabilidade** (journey). Construímos em paralelo um cenário
novo — **aumento de limite de crédito** — que materializa o modelo de 3 níveis e é candidato a
substituir a portabilidade no Bloco B.

## Estado: Fase 1 COMPLETA e validada

`infra/test/smoke_limite_tres_acessos.sh` → **16/0**. Percurso ao vivo no webchat + Console
confirmado: formulário multi-campo com CVV mascarado, menu de status com cartão `***4444`, e
entrega do resultado com o valor **editado pelo aprovador** chegando ao cliente.

Desenho completo, decisões e achados: [`limite-credito-3-niveis-design.md`](limite-credito-3-niveis-design.md).
**Leia §11 antes de mexer em qualquer coisa** — é onde estão os seis defeitos encontrados e por que
cada asserção do smoke existe.

Artefatos: skills `skill_limite_{entrada,processo,entrega,retorno}_v1` · pools `limite_ia`,
`limite_processo`, `limite_entrega`, `limite_retorno`, `aprovacao_credito` · forms
`dialog_limite_{solicitacao,aprovacao}` · smoke + `seed_dialog_limite_forms.sh` +
`probe_flow_transitions.sh`.

## Próximos passos, em ordem

**0. `CHANGELOG.md` está pendente.** A convenção do projeto (CLAUDE.md § "Regra de persistência")
exige entrada no CHANGELOG para implementação concluída, e o doc correspondente já existe. É a
primeira coisa a fazer amanhã.

**1. Limpar markdown das mensagens ao cliente** (~5 min, sem risco). O `webchat-test.html` **não
renderiza markdown**: `**negrito**` aparece com asteriscos literais e `\n\n` é colapsado. Na tela da
demo lê como descuido. Tirar os `**` dos `notify`/`menu` dos três skills de cliente.

**2. Conceder ABAC ao operator.** Hoje **só `admin@plughub.local`** tem `approvals.operacao` +
`approvals.decide` (`seed_auth.py:230-233`). E admin está em `supervisor_roles` ⇒ **vê tudo em
claro**. Sem conceder ao `operator@plughub.local` em Configuration › Access, **a cena de
mascaramento não existe na demo**.

**2b. Observabilidade: o Workflow trace ficou pela metade** — ✅ **RESOLVIDO (2026-08-12): não era
defeito.** Antes de separar a entrega, `/analise/sessions` mostrava **7 execuções**; depois, **3**.
Causa: o Workflow trace é **session-scoped**, e metade da história mudou de sessão de propósito
(§11 do design doc — foi o que consertou o re-enfileiramento na fila pull).

`infra/test/probe_journey_limite.sh` (novo, **5/0**) mediu: as três sessões (intake → análise →
entrega) formam **UMA journey**, raiz = o `session_id` do intake, `spawn_reason='trigger'` na
aresta. A herança de raiz é transitiva por construção — `handle_trigger` lê
`session.root_session_id` do ctx do CHAMADOR e sempre semeia a tag na sessão nova, então "workflow
disparando workflow" nunca foi caso especial.

**Consequência para a demo:** a leitura ponta-a-ponta é a **Vista Processos**, não a de Sessões.
⚠️ Ao procurar a journey, **não filtre por pool `limite_processo`** — a sessão da análise sai com
`pool_id = aprovacao_credito` (o delegate ao pool humano reescreve a linha no `ReplacingMergeTree`).
Busque pela raiz, ou por `limite_ia`/`limite_entrega`.

*Refinamento opcional, não bloqueante:* dar ao Workflow trace um link "ver processo completo".

**3. Decidir a journey do roteiro.** Trocar o Bloco B (8 min, hoje portabilidade) pelo aumento de
limite? A favor: masking por política, aprovação humana em fila pull, três acessos, tudo testado.
Contra: nasceu ontem; a portabilidade já foi ensaiada. Se trocar, reescrever o Bloco B do roteiro.

**4. Itens do roteiro que continuam abertos** (ver `demo-roteiro-30min.md` §4): datas dos seeds de
analytics são **hardcoded em junho/julho de 2026** (hoje é agosto) e não há gerador de volume —
com N=1 as lentes funcionam mas não impressionam.

## Rituais de deploy — a fonte de metade dos ciclos perdidos ontem

São **três operações distintas** e confundi-las custou duas rodadas:

| Situação | Comando |
|---|---|
| Skill/pool **inédito** | `docker compose -f docker-compose.demo.yml restart orchestrator-bridge` (seed-if-absent; `infra/registry` e `packages/skill-flow-engine/skills` são volumes montados) |
| Skill **já existente** editado | `REGISTRY_SYNC_RECONCILE=true docker compose … up -d orchestrator-bridge` — republica o `skill.flow`. **NÃO promove.** |
| O que o bridge **executa** | `PUT /v1/pools/{p}/slots/next` + `POST /v1/pools/{p}/promote` (header `x-service-token`, `config_json` COMPLETO) |
| DialogForm editado | `bash infra/test/seed_dialog_limite_forms.sh` (POST cria versão nova; PUT como fallback) |
| Código Python/TS | `build` + `up -d` — **nenhum serviço monta o fonte** |
| `webchat-test.html` | `build agent-assist-ui` (é `COPY` no Dockerfile) + Ctrl+Shift+R |

⚠️ `masking.context_rules` é seed-if-absent **por chave**: as 3 regras novas do `seed.py` **não
entram** em base já semeada. Em `/config/masking`, role `operator`: `session.numero_cartao`→`last_4`,
`session.cpf_titular`→`last_2`, `session.limite_solicitado`→`financial`. E **não** ponha o catch-all
de operator em `hidden` — derruba `session.dialog_form_id`/`decisions` e a tela de aprovação some.

## Armadilhas medidas ontem (não redescobrir)

- **`delegate.context` é namespace compartilhado.** Chaves `phone`/`email`/`cpf`/`princ` viram
  **âncoras de identidade**, não campos de tela. Por isso a tag é `cpf_titular`.
- **Transição para step inexistente PARA o workflow em silêncio.** `validateFlow` não valida alvos.
  Rodar `bash infra/test/probe_flow_transitions.sh` depois de renomear qualquer step.
- **Num pacote de aprovação, campo lido pelo workflow tem de ser `field`, não `question`.** O
  `ApprovalPanel` monta `payload.edits` a partir de `fields[]`; question nodes são decorativos.
- **`{t}:session:{id}:status` mente** para toda workflow encerrada (diz `active`). Medir fechamento
  em `analytics.sessions`, não nessa chave.
- **Delegate a pool humano deve ser o ÚLTIMO ato da sessão.** Continuar o processo depois? Dispare
  outro processo — senão o item volta para a fila pull na primeira queda de WS.
- **Máscara e ênfase disputam o asterisco.** Valor vindo de `context_preview` já tem `***`; não
  envolver em `**negrito**`.
- **Um telefone por ensaio.** O índice de pendências é chaveado por sessão e só a mais recente é
  lida — reusar o número esconde os pedidos anteriores.

## Comandos de verificação

```bash
bash infra/test/smoke_limite_tres_acessos.sh     # 16/0 esperado
bash infra/test/probe_flow_transitions.sh        # alvos de transição órfãos (todos os skills)
bash infra/test/smoke_approval_segment_closes.sh # referência: aprovação fecha segmento (7/0)
```

Cliente webchat: `http://localhost:5173/webchat-test.html` → pool `limite_ia`.
Console: `http://localhost:5174/console` → pool `aprovacao_credito`.
