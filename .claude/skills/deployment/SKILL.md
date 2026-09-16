---
name: deployment
description: Como levar uma mudança do PlugHub ao ar e provar que ela está rodando — código de serviço para imagem e container, skill YAML para o slot do pool, config de pool/hooks para o agent-registry, stack demo inteira. Use ao rodar docker compose (build, up -d, restart, docker cp, --no-cache), rebuild-all.sh, up.sh, fresh-install.sh ou --wipe; ao publicar ou promover skill (deploy_skill_to_slot.sh, set-next, promote); ao editar infra/registry/*.yaml, infra/modules.yaml ou @plughub/schemas; ao verificar se um serviço roda o código novo; e ao operar o repo pela toolchain Windows (git, Python, Git Bash, wsl.exe) — CRLF, bit +x, variáveis comidas pelo shell.
---

# deployment — o que roda é a imagem, não a árvore

Três perguntas diferentes, que se confundem porque todas respondem "salvei":
**a árvore mudou?** · **o artefato (imagem, slot, linha do registry) mudou?** · **o processo
vivo está usando o artefato novo?** Só a terceira é deploy. O porquê de cada regra está em
[`references/casos-medidos.md`](references/casos-medidos.md).

## 1. Mudei código de um serviço

**Nenhum serviço monta `packages/` por bind-mount** (medido 2026-09-16, §3). Editar o arquivo
não muda nada no container — nem o `pytest` rodado lá dentro.

```bash
docker compose -f docker-compose.demo.yml build <svc>
docker compose -f docker-compose.demo.yml up -d <svc>
```

- **Arquivo NOVO no pacote → `build --no-cache`** (nota do `rebuild-all.sh`: o cache de layer
  não invalida por arquivo novo).
- **`@plughub/schemas` mudou → rebuild de TODO consumidor que valida aquele schema.** Caso
  medido: mexer no `MenuStepSchema` obriga `agent-registry`, `skill-flow-service` e
  `mcp-server-plughub` juntos, senão o registry rejeita o ref com 422.
- **`docker cp` é iteração efêmera**: sobrevive a `restart`, some no próximo `up -d` e o
  teste "regride" sem motivo (§2). Nunca é o deploy.
- **Subiu serviço a serviço?** Termine com `infra/scripts/up.sh`: `build X` + `up -d X`
  deixa containers de idades diferentes, e só o `up -d` completo reconcilia. O `up.sh`
  também julga o estado depois (exit 0 do `up` não é veredicto).

### Provar que está rodando

1. `docker inspect --format '{{.State.StartedAt}}' plughub-demo-<svc>-1` é **posterior** ao build.
2. Âncora de conteúdo **dentro do container**: `docker exec plughub-demo-<svc>-1 grep -c '<texto novo>' <arquivo>`. `0` significa que o teste que você rodou mediu o código antigo.
3. Serviço Python não recarrega (uvicorn sem `--reload`): probe ao vivo mede o código de
   quando o container subiu.

## 2. Mudei um skill / flow

Salvar não muda o que roda. O bridge executa **só o snapshot do slot `current` do POOL**.

```bash
bash infra/scripts/deploy_skill_to_slot.sh   # leia o cabeçalho: argumentos e CONFIG_MERGE
```

- Skills são **seed-if-absent**: editar o YAML e reiniciar é no-op (só loga DRIFT). O caminho é
  `PUT /v1/skills/:id` → `PUT /v1/pools/:id/slots/next` → `POST /v1/pools/:id/promote`.
- **O promote responde OK mesmo se o PUT falhou**, e fotografa o flow ANTIGO. O script
  compara o snapshot promovido com uma âncora do flow novo — não pule essa etapa.
- **`set-next` sem `config_json` grava `{}`** e o promote apaga a config do pool (ex.:
  `max_concurrent_sessions`). O script preserva a atual; `CONFIG_MERGE` acrescenta chaves.
- Deploy é do POOL; `POST /v1/skills/:id/deploy` responde 410. Lote de verdade:
  `POST /v1/pool-slots/promote-batch`.

## 3. Mudei config de pool, hooks, deploy, capacity

- `infra/registry/*.yaml` também é **seed-if-absent**: editar pool já semeado é no-op, o **DB
  vence**. Estado real se pergunta ao registry (`GET :3300/v1/pools/<id>`), **nunca ao YAML**.
- Para o YAML valer: a API do registry (ou a tela), ou `REGISTRY_SYNC_RECONCILE=true` em dev.
- Config horizontal: config-api (`platform_config`), que publica `config.changed`.

## 4. Mudei `infra/modules.yaml` (catálogo ABAC)

É **arquivo único montado** no `auth-api`, e `docker restart` o deixa PARADO (§3a):

```bash
docker compose -p plughub-demo -f docker-compose.demo.yml up -d --no-deps --force-recreate auth-api
```

Vale para qualquer arquivo montado sozinho; `up -d --no-deps` sem `--force-recreate` também falha.

## 5. Stack inteira e bancos

| Comando | Quando | Custo |
|---|---|---|
| `infra/scripts/up.sh` | subir/reconciliar (nunca o Start do Docker Desktop) | nenhum |
| `infra/scripts/rebuild-all.sh` | rebuild de todas as imagens, volumes preservados | tempo |
| `rebuild-all.sh --wipe` | instalação limpa como TESTE, em dia calmo | **apaga todo pool/skill/config/form criado pela UI** |
| `infra/scripts/fresh-install.sh` | reset DESTRUTIVO do banco do agent-registry, de propósito | `prisma db push --accept-data-loss` |

- **Nunca** `prisma db push --accept-data-loss` no boot normal; o entrypoint é
  `bootstrap-db.js`, que só aplica `migrate deploy`.
- Falha logo após `--wipe`: a hipótese ordenada é "o wipe revelou" um defeito antigo.
- `--wipe` e `fresh-install.sh` são irreversíveis: **confirme com o usuário antes**.

## 6. Toolchain Windows operando o clone do WSL

O diretório é do WSL; `git`, Python e Git Bash desta máquina são de Windows (§1).

- **`.sh` com CRLF não roda no WSL** (roda no Git Bash, que tolera). Python escrevendo arquivo:
  `open(..., "w", newline="")`. Confira com `file <arq>` (não pode dizer `CRLF`).
- **git de Windows não vê o bit `+x`**: `core.fileMode=false` por clone, via
  `scripts/bootstrap-clone.sh`. **Nunca voltar `false` → `true` automaticamente.**
- **Rodar no WSL**: `wsl.exe -d ubuntu -- bash -lc 'cd /home/a1/projects/plughub && …'`.
  Variável, `$?`, laço, `$(…)` ou `{{…}}` → **script em arquivo** no scratchpad (aspas
  simples NÃO protegem), chamado de dentro do `bash -lc '…'` (fora dele o Git Bash reescreve
  `/mnt/c/…`). Leia o veredicto na saída impressa, nunca num exit code que cruzou a fronteira.
- Gate com `jq` roda de dentro do WSL; do Git Bash sai vermelho sem ter medido.
- Commit com sessão paralela na mesma árvore: `git diff --cached --name-only` vazio antes,
  depois `git add -- <caminhos>` + `git commit -- <caminhos>`. **Nunca `git add -A`.**
