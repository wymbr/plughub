---
name: plughub-review
description: Checklist de revisão de código do PlugHub — o que conferir num diff, commit, branch ou PR deste repositório além de bugs genéricos. Use ao revisar, auditar ou dar parecer sobre mudanças (diff, git diff, commit, branch, PR, "revise", "review", "o que acha desta mudança"), ao preparar um commit para conferir antes de fechar, e junto de /code-review ou /security-review quando o alvo é este repositório. Traz um scanner das linhas adicionadas (except pass, catch vazio, export *, xadd direto, hex inline, alias ClickHouse, None/?? 0, escopo vazio) e a tabela de gates a rodar por tipo de mudança.
---

# plughub-review — revisar é perguntar o que o diff ACRESCENTA

Três regras de postura antes do checklist:
- **Revise o que o diff acrescenta, não o repositório.** Há dívida antiga medida (175
  `except …: pass` e 520 hex inline em `.tsx`, em 2026-09-16); reportá-la como achado novo
  afoga o que importa.
- **Todo achado vem com cenário de falha concreto** — entrada ou estado → saída errada. Sem
  cenário é opinião; com cenário, é verificável.
- **Verifique antes de reportar.** Afirmação de memória ou de docstring não é evidência: o
  repositório tem casos de comentário que promete invariante sem mecanismo.

## 1. Delimite o alvo

- Há **sessões paralelas na mesma árvore**. `git status --short` mostra o que é de quem; revise
  só os caminhos do trabalho em questão (`--paths`), ou o range de commits (`--base`).
- Range típico: branch contra `main` → `--base main`; commit único → `git diff X^ X | … --stdin`.

## 2. Rode o scanner

```bash
wsl.exe -d ubuntu -- bash -lc 'cd /home/a1/projects/plughub && python3 .claude/skills/plughub-review/scripts/scan_diff.py --base main'
```

Variações: sem `--base` (árvore contra `HEAD`, inclui arquivo novo não rastreado) · `--paths a b`
· `--stdin` · `--selftest` (planta cada violação e exige que seja pega; rode se editar o script).

| Nível | Regras | O que fazer |
|---|---|---|
| **ERRO** | `except-pass` · `catch-empty` (produção) · `export-star` · `xadd-direct` (mcp-server) · `inline-hex` (platform-ui) · `ch-alias-shadow` · `gate-sem-classe` | regra objetiva do `CLAUDE.md`; não entra assim |
| **ATENÇÃO** | `is-none` (perto de leitura de Redis/config/env) · `zero-default` · `scope-empty` · `kafka-no-key` · `jsx-literal` · `i18n-uma-locale` · `schema-mudou` | exige julgamento — confirme lendo o contexto; `dict.get` seguido de `is None` é legítimo |

Saída `0` sem ERRO · `1` com ERRO · `2` INCONCLUSIVO (diff vazio, ref inválido). **Diff vazio
não é "limpo"** — confira a base. O scanner cobre só padrões de LINHA; o resto é leitura.

## 3. Leia por preocupação — e carregue a skill do assunto

| O diff toca… | Pergunta central | Skill |
|---|---|---|
| teste, probe, gate, mock, asyncio | o que faria este teste ficar vermelho? | `testing-pattern` |
| ClickHouse, Kafka, uuid5, relatório | qual linha sobrevive, e em que ordem os eventos chegam? | `data-engineering` |
| rota, JWT/ABAC, escopo, chamador interno, tool MCP | quem mais responde a esta pergunta de acesso? | `security-boundaries` |
| Dockerfile, compose, skill YAML, registry, schemas | o processo vivo vai rodar isto? | `deployment` |
| `pending.md`, `done.md`, `CHANGELOG.md`, commit | a ficha foi movida e a entrega documentada? | `task-ledger` |

Perguntas que valem para qualquer diff:
1. **Degradação é barulhenta?** Todo fallback loga por que degradou; nenhum default fabrica valor
   plausível (`None` = não medido).
2. **Presença ≠ conteúdo?** "Foi escrito" não é "mudou"; comparar conteúdo, não existência.
3. **Existe uma segunda casa para o mesmo fato?** Duas implementações, dois escritores da mesma
   chave, duas rotas para o mesmo dado — a mais permissiva é a que vale.
4. **Comentário novo promete invariante?** Só com mecanismo que a imponha, senão é defeito.
5. **Escopo do fato:** segmento × sessão × journey; pool de ENTRADA × pool que ATENDE.
6. **Endereçamento:** dispara por POOL, nunca por `skill_id`.

## 4. Contratos que atravessam fronteiras

Nenhum `grep` num arquivo só alcança estes; confira os dois lados:
- **Schema Zod** mudou em `@plughub/schemas` → consumidores atualizados e rebuild listado; tipo
  **nunca** redefinido localmente noutro pacote.
- **Tópico Kafka novo** → schema Zod + linha nas tabelas *Kafka Topics* e *Kafka Event Schemas*.
- **Payload** → a chave é medida no LEITOR, não no produtor.
- **Texto visível** → `t()` + chave em `en/` **e** `pt-BR/`; nada de título traduzido gravado em store.
- **Identificador técnico** em inglês (rota, variável, chave i18n, tópico); português só em valor
  de i18n e ID de entidade do tenant.
- **Afirmação do `CLAUDE.md`** que o diff torna falsa → corrigida no mesmo commit.

## 5. Gates por tipo de mudança

Rode de dentro do WSL (`bash infra/test/<gate>.sh`). Probe ao vivo mede a IMAGEM: sem `build` +
`up -d`, mede o código antigo.

| Mudou | Gate |
|---|---|
| locale JSON | `probe_i18n_duplicate_keys.sh` |
| rota da analytics-api / relatório | `probe_route_credential_coverage.sh` · `probe_report_row_scope.sh` |
| verificador JWT/ABAC | `probe_authz_single_verifier.sh` |
| prefixo HTTP do channel-gateway | `probe_edge_surface.sh` |
| método chamado em adapter (mock) | `probe_adapter_self_calls.sh` |
| payload de menu/formulário | `probe_menu_result_contract.sh` · `probe_masked_field_echo_parity.sh` |
| leitura de fila (ZSET) | `probe_queue_window_order.sh` |
| steps de skill / perfil de pool | `probe_skill_profile_steps.sh` |
| tag de ContextStore | `probe_contextstore_cadastro.sh` |
| config de negócio / env | `python3 infra/check_config_invariants.py` |
| Dockerfile Python / suíte | `probe_python_suites.sh` |
| `.sh` novo em `infra/test/` | `probe_gates_manifest_coverage.sh` |
| `pending.md` / `done.md` | `probe_task_ledger.sh` |

## 6. Relate

- Um achado por linha: **severidade · `arquivo:linha` · o defeito em uma frase · cenário de falha
  concreto**. Se o host pede `ReportFindings`, use-o; senão, lista ordenada do mais grave.
- Separe **novo neste diff** de **dívida pré-existente vista de passagem** (esta vira ficha, não
  bloqueio — skill `task-ledger`).
- Diga o que **não** foi verificado (gate não rodado, imagem não rebuildada, stack fora).
- Achado que se desfez na verificação não entra — mas, se a suspeita era razoável, diga em uma
  linha por que não é defeito, para a próxima revisão não refazer a conta.
