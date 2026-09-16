---
name: testing-pattern
description: Método de teste do PlugHub — como desenhar, falsear, rodar e registrar testes, probes e gates sem produzir verde que não pode reprovar. Use ao escrever ou revisar teste (pytest, vitest, jest), probe ou gate em infra/test/ (probe_*, gate_*, mut_*, smoke_*), bateria de mutação, gates.manifest ou run_gates.sh; ao mockar (MagicMock, vi.fn, patch); ao testar código asyncio (ensure_future, tasks); ao rodar suíte Python dentro de container Docker; ao interpretar um resultado verde, vermelho, skipped ou INCONCLUSIVO; e ao validar stack depois de rebuild, wipe ou up -d.
---

# testing-pattern — o teste tem de poder reprovar

A pergunta que governa tudo abaixo: **o que faria este teste ficar vermelho?** Se a resposta
for "nada que eu consiga produzir", ele não é teste — é confiança comprada sem lastro.
O porquê de cada regra, com número e data, está em
[`references/casos-medidos.md`](references/casos-medidos.md). Leia a seção correspondente
quando a regra parecer excesso de zelo — foi medida.

## 1. Antes de escrever: a proposição

1. **Escreva em uma frase a PROPOSIÇÃO que o teste julga.** Depois confira de qual proposição
   cada ramo do veredicto é evidência. Um instrumento honesto pode responder a pergunta
   *vizinha* (casos-medidos §1).
2. **"Isto machuca?" são dois números**: exposição (quantos passaram pela condição) e dano
   (quantos sofreram por ela). Nunca um ramo só.
3. **Produtor novo pede dois testes**: *registrou o fato* e *não registrou o não-fato* — com a
   testemunha de presença ao lado, senão um produtor que nunca emite passa nos dois. O defeito
   costuma aparecer só quando alguém conta a população que NÃO deveria ter linha.
4. **Fechando um portão (auth, escopo, gate de config)**: escreva também o caso que prova que
   ele **deixa alguém passar**. O vermelho de um controle positivo parece proteção.
5. Se spec e código discordam, o teste pode revelar que a **spec** pedia a coisa errada —
   corrigir a spec é resultado válido.

## 2. Desenho do veredicto (probe / gate shell)

- **Três desfechos, nunca dois**, que é o contrato do `run_gates.sh`:
  `0` VERDE · `1` VERMELHO · `2` INCONCLUSIVO (não conseguiu medir: serviço fora, credencial
  ausente, amostra vazia). INCONCLUSIVO **conta como falha** e não é arredondado.
- **Amostra vazia é INCONCLUSIVO, nunca verde.** Imprima `SEM AMOSTRA` e saia 2.
- **Dependência ausente é INCONCLUSIVO, nunca vermelho** — e diga qual (`command not found`
  tem a mesma cor de reprovação se o script não distinguir).
- Janela temporal sobre dado gravado: corte por `ingested_at`, não por `started_at`
  (senão cobra dado anterior ao deploy).
- Ramos nomeados (`A · CENSO`, `B · MUTACAO`, …) e resumo final que permite chegar ao motivo
  sem re-rodar.

### Armadilhas de shell já pagas

| Forma | O que faz de errado |
|---|---|
| `set -e` + `VAR=$(curl …)` | mata o script **sem imprimir** quando o serviço ainda sobe |
| `jq '.campo // empty'` | trata `false` como ausente |
| `date -d "$X"` com `X` vazio | devolve HOJE; o `\|\| fallback` nunca dispara |
| ler `REDIS_URL` quando o serviço usa `PLUGHUB_REDIS_URL` | `skipped` eterno, que parece resposta |
| `grep` no fonte para provar comportamento | conta o comentário que documenta a mudança |

## 3. Falseabilidade: a bateria de mutação

Gate que decide cobertura, autorização ou contrato ganha um **`mut_<gate>.sh`** irmão
(modelo: `infra/test/mut_gates_manifest_coverage.sh`):

1. **M0 = controle positivo**: sem mutação, o gate tem de estar VERDE. Se já está vermelho,
   saia 2 — as mutações não provariam nada.
2. **M1..Mn**: plante cada defeito que o gate existe para pegar e **exija VERMELHO**.
3. Prefira entrada de mentira por env (ex.: `GATE_MANIFEST`) a editar arquivo real; quando
   editar for inevitável, faça backup e restaure em `trap … EXIT INT TERM`.
4. Saída: `0` todas pegas · `1` alguma sobreviveu · `2` não mediu.

## 4. Python / TypeScript

- **Mock de método do PRÓPRIO objeto sob teste: asserte `hasattr` antes.** O mock CRIA o
  alvo e o teste prova a chamada escondendo a ausência (casos-medidos §2a).
- **`MagicMock` é truthy** em qualquer atributo — flag lida de mock muda o caminho do código
  em silêncio. Configure o valor explicitamente.
- **Contrato de payload se mede no LEITOR**, não no produtor nem em constante do teste
  (§2b). Produtor e teste concordando entre si não dizem nada sobre o consumidor.
- **Censo de população = AST, não `grep` nem `hasattr`** (`_route_principal_census.py`,
  `_scope_resolver_census.py` são modelos). Comportamento de query: asserte sobre o **SQL
  executado**, não sobre o fonte.
- **asyncio: espere pelas TASKS, nunca por contagem de `sleep(0)`** (§2c). O conjunto de
  tasks mora no PRODUTO (e guardar o retorno de `ensure_future` já é obrigatório por GC);
  helper com `asyncio.all_tasks` varre as tasks do chamador também.
- Guarda sobre valor decodificado: `if not x`, nunca `is None` — os decoders do repo devolvem
  `""`. O teste do ramo de ausência tem de usar o valor que a FONTE produz.

## 5. Rodar

- **Gate shell: de dentro do WSL**, não do Git Bash (sem `jq` o gate sai vermelho sem ter
  medido nada):

  ```bash
  wsl.exe -d ubuntu -- bash -lc 'cd /home/a1/projects/plughub && bash infra/test/<gate>.sh'
  ```

  Conjunto: `bash infra/test/run_gates.sh [--only <padrão>] [--list]`, `GATE_TIMEOUT=600`.
- **pytest no WORKDIR do pacote**, nunca da raiz do monorepo (o rootdir muda e o
  `asyncio_mode = "auto"` do pacote deixa de valer — 476 falsos vermelhos, §3b):

  ```bash
  docker exec plughub-demo-<svc>-1 sh -c 'cd /app/packages/<svc> && python -m pytest -q'
  ```

  ⚠️ **Isso testa o código da IMAGEM, não da árvore** — nenhum serviço monta `packages/` por
  bind-mount. Sem `build` + `up -d` antes, o verde é sobre o arquivo antigo (medido
  2026-09-16: 766 verdes e `grep` do texto novo no container = 0). Confirme com
  `docker exec … grep -c '<âncora nova>' <arquivo>` antes de ler o resultado. Ver skill
  `deployment` § 1.

- **O serviço não recarrega** (uvicorn sem `--reload`): probe AO VIVO mede o código de
  quando o container subiu. Confira `docker inspect --format '{{.State.StartedAt}}'` antes de
  culpar ou absolver um edit.
- **`docker cp` sobrevive a `restart`, não a `up -d`.** Mudança de código de serviço =
  `build`. Um `up -d` no meio da validação faz o teste "regredir" sem motivo.
- **Reprodutível = IMAGEM, não container** (§3a): `docker run` sobre a imagem responde o que
  `docker exec` não responde. Declaração (Dockerfile) → imagem → execução são três fatos
  (`probe_python_suites.sh`).
- **Instalação limpa é teste** (§3): `infra/scripts/rebuild-all.sh --wipe`, de propósito e em
  dia calmo. Falha logo após wipe: a hipótese ordenada é "o wipe revelou".

## 6. Registrar

- **Todo `.sh` novo em `infra/test/` entra no `gates.manifest`** numa das quatro classes:
  `<nome>` AUTO · `!<nome>` ASSISTIDO (com o requisito) · `=<nome>` ISENTO (com o motivo) ·
  `?<nome>` NÃO TRIADO. `probe_gates_manifest_coverage.sh` reprova script sem classe (§3c).
- O **prefixo não define** o que é gate; a classe no manifesto define.
- Gate que protege um invariante do `CLAUDE.md` é citado **na própria regra** (`Gate: …`).

## 7. Ler o resultado

- **Vermelho**: antes de acreditar, procure `command not found`, serviço subindo, container
  com imagem velha. Um gate que não rodou e um gate que reprovou têm a mesma cor.
- **Verde**: pergunte o que o faria vermelho. Se não souber, rode a bateria de mutação.
- **Número que mudou sem código mudar**: compare com medição anterior do MESMO alvo
  (`channel-gateway` 699/0 → 594/187 com o mesmo código era o runner, não o produto).
- **Relate os três desfechos com o nome certo** — INCONCLUSIVO nunca vira "passou".
