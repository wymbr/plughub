# Passagem — V3 do arco ALLOWLIST (mapa do ContextStore + aliases + modo auditoria)

> **Uso:** este documento é o prompt de abertura da sessão nova. Cole-o inteiro, ou abra a sessão
> apontando para ele. Sessão **nova** (não `/compact`): a V3 é outro arco, com outra superfície de
> medição — ver § *Por que sessão nova* no fim.

---

## A tarefa

Implementar a **V3** de [`docs/adr/adr-contextstore-allowlist.md`](../adr/adr-contextstore-allowlist.md):

> **V3** — Mapa do ContextStore (D2) + aliases contados (D3) + **modo auditoria** (só registra o que
> teria sido escondido/recusado).

**O ADR está Aceito e a decisão está fechada.** V0 *(metade)* · V1 · V1b · V2 · V2b entregues. A V3
é a próxima e é **reversível**; a V4 (inverter para deny-by-default) **não é**, e não é esta tarefa.

### O que a V3 entrega, e o que ela NÃO entrega

A V3 entrega **medição**, não fechamento. O modo auditoria existe para **produzir a lista real** que
a V4 vai usar — ele só registra o que *teria* sido escondido ou recusado, sem esconder nada. Entrar
achando que a V3 "liga a allowlist" é o modo mais fácil de estragá-la.

---

## O que já está MEDIDO — não re-medir, mas conferir por grep se algo destoar

- **O catálogo de tipos existe e é único** (`masking.types` no config-api, 10 tipos: `cpf`,
  `credit_card`, `phone`, `email_addr`, `address`, `health`, `financial`, `credential`, `card_cvv`,
  `opaque`). `DataTypeSchema` em `packages/schemas/src/audit.ts`. O mapa da D2 referencia esses
  `tipo:` — não invente um segundo vocabulário.
- **`DataType.declared_only`** marca tipo alcançável só por declaração (hoje `credential`,
  `card_cvv`, `opaque`); o oráculo `verifyDataTypeCatalog` o respeita.
- **`LgpdClass`** = `pessoal | sensivel | financeiro | credencial | none | nao_classificado`.
- **A casa legada de display rule (`rule.{category}`) está FECHADA** (V2b) — zero escritores, zero
  leitores, zero chaves em todo o `platform_config`. Gate: `probe_legacy_display_rule_closed.sh`.
- **`default_unmatched_operator: "plain"`** segue sendo o deny-nothing que motiva o arco.
- **`session:{id}:meta` é String (JSON), não hash** — já custou um `WRONGTYPE` no ai-gateway.

## O que NÃO está medido, e é a PRIMEIRA coisa a fazer

**Recontar o denominador.** A §1.8 do ADR afirma **486** ocorrências a migrar. Medido em 2026-08-29:

| escopo | contagem |
|---|---|
| `@ctx.[a-z_]+\.[a-z_.]+` em `packages/` | **231** (53 arquivos) |
| idem, incluindo `infra/` (YAMLs de skill) | **326** |
| sítios de `hset` sobre ctx | **39** |

**Nenhuma bate com 486.** Ou o número envelheceu, ou foi medido por outro critério — provavelmente
incluindo literais `session.*`/`caller.*` fora de `@ctx.`. O mapa da D2 precisa do denominador certo:
herdar um número não conferido é começar pelo erro que o arco inteiro existe para corrigir.

**Segundo item não medido:** quais prefixos existem de fato no ContextStore vivo, por tenant. O mapa
é uma allowlist — declarar campo que ninguém escreve, ou omitir campo que alguém escreve, são os dois
modos de falha, e só o segundo é silencioso.

---

## Decisões já fechadas — não re-litigar

- **D2 — o escopo FICA no primeiro segmento** (`escopo.dominio.campo`). A alternativa (raiz por
  domínio, escopo no nó) foi **recusada com razão medida**: obrigaria todo escritor a ter o mapa
  carregado para saber em qual hash gravar, e os escritores estão em TS **e** em Python. Seria
  roteamento de **retenção de PII** dependente de config, na casa onde o `CLAUDE.md` já registra
  três causas empilhadas de leitura de config falhando — todas degradando para *"usa o default"*.
- **D3 — o alias resolve na BORDA**, antes de qualquer decisão de política; **só a canônica é
  armazenada**; e **cada resolução é CONTADA**. Sem contador é grafia permanente, não migração.
- **D4 — quatro políticas, um vocabulário** (W escrita · R-agente · R-humano · P persistência).
  Fundi-las é erro: o portão de namespace na persistência apagaria história.
- **A V4 exige que a omissão deixe de ser MUDA antes da inversão** — já entregue na V1.

---

## Armadilhas MEDIDAS nesta base (todas custaram tempo real)

**Ambiente e deploy**
- **Nenhum serviço monta o fonte.** Mudou código ⇒ `build` + `up -d`. `restart` roda a imagem antiga.
- **Confira o resultado do build.** Um `tail -4` escondeu um build que reprovou; o `up -d` subiu a
  imagem anterior e o gate saiu **VERDE** sobre código velho.
- **Preflight de CONTEÚDO, não de símbolo.** Conferir que o símbolo *existe* não prova que é o de
  agora. Compare com o **FONTE** — é o único lado que não pode estar atrás de si mesmo.
- **`config-seed` é serviço PRÓPRIO.** Construir `config-api` não o atualiza. E o seed é
  *seed-if-absent*: chave existente é pulada. Reaplicar exige
  `plughub-config-seed --only <ns>.<key> --overwrite`.
- **Para hooks/deploy/capacity/skills, pergunte à AUTORIDADE (agent-registry, dialog-api), nunca ao
  YAML** — é seed-if-absent e o DB vence.
- **`MenuStepSchema`/schemas mudou ⇒ rebuildar `agent-registry` + `skill-flow-service` +
  `mcp-server-plughub` JUNTOS**, senão o registry recusa com 422.
- **O bridge executa o SNAPSHOT DO SLOT**, não o `skill.flow`: migrar skill exige `set-next` +
  `promote`.

**Instrumentos**
- **Comentário entra na contagem.** Aconteceu **quatro vezes** nesta sequência: a prosa que documenta
  a forma antiga reproduz a string e o contador acusa a própria explicação. Exclua linha de comentário,
  sempre — e a exclusão é *load-bearing*, não higiene.
- **Bateria de mutação precisa AFIRMAR que a mutação entrou.** Duas mutações "não derrubaram nada"
  porque não aplicaram (`sed` quebrando em `||`). Sem essa asserção, a bateria mede a si mesma.
- **Cuidado com a proposição vizinha.** Um teste meu chamado *"catálogo VAZIO"* exercitava catálogo
  **AUSENTE** — o `!Array.isArray` já lançava e a cláusula testada nunca rodava. Só apareceu porque a
  mutação **aplicou e o verde continuou**.
- **Round-trip de JSON reformata arquivo alinhado à mão.** Duas vezes: 831 linhas de diff para
  acrescentar uma chave. Faça substituição textual e valide o JSON **antes** de gravar.
- **`kafka-console-consumer --from-beginning --max-messages N` lê as MAIS ANTIGAS.** Li um zero como
  "o produtor não publica" e persegui o lado errado por um ciclo.
- **`jq` não existe no Git Bash desta máquina**; os probes da casa rodam de dentro do WSL. Node no
  WSL exige carregar o nvm.

---

## Achados abertos e adjacentes (contexto, não escopo)

- 🔴 **Colocação da detecção de PII** — arco próprio, registrado no `TODO.md`. Um único call site
  (`mcp-server-plughub/src/tools/session.ts:472`), e o caminho da submissão de form não passa por ele.
- 🔴 **`session.ts:485`** — `catch` mudo entrega conteúdo **cru** quando a detecção falha. Único dos
  8 `catch` do arquivo que degrada para vazamento.
- 🔴 **Quantos pacotes não compilam é desconhecido.** O `mcp-server-plughub` estava quebrado desde
  `10bde79` e ninguém soube porque ninguém reconstruiu. Evidência só existe rodando
  `infra/scripts/rebuild-all.sh --wipe`.
- 🟡 **2 testes de `pools.test.ts`** vermelhos, pré-existentes (500 onde se espera 201).
- 🟡 **Proveniência da DETECÇÃO** (`masked_categories`) ainda não existe em `messages` — a T3 fechou
  só o lado da declaração (`masked_types`). **São CINCO camadas**, não quatro: schema · produtor ·
  parser · **ESCRITOR** (`_MESSAGE_COLS` + `_message_row`) · DDL. O escritor descarta chave extra em
  silêncio.
- 🟡 **Três casas** produzem o placeholder `••••••` (bridge · `webchat.py:839` ·
  `AgentAssistPage.tsx:398`). Consolidá-las é fase própria; o gate conta e reprova a quarta.
- 🟡 **`platform-ui` não tem test runner** — dívida registrada; por isso a T4 se apoia em probe de shell.

---

## Regras da tarefa

- **Meça antes de mudar**; arquivo:linha para cada asserção.
- **Para cada gate, escreva o que o faria ficar VERMELHO e verifique que fica, ANTES de corrigir.**
  Nesta base isso já pegou defeito em toda fase desta sequência, sem exceção.
- **Degradação nunca é silenciosa**, e em masking ela **recusa alto**: é a única política em que
  fallback mudo não é opção.
- **Exposição ≠ dano.** Conte as duas grandezas antes de chamar algo de defeito ou de inócuo.
- Achado fora do escopo vai para o `TODO.md`, não para o diff.
- Ao concluir: `CHANGELOG.md` + fases no ADR + índice do `CLAUDE.md`.

---

## Estado do repositório

Branch `seguranca/autorizacao-e-irrestrito-explicito`, árvore limpa, tudo commitado e pushed.

Gates verdes do arco (rodar de dentro do WSL):
`probe_type_catalog.sh` · `probe_masked_type_provenance.sh` · `probe_masking_display_parity.sh` ·
`probe_legacy_display_rule_closed.sh` · `probe_i18n_duplicate_keys.sh` ·
`q_masked_declaration_census.sh` *(censo, não gate — devolve NÚMERO)*.

**Decisões tomadas e não iniciadas:**
- **S1 do `adr-deploy-time-content-snapshot` = caminho A (pin de versão)**, prioridade baixa e medida
  (dano zero hoje; fazer antes de o ADR da árvore entrar). A supersessão da F0b foi **revertida**.
- **T7-B do `adr-masked-typed-declaration`** (remover a tolerância do runtime a `masked: true`) —
  bloqueada por decisão do dono; recomendação registrada: **adiar indefinidamente**.

---

## Por que sessão nova, e não `/compact`

A V3 atravessa **326 referências de tag em 8 pacotes**, TS e Python. O arco anterior tocou ~10
arquivos e um vocabulário só — o contexto que sobra dele (declaração de `masked`, `form_get`, redação
no bridge) **quase não serve** para namespaces de tag, prefixo roteando hash e TTL, e aliases.

O que serve já está escrito: `CLAUDE.md`, os probes, o `CHANGELOG` e este documento. Uma sessão nova
lê isso melhor do que herda um resumo — e devolve o teto inteiro de 200k para um arco desse tamanho.
