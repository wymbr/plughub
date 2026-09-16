# Casos medidos — o porquê de cada regra da skill `deployment`

> §1 e §2 movidos **integralmente** do `CLAUDE.md` em 2026-09-16. §3 é medição do mesmo dia.
> §4 consolida armadilhas de shell que viviam só em memória local de sessão. Não resuma.

---

## 1. A FILESYSTEM RULE vale para as FERRAMENTAS, não só para os fontes (emenda de 2026-08-28)

A copia Windows continua intocada — o que estava misturado era a toolchain: o diretorio e o
do WSL, mas os binarios que o operam sao de Windows (`git 2.47.1.windows.1`, Python com
`os.linesep == '\r\n'`). Dois danos, ambos silenciosos ate serem fatais:
**(1)** `core.autocrlf=true` vem do gitconfig de SISTEMA do Git for Windows, e **um `.sh` com
CRLF nao roda sob WSL** — falha com `syntax error`, *depois* de ter rodado no Git Bash, que
tolera CRLF; **(2)** o git de Windows **nao enxerga o bit `+x`** neste mount (medido: 33
mudancas `755→644` pendentes, nenhuma no sentido inverso — e o `ls` da MESMA sessao mostra
`-rwxr-xr-x`, ou seja, `ls` e `git` discordam).

**Duas metades, e so uma viaja no commit.** `.gitattributes` e conteudo e o git le sozinho —
mecanismo. `core.fileMode` e `safe.directory` sao config **por clone**, e nenhum arquivo as
carrega: vivem em **`scripts/bootstrap-clone.sh`** (rodar apos `git clone`; o
`scripts/linux/setup.sh` delega a ele). Isso e promessa, nao mecanismo, e esta declarado como
tal no cabecalho do script.

**A decisao do `fileMode` e ASSIMETRICA, e a versao "mede e aplica" esta errada** — o mesmo
clone mede `100755` de dentro do WSL e `100644` pelo `\wsl.localhost`, entao uma execucao so
observa o proprio lado. `false` vence sempre; **nunca se volta de `false` para `true`
automaticamente**, porque quem roda nao sabe se outro lado toca o clone. Mesma forma do
`resolve_scope`: o restritivo vence, porque o permissivo degrada mudo.

Ao escrever arquivo com ferramenta Windows, **`newline=""` em Python** — modo texto grava CRLF.
O `.gitattributes` conserta no commit, mas o `.sh` ja quebrou antes disso.

---

## 2. `docker cp` sobrevive a `restart`, não a `up -d`

`up -d` recria o container a partir da imagem.
Mudança em código de serviço = `build`, nunca `cp` (que é só atalho de iteração efêmera). Um `up -d`
no meio de uma validação faz o serviço voltar à imagem antiga e os testes "regridem" sem motivo.

---

## 3. Nenhum serviço monta o código por bind-mount (medido 2026-09-16)

Uma memória de sessão afirmava *"o código é bind-mount, então o pytest já pega a edição"*.
Medido com `docker inspect` sobre os 35 containers do demo: **nenhum** serviço de aplicação
monta `packages/`. Os únicos binds são:

| Container | Destino montado |
|---|---|
| `orchestrator-bridge` | `/registry` · `/skills` |
| `routing-engine` | `/app/packages/skill-flow-engine/skills` |
| `skill-flow-service` | `/app/skills` |
| `auth-api` | `/infra/modules.yaml` (arquivo único) |
| `postgres` | `/docker-entrypoint-initdb.d` |
| `demo-assets` | `/usr/share/nginx/html` |

Consequência medida no mesmo dia: editou-se o docstring de
`analytics-api/clickhouse.py`, rodou-se `pytest` no container (**766 verdes**), e o
`grep` do texto novo dentro do container deu **0** — a suíte tinha medido o arquivo
ANTIGO. Verde sobre o artefato errado, exatamente o *"foi escrito ≠ mudou"*.

Refaça esta tabela antes de confiar nela: um bind novo no compose muda a resposta.

## 3a. Arquivo único montado quebra o `docker restart`

O compose monta `./infra/modules.yaml:/infra/modules.yaml:ro` no `auth-api`, e o Docker
Desktop/WSL fixa o mount no arquivo de quando o container foi criado. Edit/Write (e scripts
Python com `newline=""`) SUBSTITUEM o arquivo em vez de reescrever no lugar, e o mount passa
a apontar para algo que não existe. Medido em 2026-09-13 (PID-12):
`docker restart plughub-demo-auth-api-1` → `error mounting ... no such file or directory`,
container **Exited (127)**; `up -d --no-deps` também falha (religa o mesmo container). Resolveu
`docker compose -p plughub-demo -f docker-compose.demo.yml up -d --no-deps --force-recreate auth-api`.
O auth-api ficou ~4 min fora numa stack compartilhada.

---

## 4. Armadilhas de shell Windows → WSL

- **`wsl.exe -- bash -c` atravessa DUAS camadas de shell** e a de fora processa a string
  primeiro. `$?` e variáveis somem — **aspas simples NÃO salvam**: em 2026-09-07 dois gates
  que reprovavam (`exit 1`/`exit 2`) foram lidos como `rc=0`; em 2026-09-12 um
  `RC=$?; if [ "$RC" -ne 0 ]` não avaliou nada e o `git commit` seguinte rodou sem guarda; em
  2026-09-16 `S=…; git commit -F $S/msg` virou `-F /msg`. Backticks e `$(…)` entre aspas
  duplas são EXECUTADOS do lado de fora (inclusive dentro de `python3 -c "…"`).
  **Variável, laço, código de saída ou template (`{{…}}`) → script em arquivo, e o veredicto
  se lê na SAÍDA impressa, nunca num exit code que atravessou a fronteira.**
- **Git Bash reescreve caminho `/mnt/c/...` passado como ARGUMENTO** para
  `C:/Program Files/Git/mnt/c/...`. Dentro de `bash -lc '…'` o caminho chega intacto.
- **Heredoc do Bash mutila `\`**: patch em Python com barra invertida falha ou vira no-op.
  Use a ferramenta Write ou ancore a substituição em linha sem barra.
- **`String.replace` do Node come `$$`**: use função como substituto.
- **Gate com `jq` rodado do Git Bash** sai vermelho sem ter medido (`jq: command not found`).
