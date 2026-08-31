# ADR: DELETE de DialogForm — arquivamento reversível, com purga só do nunca-publicado

**Status:** **Aceito + implementado (2026-08-28)** — as sete decisões fechadas pelo dono e as fases
F1–F5 entregues no mesmo dia. Gate `infra/test/probe_dialog_form_delete.sh` (9 falhas antes do
build → VERDE depois). Ver `CHANGELOG.md`.
**Data:** 2026-08-28
**Componentes:** `packages/dialog-api` (`db.py` — coluna + leitura; `router.py` — rotas),
`packages/platform-ui` (`modules/dialog-forms/DialogFormsPage.tsx`, `api/dialog-hooks.ts`),
`packages/channel-gateway` (`survey_web.py` — `survey_link_create`), `infra/seed/seed_dialog.py`,
`infra/test/probe_dialog_form_delete.sh` (novo).
**Relacionado:** [`adr-otp-workflow-and-dialog-primitive.md`](adr-otp-workflow-and-dialog-primitive.md)
(as 4 costuras; conteúdo × controle), [`adr-dialog-conditional-skip-logic.md`](adr-dialog-conditional-skip-logic.md),
[`adr-survey-form-scoring-composition.md`](adr-survey-form-scoring-composition.md) (composição no `survey_record`),
`TODO.md` § "`dialog-api` não tem rota DELETE", `CLAUDE.md` § Dialog Primitive.

---

## Contexto

A `dialog-api` não tem rota `DELETE`. Rotas medidas em 2026-08-27: `POST ""`, `GET ""`, `GET /{id}`,
`PUT /{id}`, `POST /{id}/publish` — e nada mais. Consequência prática: **todo DialogForm criado é
permanente**, e qualquer script que crie form precisa limpar pelo Postgres (o
`probe_config_service_write_gate.sh` faz isso, e diz por quê no comentário — um probe que suja o
ambiente que mede acaba medindo a própria sujeira).

A rota não foi escrita porque faltava a decisão: **o que acontece quando se apaga um form publicado que
um skill em execução referencia.** Hoje isso derrubaria a superfície de um contato em andamento
(`form_get` → 404 → `on_failure`).

### Inventário medido — são SEIS leitores, e todos resolvem `?status=published`

| # | Leitor | Momento da leitura | Efeito hoje se o form some |
|---|---|---|---|
| 1 | `form_get` — `mcp-server-plughub/src/tools/dialog.ts:230` | **início** do diálogo | `on_failure` (ex.: `agente_nps_v1.yaml:68` encerra sem pesquisa) |
| 2 | `survey_record` — `tools/survey.ts:161` | **fim** — compõe a nota | perguntas já respondidas, composição falha ⇒ resposta perdida |
| 3 | `segment_outcome_record` — `tools/segment.ts:81` | **fim** — deriva eventos Arc 12 | degradação já honesta (`null` + `warn`); perde captura, mantém `outcome`/prosa |
| 4 | `survey_link_create` — `channel-gateway/survey_web.py:556` | **só na criação** do link (congela o form no token) | página e submit são **imunes** (leem `rec["form"]`) |
| 5 | `DialogFormRenderer.tsx:230` (Console) | ao abrir o painel | item reivindicado, painel **vazio** |
| 6 | `WebhookSegmentDetail.tsx:257` | **retrospectivo**, segmento já fechado | dicionário de rótulos vazio ⇒ histórico com chaves cruas, para sempre |

Três fatos saem daí e decidem este ADR:

- **A janela de risco vai até o submit, não até o `carregar_form`** — dois dos seis leem no FIM.
- **Hard delete degrada história encerrada** (#6), não só contato vivo. Esse dano não tem janela: é permanente.
- **O seed ressuscita.** `seed_dialog.published_version()` trata `404` em `GET ?status=published` como
  AUSENTE e faz `POST` + `publish`. Com a leitura fechada, todo boot recria o form apagado — um delete
  que se desfaz sozinho, em silêncio.

### O erro de enquadramento que a medição desfez

"Soft-delete" responde **"dá para recuperar?"** — eixo **armazenamento**. Não responde **"o contato em
andamento cai?"** — eixo **leitura**. São dois fatos, e a segunda pergunta é a que motivou a decisão:
escolher soft-delete e manter `404` na resolução quebra os seis leitores **exatamente como o hard delete**,
com a diferença de que agora existe um backup que ninguém consulta. As decisões abaixo separam os dois eixos.

## Decisões

### D1 — `GET /{form_id}` continua servindo o form arquivado (com `deleted_at` no corpo)

O catálogo fecha; a **resolução por id explícito** não. Justificativa: ninguém *descobre* um form por id —
quem chama `GET /{form_id}` já tem um vínculo (skill em execução, `config_json` do slot,
`session.dialog_form_id` no ctx, segmento histórico). Fechar essa porta não impede uso novo; impede a
**continuação** e a **leitura do passado**.

Portanto:

- `GET /` (catálogo) — **esconde** arquivados; `?include_deleted=true` para a lixeira do editor.
- `GET /{form_id}` — **serve**, carregando `deleted_at` para que o chamador possa logar.

Cai de graça: o contato em andamento termina, `survey_record` compõe, `segment_outcome_record` mantém a
captura, o Console renderiza, o histórico do #6 mantém os rótulos, e o seed vê `200` e **não ressuscita**
(sem tocar no seed). O editor lê pela mesma rota sem `status` (`dialog-hooks.ts:189`), então a tela de
restauração sai de graça também.

### D2 — Form que nunca teve versão publicada é PURGADO de verdade; a tela avisa antes

Os seis leitores resolvem `status=published`. Logo, um form que nunca publicou **não pode estar vinculado
a nada** — isso é demonstrável, não presumido. Nesse caso o `DELETE` remove as linhas.

Isso põe dois regimes atrás de um verbo, e a mitigação é obrigatória, não opcional: **a tela avisa que o
caso é irreversível antes de confirmar**, e a resposta declara qual dos dois aconteceu (`purged: true|false`).
Fecha o motivo original do item (`TODO.md`): o probe deixa de apagar pelo Postgres e não deixa resíduo.

### D3 — `POST`/`PUT`/`publish` sobre form arquivado → **409**; restauro é rota própria

Nota de fato, que corrige o enquadramento comum: hoje **não existe** "criar form" distinto de "adicionar
versão" — `POST` insere `max(version)+1` como draft, exista o `form_id` ou não. A pergunta real é
*escrever num form arquivado ressuscita?* — e a resposta é **não**.

O caso ruim que o `409` evita: conteúdo novo herdando um id ao qual um slot antigo ainda aponta, fazendo o
slot executar outra coisa **sem ninguém ter tocado no deploy**. Restaurar é ato deliberado, com rota
própria — `POST /v1/dialog/forms/{form_id}/undelete` —, não efeito colateral de salvar. Rota própria
(em vez de flag no `DELETE`) para aparecer no OpenAPI, ser auditável como ato, e não fazer `DELETE`
significar duas coisas.

### D4 — Recusa é só de quem cria vínculo NOVO, nunca de quem continua um

Com D1, "recusar arquivado" deixa de ser global e passa a ser pontual. O enunciado — que é o que envelhece
bem, não a lista — é: **quem passa um `form_id` que não veio de um vínculo existente verifica.**

Única implementação hoje: `survey_link_create` (`survey_web.py:556`), que **congela** o form num token.
Atenção à regressão silenciosa: hoje ele já falha (`raise_for_status` no 404); com D1 ele passaria a
**suceder** e congelar um form arquivado se ninguém puser a checagem.

O combo de deploy (`AgentFlowDeployPage.tsx:125`) fica correto sozinho — usa a lista.

### D5 — O delete é do `form_id` (todas as versões), não de uma versão

Todo consumidor vincula por `form_id` e resolve "maior publicada". Deletar versão individual não responde
pergunta que alguém tenha; a pergunta que se parece com essa é **despublicar**, que é outra operação
(voltar `status` a `draft`) e hoje não existe. Misturar as duas é o caso clássico do campo cujo rótulo
tem "e".

Implementação: `deleted_at TIMESTAMPTZ NULL` **por linha**, carimbado em todas as versões do `form_id` no
delete e limpo em todas no undelete. A coluna fica por linha só para não exigir outra migração se um dia
existir aposentadoria por versão — mas o **verbo de hoje é do form**.

### D6 — `DIALOG_SEED_RECONCILE=true` limpa `deleted_at`; o log do seed diz "arquivado"

No boot normal, D1 já basta: `published_version()` recebe `200`, decide "existe" e não ressuscita. No modo
reconcile a regra da casa é *o arquivo vence* — então ele limpa `deleted_at`. Um form que o arquivo declara
e que continua arquivado seria um meio-termo que não corresponde a nenhuma das duas fontes.

E o pulo tem de ser **nomeado**: quando o seed encontra um form arquivado, o log diz **arquivado**, nunca
"já publicado, não toco". Motivo de método: decisão que não diz o motivo é a família que a § Postura de
Engenharia manda caçar — e o seed foi escrito justamente para que nenhum caminho fosse mudo.

### D7 — Na tela é **arquivar**, não "apagar"

Com D1, "apagado" faria a tela mentir: o form continua atendendo quem já está vinculado. O texto diz o que
de fato acontece — *sai do catálogo e de vínculos novos; quem já está vinculado continua até alguém trocar
o deploy*. A rota HTTP segue sendo `DELETE`; o que muda é a palavra que o operador lê.

## Consequências aceitas

- **"Arquivado" ≠ "inalcançável".** Um deploy que aponte para um form arquivado continua executando-o
  indefinidamente. É comportamento decidido, e é exatamente por isso que D7 proíbe a palavra "apagado".
- **Dois regimes atrás de um verbo** (purga × arquivamento). Aceito sob a condição de D2: aviso antes e
  `purged` na resposta.
- **Linhas arquivadas acumulam.** São linhas de config, pequenas, e ficam fora do catálogo. Não há
  expurgo por idade no v1 — inventá-lo agora seria política de retenção sem requisito.

## O que NÃO fazer

- **Não usar o `DELETE` para despublicar.** São operações diferentes (D5); a segunda ainda não existe.
- **Não deixar `POST`/`PUT` ressuscitar** (D3).
- **Não construir checagem de "referência viva" cross-service** (`dialog-api` → `agent-registry`). Foi
  considerada e recusada: o `form_id` literal mora **dentro do flow do snapshot do slot**, não num campo
  estruturado, então a checagem seria incompleta por construção; e o ramo de degradação (registry
  inalcançável ⇒ libera ou barra?) decidiria sozinho se o portão é real ou decorativo. A parte **decidível**
  dessa ideia é o que virou D2.

## Fases

| Fase | Escopo | Fecha |
|---|---|---|
| **F1** | `db.py`: coluna `deleted_at` + carimbo/limpeza por `form_id` + filtro no `db_list_forms` + `deleted_at` no `_row_to_form`. `router.py`: `DELETE /{form_id}` (com o ramo de purga), `POST /{form_id}/undelete`, `?include_deleted` na lista, `409` em `POST`/`PUT`/`publish` de arquivado. Portão de escrita = o mesmo dual já existente (`config.dialog_forms`) | D1, D2 (backend), D3, D5 |
| **F2** | platform-ui: ação de arquivar, lixeira (`include_deleted`), restaurar, **aviso pré-purga**, rótulos i18n (`en` + `pt-BR`) | D2 (tela), D7 |
| **F3** | `survey_web.create` recusa form arquivado | D4 |
| **F4** | `seed_dialog.py`: log "arquivado" + `RECONCILE` limpando `deleted_at` | D6 |
| **F5** | `infra/test/probe_dialog_form_delete.sh` + linha em `infra/test/gates.manifest` | — |

**Emenda da F2 (2026-08-28):** a tela não conseguia avisar direito com o que a lista devolvia —
`ever_published` **não é derivável do `status`** (a última versão pode ser rascunho e existir uma
publicada mais antiga, caso em que o form NÃO pode ser purgado). A lista passou a carregar o campo
(`EXISTS (…status='published')`), e a ação de arquivar fica **desabilitada** enquanto ele não
estiver à mão — supor o caso reversível é o palpite confortável, e o errado num ato irreversível.

## Gate — o que o faria ficar vermelho

`probe_dialog_form_delete.sh`, re-executável, sem contato real:

1. **Testemunha negativa (a asserção que protege o contato):** form publicado + arquivado ⇒
   `GET /{id}?status=published` devolve **`200` com `deleted_at` preenchido**. Um probe que só verificasse
   "sumiu da lista" ficaria verde igual num hard delete — é esta asserção que separa os dois.
2. Arquivado **não** aparece em `GET /` e **aparece** em `GET /?include_deleted=true`.
3. Form **nunca publicado** + `DELETE` ⇒ `404` depois, e resposta com `purged: true`.
4. `POST`/`PUT`/`publish` sobre arquivado ⇒ `409`; após `undelete`, ⇒ `200`.
5. **Testemunha de presença:** um form vivo, não tocado, continua `200` na lista e na resolução — senão um
   backend que respondesse `404` a tudo passaria nos itens 2 e 3.


---

## Apêndice — resumo denso migrado do índice do `CLAUDE.md` (2026-08-31)

> Este bloco vivia como **uma linha** do índice `docs/` no `CLAUDE.md`, onde ocupava 2015 bytes.
> Medido antes de mover: **~85% do seu vocabulário já existe neste ADR** — ele é uma condensação
> independente, não uma cópia, e por isso os ~15% restantes (achados, números e nomes de arquivo que
> só foram registrados no índice) **não existiam em lugar nenhum além dali**. Movido inteiro, sem
> resumir, porque a alternativa — cortar no CLAUDE.md e confiar que o ADR já dizia tudo — perderia
> exatamente a fração que não dá para recuperar.
>
> **É trabalho aberto**, não documentação final: a fração nova deve ser dobrada no corpo do ADR e
> este apêndice, encolhido. Enquanto isso não acontece, ele é a única cópia.

`DELETE` de DialogForm: **arquivar** (reversível) e não apagar. A medição separou dois eixos que "soft-delete" funde: ARMAZENAMENTO (*"dá para recuperar?"*) × **LEITURA** (*"o contato em andamento cai?"*) — soft-delete com `404` na resolução quebraria igual ao hard delete. **D1: o catálogo fecha, `GET /{form_id}` continua servindo** (com `deleted_at`), porque ninguém DESCOBRE form por id: quem chama já tem vínculo. São **SEIS** leitores, todos por `?status=published`; **dois leem no FIM** do diálogo (`survey_record`, `segment_outcome_record` ⇒ a janela de risco vai até o submit, não até o `carregar_form`) e **um lê história encerrada** (`WebhookSegmentDetail`, dano sem janela). **D2: purga real só do nunca-publicado** — é a única parte DECIDÍVEL de "recusar quando há referência viva" (o `form_id` literal mora dentro do flow do snapshot do slot, então checagem cross-service seria incompleta por construção), com aviso de irreversibilidade na tela. D3 `409` em escrita sobre arquivado + `undelete` próprio (ressuscitar implícito faria slot antigo executar conteúdo novo sem ninguém tocar no deploy) · D4 recusa só de quem cria vínculo NOVO (`survey_link_create`) · D5 delete é do `form_id`, nunca da versão (despublicar é outra operação) · D6 `RECONCILE` limpa `deleted_at`, log diz *arquivado* · D7 a tela diz **arquivar**. Achado de tabela: `seed_dialog.published_version()` trata `404` como AUSENTE ⇒ com leitura fechada, **todo boot ressuscitaria o form apagado**. Emenda medida na UI: **`ever_published` não é derivável do `status` da lista** (última versão pode ser rascunho com uma publicada mais antiga), então a lista o carrega e o botão de arquivar fica DESABILITADO sem ele — supor o caso reversível é o palpite confortável e o errado num ato irreversível. Fases F1–F5 e gate `probe_dialog_form_delete.sh` (9 falhas antes do build → verde depois) — **Aceito + implementado 2026-08-28**
