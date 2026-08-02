#!/usr/bin/env bash
# measure_capacity_licensing_baseline.sh
#
# Mede o estado ATUAL de capacidade e licenciamento antes do arco
# "Capacidade, licenças e isolamento entre pools" (TODO.md).
#
# Perguntas que decidem escopo (estado em 2026-08-02, pós-fatia 3):
#   Q-1 estou no banco do agent-registry, ou num fóssil?       → PORTÃO, aborta se errado
#   Q1  algum pool de IA roda com > 1 sessão por instância?    → defeito de unidade ATIVO ou latente
#   Q3  existe quota contratada (C) configurada?               → defeito C (teto misto) no provisionamento
#   Q4  estado do balde de IA + resíduo das chaves mortas      → regressão da fatia 3
# E tira o retrato do defeito A (snapshot × verdade do semáforo).
#
# **Q2 ("alguém usa `session_reservation`?") SAIU** — respondida por remoção: a coluna
# foi dropada na fatia 3. Duas lições ficaram registradas no `TODO.md`
# § "Medições que decidiram o escopo", e valem para qualquer sonda futura:
#   · a medição ORIGINAL de Q1/Q2 saiu do banco ERRADO (`plughub_demo`, que tem um
#     `pools` fóssil) — daí o portão Q-1, que agora abre este arquivo;
#   · a descoberta da tabela era ancorada em `session_reservation`, o próprio objeto
#     sob investigação. Sonda que só funciona enquanto o defeito existe para de
#     funcionar exatamente quando se quer confirmar que ele foi embora.
#
# NÃO usa `set -e`: um comando que falha deve IMPRIMIR e seguir. Script que morre
# mudo no meio de uma medição produz ausência de dado indistinguível de "não há dado".
#
# Uso:  bash infra/test/measure_capacity_licensing_baseline.sh [tenant_id]

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="${1:-demo}"
PGUSER="plughub"
# ⚠️ CORRIGIDO 2026-08-02 — era `plughub_demo`, e essa era a resposta ERRADA.
#
# O agent-registry roda ISOLADO em `plughub_registry` (docker-compose.demo.yml:551-555:
# ele executava `prisma db push --accept-data-loss`, que dropa tabelas do `public` fora
# do seu Prisma; apontá-lo a um DB dedicado impediu o clobber). `plughub_demo` guarda um
# `public.pools` **FÓSSIL**, de antes dessa separação — mesma estrutura, dados
# congelados, nenhum serviço escrevendo.
#
# Q1 e Q2 desta linha de base foram medidos contra o fóssil em 2026-07-31, e foi Q2
# ("zero pools usam `session_reservation`") que decidiu ADIAR a fatia 4. A tabela existe,
# tem as colunas certas e devolve linhas plausíveis — o modo de falha perfeito: nada
# fica vazio, nada erra, e o número está desatualizado.
PGDB="plughub_registry"

# Extrai campo numérico de JSON. `json.dumps` do Python usa separador ': ' (COM
# espaço) — um grep '"campo":[0-9]*' casa o rótulo e captura ZERO dígitos, devolvendo
# vazio que o relatório imprime como '-'. Foi o 3º "parece que não há dado" deste script.
jnum() { printf '%s' "$1" \
         | grep -o "\"$2\"[[:space:]]*:[[:space:]]*-\?[0-9]\+" \
         | head -1 | grep -o -- '-\?[0-9]\+$'; }
jarr() { printf '%s' "$1" | grep -o "\"$2\"[[:space:]]*:[[:space:]]*\[[^]]*\]" | head -1; }

hr()   { printf '\n────────────────────────────────────────────────────────────────\n'; }
head1() { hr; printf '%s\n' "$1"; hr; }
note()  { printf '  » %s\n' "$1"; }

# `< /dev/null` NÃO é adorno: `docker compose exec -T` LÊ STDIN. Chamado de dentro de
# um `while read`, ele engole o resto do pipe e o laço roda UMA vez só — devolvendo um
# retrato parcial que parece completo. Foi o que aconteceu na execução de 21:11.
redis() { $COMPOSE exec -T redis redis-cli "$@" 2>&1 < /dev/null; }
psqlq() { $COMPOSE exec -T postgres psql -U "$PGUSER" -d "$PGDB" -tA -c "$1" 2>&1 < /dev/null; }

printf 'Baseline de capacidade e licenciamento — tenant=%s — %s\n' \
       "$TENANT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── Q-1 — estou olhando o banco do agent-registry, ou um fóssil? ──────────────
# Este portão existe porque a resposta errada NÃO se anuncia: `plughub_demo` também
# tem um `public.pools`, com as mesmas colunas, devolvendo linhas plausíveis e
# desatualizadas. O discriminador é `_prisma_migrations` — só o banco que o Prisma do
# agent-registry administra a possui. Vem ANTES de tudo: uma linha de base tirada do
# banco errado não é imprecisa, é sobre outra coisa.
MIGR=$(psqlq "SELECT to_regclass('public._prisma_migrations') IS NOT NULL;")
if [ "$MIGR" != "t" ]; then
  hr
  printf '!! ABORTADO: %s não é o banco do agent-registry (sem _prisma_migrations).\n' "$PGDB"
  printf '   O DATABASE_URL autoritativo:\n'
  printf '     %s exec -T agent-registry sh -lc '"'"'echo "$DATABASE_URL"'"'"'\n' "$COMPOSE"
  printf '   Ajuste PGDB no topo deste arquivo. NÃO leia os números abaixo de outro banco:\n'
  printf '   `plughub_demo.public.pools` é um FÓSSIL de antes da separação (compose:551).\n'
  hr
  exit 2
fi
note "banco conferido: $PGDB tem _prisma_migrations (é o do agent-registry)"

# ─────────────────────────────────────────────────────────────────────────────
# Q0 — sanidade do prefixo. Medir o tenant errado devolve tudo vazio, e vazio
# PARECE medição. Descobrir quais tenants existem de fato antes de concluir nada.
head1 "Q0 — sanidade do tenant (o prefixo existe?)"

REDIS_TENANTS=$( { redis --scan --pattern '*:pool:*:instances';
                   redis --scan --pattern '*:instance:*'; } \
                 | sed -e 's/:pool:.*//' -e 's/:instance:.*//' | sort -u | grep -v '^$' )
PG_TENANTS=$(psqlq "SELECT DISTINCT tenant_id FROM public.pools ORDER BY 1;")

note "tenants com estado no Redis : $(printf '%s' "$REDIS_TENANTS" | tr '\n' ' ')"
note "tenants na tabela de pools  : $(printf '%s' "$PG_TENANTS"    | tr '\n' ' ')"

if [ -n "$REDIS_TENANTS" ] && ! printf '%s\n' "$REDIS_TENANTS" | grep -qx "$TENANT"; then
  note "!! ATENÇÃO: '$TENANT' NÃO aparece no Redis. Tudo abaixo que dependa de Redis"
  note "   (Q3, Q4 e o retrato de A) sai VAZIO por prefixo errado — vazio aqui NÃO é"
  note "   'não há dado', é 'não medido'. Rode de novo com um dos tenants acima."
elif [ -z "$REDIS_TENANTS" ]; then
  note "!! Nenhum tenant com estado de pool/instância no Redis. Ou a stack está parada,"
  note "   ou o Redis foi limpo e o bridge não repovoou. Checar: $COMPOSE ps"
fi

# ─────────────────────────────────────────────────────────────────────────────
head1 "Q1 — configuração dos pools (unidade de licença)"

# ⚠️ A DESCOBERTA DA TABELA ERA ANCORADA NA COLUNA QUE ESTE ARCO REMOVEU.
# Até 2026-08-02 este bloco localizava a tabela por `column_name='session_reservation'`
# — e a fatia 3 dropou a coluna, então o script passou a sair INCONCLUSIVO. A âncora
# tinha de ser algo ESTÁVEL, não o próprio objeto sob investigação: uma sonda que só
# funciona enquanto o defeito existe deixa de funcionar exatamente quando você precisa
# confirmar que ele foi embora. Agora ancora em `pool_id` + `tenant_id`.
#
# **Q2 SAIU do script.** A pergunta era "alguém usa `session_reservation`?", e o campo
# não existe mais (migração `20260802000000_drop_pool_session_reservation`). Mantê-la
# devolveria "0 pools" para sempre — resposta que parece medição e é tautologia. O
# histórico dela, e o fato de a medição original ter sido tirada do BANCO ERRADO, está
# no `TODO.md` § "Medições que decidiram o escopo".
POOL_TABLE=$(psqlq "SELECT c.table_schema||'.'||c.table_name
                      FROM information_schema.columns c
                     WHERE c.column_name = 'pool_id'
                       AND EXISTS (SELECT 1 FROM information_schema.columns c2
                                    WHERE c2.table_schema = c.table_schema
                                      AND c2.table_name  = c.table_name
                                      AND c2.column_name = 'agent_kind')
                     LIMIT 1;")
if [ -z "$POOL_TABLE" ] || printf '%s' "$POOL_TABLE" | grep -qi 'error'; then
  note "INCONCLUSIVO: não localizei a tabela de pools (pool_id + agent_kind)."
  note "Saída bruta: ${POOL_TABLE:-<vazio>}"
  note "Fallback: consultar o agent-registry — curl -s localhost:3300/v1/pools -H \"x-tenant-id: $TENANT\""
else
  note "tabela de pools = $POOL_TABLE"
  note "CUIDADO — colisão de nome: a coluna Pool.max_concurrent_sessions é o THROTTLE de pool"
  note "webhook. As sessões-por-instância de IA vivem em PoolSkillSlot.config_json (slot 'current'),"
  note "e a API as expõe como 'deployed_max_concurrent_sessions' (pools.ts:213-217). Não são a mesma"
  note "grandeza; só a segunda responde a pergunta de unidade de licença."

  printf '\n%-28s %-8s %-12s\n' POOL KIND WH_THROTTLE
  psqlq "SELECT COALESCE(pool_id,'?'), COALESCE(agent_kind,'-'),
                COALESCE(max_concurrent_sessions::text,'-')
           FROM $POOL_TABLE WHERE tenant_id = '$TENANT'
          ORDER BY agent_kind NULLS LAST, pool_id;" \
    | awk -F'|' '{printf "%-28s %-8s %-12s\n", $1, $2, $3}'

  # Q1 — sessões por instância de IA: slot 'current', config_json.
  SLOT_TABLE=$(psqlq "SELECT c.table_schema||'.'||c.table_name
                        FROM information_schema.columns c
                       WHERE c.column_name = 'config_json'
                         AND EXISTS (SELECT 1 FROM information_schema.columns c2
                                      WHERE c2.table_name = c.table_name
                                        AND c2.column_name = 'slot')
                       LIMIT 1;")
  printf '\n'
  if [ -z "$SLOT_TABLE" ] || printf '%s' "$SLOT_TABLE" | grep -qi 'error'; then
    note "Q1 INCONCLUSIVO: não localizei a tabela de slots. Saída: ${SLOT_TABLE:-<vazio>}"
    note "Fallback: curl -s localhost:3300/v1/pools -H \"x-tenant-id: $TENANT\" | grep deployed_max"
  else
    note "tabela de slots = $SLOT_TABLE"
    # `slot` é ENUM (SkillSlot): COALESCE com literal de texto falha na inferência
    # de tipo. Castar para text ANTES de qualquer COALESCE.
    printf '  %-28s %-9s %s\n' POOL SLOT SESSOES_POR_INSTANCIA
    # Filtro por tenant via pools (não assume que a tabela de slots tenha tenant_id).
    SLOT_SCOPE="pool_id IN (SELECT pool_id FROM $POOL_TABLE WHERE tenant_id = '$TENANT')"
    psqlq "SELECT COALESCE(pool_id,'?'), COALESCE(slot::text,'-'),
                  COALESCE(config_json->>'max_concurrent_sessions','(ausente→1)')
             FROM $SLOT_TABLE WHERE slot = 'current' AND $SLOT_SCOPE ORDER BY pool_id;" \
      | awk -F'|' '{printf "  %-28s %-9s %s\n", $1, $2, $3}'
    AI_MULTI=$(psqlq "SELECT count(*) FROM $SLOT_TABLE
                       WHERE slot = 'current' AND $SLOT_SCOPE
                         AND COALESCE((config_json->>'max_concurrent_sessions')::int, 1) > 1;")
    note "Slots 'current' com 'Concurrent sessions' > 1 → ${AI_MULTI:-?}"
    note "ATENÇÃO — este número NÃO responde Q1. O bootstrap (instance_bootstrap.py:1054-1072)"
    note "usa 'Concurrent sessions: N' como NÚMERO DE INSTÂNCIAS, cada uma com max_concurrent=1."
    note "Logo, para IA, instância == sessão, e o campo acima é o TOTAL do pool, não a"
    note "concorrência por instância. Q1 se responde no retrato de A (coluna MAXC das"
    note "instâncias no Redis), não aqui."
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
head1 "Q3 — quota contratada (C) e por tipo"

QUOTA_KEYS=$(redis --scan --pattern '*:quota:*' | sort)
if [ -z "$QUOTA_KEYS" ]; then
  note "Nenhuma chave de quota. Defeito B está DORMENTE aqui (sem C, a admissão não tem limite)."
  note "ATENÇÃO: dormente no demo ≠ dormente em tenant com pricing configurado."
else
  printf '%s\n' "$QUOTA_KEYS" | while read -r k; do
    [ -z "$k" ] && continue
    printf '  %-52s = %s\n' "$k" "$(redis GET "$k")"
  done
  note "C misto (ai+human) em ':quota:max_concurrent_sessions' é o defeito B: gasto em SESSÕES."
fi

# ─────────────────────────────────────────────────────────────────────────────
head1 "Q4 — admissão: um balde só, e as chaves mortas não voltaram"

# A pergunta original era "o balde compartilhado contém sessão HUMANA?" — evidência
# direta do defeito B-2. Ela foi RESPONDIDA POR REMOÇÃO na fatia 3 (2026-08-02): o pote
# misto deixou de existir e humano não é mais gateado por sessão. O que este bloco mede
# agora é o estado do único balde que sobrou, mais um portão de regressão sobre as
# chaves mortas — porque a evidência de que o modelo antigo voltou seria justamente
# alguém REESCREVENDO `admission:shared`.
KIND_AI=$(redis SCARD "$TENANT:admission:kind:ai")
C_AI=$(redis GET "$TENANT:quota:capacity:ai_agent")
note "SCARD $TENANT:admission:kind:ai = ${KIND_AI:-?}   (teto C_ai = ${C_AI:-<sem quota>})"
note "Atribuição por pool (HASH ai_pools) — todo membro do SET deve ter uma linha aqui:"
redis HGETALL "$TENANT:admission:ai_pools" | paste - - 2>/dev/null | sed 's/^/    /'

# Chaves mortas: presença é HISTÓRIA (não expiram sozinhas), reescrita é REGRESSÃO.
# O script não sabe distinguir as duas numa foto única — quem distingue é o
# `smoke_admission_licensing.sh`, que compara duas leituras em volta de tráfego.
SHARED_N=$(redis SCARD "$TENANT:admission:shared")
RESV=$(redis --scan --pattern "$TENANT:admission:reserved:*" | grep -c . 2>/dev/null)
if [ "${SHARED_N:-0}" != "0" ] || [ "${RESV:-0}" != "0" ]; then
  note "!! resíduo do pote misto: shared=${SHARED_N:-0} membro(s), ${RESV:-0} balde(s) reserved."
  note "   Sem escritor desde a fatia 3, e SETs não têm TTL — some quando as sessões"
  note "   antigas fecharem, ou por limpeza manual. Para saber se é história ou"
  note "   REGRESSÃO: bash infra/test/smoke_admission_licensing.sh (Portão A)."
else
  note "nenhum resíduo do pote misto."
fi

# ─────────────────────────────────────────────────────────────────────────────
head1 "Defeito A — snapshot × verdade do semáforo"

# ── Fatia 2 (2026-08-02): a coluna ACTIVE_C MORREU com o contador que ela lia ──
# `{t}:pool:{p}:active_count` foi REMOVIDO — contava por POOL uma capacidade que é
# do RECURSO. Mantê-la aqui faria a ferramenta de MEDIÇÃO cair exatamente na
# armadilha que o código deixou de ter: chave sem escritor devolve 0, e 0 é
# plausível. No lugar entram os campos derivados do semáforo.
#
# MODEL é a coluna que impede a leitura errada das outras:
#   resource_semaphore    → routing-engine; BUSY/ELSEW/UNTAG são MEDIDOS
#   bootstrap_placeholder → bridge (NX); ele não parseia a tag do ocupante e por
#                           isso OMITE os três. `-` ali é AUSÊNCIA, não zero — não
#                           somar, não concluir "ninguém em atendimento".
printf '%-28s %-7s %-6s %-7s %-7s %-7s %-6s %s\n' \
       POOL AVAIL BUSY ELSEW UNTAG TOTAL FILA MODEL
# Lista capturada ANTES do laço (defesa em profundidade contra consumo de stdin).
SNAP_KEYS=$(redis --scan --pattern "$TENANT:pool:*:snapshot" | sort)
for k in $SNAP_KEYS; do
  [ -z "$k" ] && continue
  POOL=$(printf '%s' "$k" | sed "s/^$TENANT:pool://; s/:snapshot$//")
  SNAP=$(redis GET "$k")
  # A chave EXISTIA no --scan e sumiu antes do GET. Não é snapshot ruim: cada campo
  # custa um `docker compose exec` (~0,5 s) e o laço leva minutos, enquanto a linha
  # do bootstrap vive 60 s. Sem este ramo, o pool sai com tudo '-' e MODEL 'legado'
  # — indistinguível de snapshot corrompido, e sistematicamente nos ÚLTIMOS pools da
  # ordem alfabética (foi assim que apareceu: só `wrapup_*`).
  if [ -z "$SNAP" ] || [ "$SNAP" = "" ]; then
    printf '%-28s %-7s %-6s %-7s %-7s %-7s %-6s %s\n' \
           "$POOL" "?" "?" "?" "?" "?" "?" "EXPIROU-DURANTE-A-LEITURA"
    continue
  fi
  AV=$(jnum "$SNAP" available)
  BU=$(jnum "$SNAP" busy)
  BE=$(jnum "$SNAP" busy_elsewhere)
  UN=$(jnum "$SNAP" untagged)
  TI=$(jnum "$SNAP" total_instances)
  MO=$(printf '%s' "$SNAP" | sed -n 's/.*"model": *"\([a-z_]*\)".*/\1/p')
  QL=$(redis ZCARD "$TENANT:pool:$POOL:queue")
  printf '%-28s %-7s %-6s %-7s %-7s %-7s %-6s %s\n' \
         "$POOL" "${AV:--}" "${BU:--}" "${BE:--}" "${UN:--}" "${TI:--}" \
         "${QL:-0}" "${MO:-legado}"
done

# Chave morta: se ainda houver `active_count` no Redis, é resíduo anterior à fatia 2
# (a chave não tem TTL — ninguém a apaga) OU um escritor que ressuscitou. Denunciar,
# porque valor sobrevivente ali é convite a que volte a ser lido.
AC_LEFT=$(redis --scan --pattern "$TENANT:pool:*:active_count" | grep -c .)
if [ "${AC_LEFT:-0}" -gt 0 ] 2>/dev/null; then
  note "ATENÇÃO — $AC_LEFT chave(s) '$TENANT:pool:*:active_count' ainda existem."
  note "O contador foi REMOVIDO na fatia 2 e não tem mais escritor. Sem TTL, resíduo"
  note "anterior ao deploy persiste indefinidamente. Se o número CRESCER entre duas"
  note "execuções, alguém voltou a escrever — procurar o produtor. Limpar o resíduo:"
  note "  redis-cli --scan --pattern '$TENANT:pool:*:active_count' | xargs -r redis-cli DEL"
fi

SNAP_N=$(redis --scan --pattern "$TENANT:pool:*:snapshot" | grep -c .)
if [ "${SNAP_N:-0}" -eq 0 ] 2>/dev/null; then
  note "NENHUM snapshot de pool. Não é 'sistema ocioso': o bootstrap do bridge reescreve"
  note "snapshot a cada heartbeat (15 s, NX, TTL 60 s). Ausência total sugere bridge parado"
  note "ou pools não carregados para este tenant. Checar:"
  note "  $COMPOSE ps orchestrator-bridge   e   redis-cli --scan --pattern '$TENANT:pool:*'"
fi

# ATENÇÃO: {t}:instance:{iid}:sessions só EXISTE enquanto há ocupação — o Lua de release
# faz DEL ao chegar a zero. Listar por esse padrão num sistema ocioso devolve vazio e
# parece "não há instância". Enumerar pelas CHAVES DE INSTÂNCIA e pelos sets do pool.
# CUSTO: cada chamada é um `docker compose exec` (~0,5 s). Iterar as ~350 instâncias
# levaria minutos. Inversão: a chave `{t}:instance:{iid}:sessions` só EXISTE enquanto
# há ocupação (o Lua de release faz DEL em zero) — então percorrer as chaves de
# ocupação dá o mesmo resultado em O(ocupadas), não O(instâncias).
INST_TOTAL=$(redis --scan --pattern "$TENANT:instance:*" | grep -vc -e ':sessions$' -e ':meta$')
OCC_KEYS=$(redis --scan --pattern "$TENANT:instance:*:sessions" | sort)
OCC_TOTAL=$(printf '%s\n' "$OCC_KEYS" | grep -c .)

printf '\n  Instâncias registradas: %s   ·   com ocupação agora: %s\n' \
       "${INST_TOTAL:-?}" "${OCC_TOTAL:-0}"
if [ "${OCC_TOTAL:-0}" -eq 0 ] 2>/dev/null; then
  note "Nenhuma instância ocupada — sem ocupação não há consumo a propagar, e este"
  note "retrato NÃO julga o defeito A. Rodar DURANTE um atendimento (o caso que o"
  note "reproduz é humano logado em ≥2 pools com pelo menos uma vaga tomada)."
else
  printf '  %-34s %-6s %-6s %-6s %s\n' INSTANCIA OCUP MAXC MCS POOLS
  for k in $OCC_KEYS; do
    [ -z "$k" ] && continue
    IID=$(printf '%s' "$k" | sed "s/^$TENANT:instance://; s/:sessions$//")
    OCC=$(redis SCARD "$k")
    RAW=$(redis GET "$TENANT:instance:$IID")
    printf '  %-34s %-6s %-6s %-6s %s\n' "$IID" "${OCC:-0}" \
           "$(jnum "$RAW" max_concurrent)" "$(jnum "$RAW" max_concurrent_sessions)" \
           "$(jarr "$RAW" pools)"
    printf '    occupants: %s\n' "$(redis SMEMBERS "$k" | tr '\n' ' ')"
    printf '    espelho current_sessions=%s (deve casar com OCUP)\n' \
           "$(jnum "$RAW" current_sessions)"
  done
fi
note "MAXC = vaga da INSTÂNCIA; MCS = total do POOL gravado no registro da instância."
note "Dois escopos no mesmo objeto — quem ler o nome errado erra por MCS/MAXC vezes."

printf '\n  Membros por pool (ready ∪ busy) — a "tag" que hoje é implícita:\n'
printf '  %-28s %-8s %-8s\n' POOL READY BUSY
POOLSET_KEYS=$(redis --scan --pattern "$TENANT:pool:*:instances" | sort)
for k in $POOLSET_KEYS; do
  [ -z "$k" ] && continue
  P=$(printf '%s' "$k" | sed "s/^$TENANT:pool://; s/:instances$//")
  # Contagem, não listagem: com 350 instâncias a listagem afoga o relatório e o
  # que importa aqui é a CARDINALIDADE por pool (a base do `total_capacity`).
  printf '  %-28s %-8s %-8s\n' "$P" \
    "$(redis SCARD "$k")" "$(redis SCARD "$TENANT:pool:$P:busy_instances")"
done

hr
note "LEITURA (fatia 2 — o que cada linha deve satisfazer):"
note "  1. ARITMÉTICA DA LINHA: em toda linha 'resource_semaphore',"
note "     AVAIL = TOTAL − BUSY − ELSEW. Não fechando, o recompute e os campos"
note "     publicados divergiram — cada um plausível sozinho, contraditórios juntos."
note "  2. CONSUMO PROPAGADO (defeito A, corrigido): para um recurso logado em N pools,"
note "     AVAIL é IGUAL nas N linhas e vale (max_concurrent − ocupação do recurso)."
note "     BUSY aparece só na linha do pool que serviu; as irmãs mostram ELSEW."
note "  3. UNTAG deve ser 0. Membro sem tag é legítimo só nas 24 h após o deploy da"
note "     F1 (TTL do SET); persistente = escritor de ocupante fora do claim_instance."
note "  4. Σ AVAIL entre pools CONTINUA ERRADO — é o defeito C (fase F4), não regressão:"
note "     available não é aditivo entre pools que compartilham o mesmo recurso."
note "  5. MODEL 'EXPIROU-DURANTE-A-LEITURA' não é defeito do dado: a linha do bootstrap"
note "     vive 60 s e este laço leva minutos (um exec por campo). Para um retrato"
note "     coerente de TODOS os pools, medir com o bridge parado ou reduzir o escopo."
hr
