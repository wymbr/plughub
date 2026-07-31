#!/usr/bin/env bash
# measure_capacity_licensing_baseline.sh
#
# Mede o estado ATUAL de capacidade e licenciamento antes do arco
# "Capacidade, licenças e isolamento entre pools" (TODO.md).
#
# Responde quatro perguntas que decidem escopo:
#   Q1  algum pool de IA roda com > 1 sessão por instância?   → defeito de unidade ATIVO ou latente
#   Q2  alguém usa session_reservation?                        → piso/teto é conserto ou FEATURE
#   Q3  existe quota contratada (C) configurada?               → defeito B (teto misto) vivo ou dormente
#   Q4  o balde compartilhado de admissão contém sessão HUMANA? → defeito B-2, evidência direta
# E tira o retrato "antes" do defeito A (snapshot × verdade do semáforo).
#
# NÃO usa `set -e`: um comando que falha deve IMPRIMIR e seguir. Script que morre
# mudo no meio de uma medição produz ausência de dado indistinguível de "não há dado".
#
# Uso:  bash infra/test/measure_capacity_licensing_baseline.sh [tenant_id]

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="${1:-demo}"
PGUSER="plughub"
PGDB="plughub_demo"

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
head1 "Q1/Q2 — configuração dos pools (unidade e uso de reserva)"

# A tabela do Prisma pode estar mapeada; descobrir em vez de adivinhar.
POOL_TABLE=$(psqlq "SELECT table_schema||'.'||table_name
                      FROM information_schema.columns
                     WHERE column_name = 'session_reservation'
                     LIMIT 1;")
if [ -z "$POOL_TABLE" ] || printf '%s' "$POOL_TABLE" | grep -qi 'error'; then
  note "INCONCLUSIVO: não localizei a tabela de pools por 'session_reservation'."
  note "Saída bruta: ${POOL_TABLE:-<vazio>}"
  note "Fallback: consultar o agent-registry — curl -s localhost:3300/v1/pools -H \"x-tenant-id: $TENANT\""
else
  note "tabela de pools = $POOL_TABLE"
  note "CUIDADO — colisão de nome: a coluna Pool.max_concurrent_sessions é o THROTTLE de pool"
  note "webhook. As sessões-por-instância de IA vivem em PoolSkillSlot.config_json (slot 'current'),"
  note "e a API as expõe como 'deployed_max_concurrent_sessions' (pools.ts:213-217). Não são a mesma"
  note "grandeza; só a segunda responde a pergunta de unidade de licença."

  printf '\n%-28s %-8s %-12s %-10s\n' POOL KIND WH_THROTTLE RESERVA
  psqlq "SELECT COALESCE(pool_id,'?'), COALESCE(agent_kind,'-'),
                COALESCE(max_concurrent_sessions::text,'-'),
                COALESCE(session_reservation::text,'-')
           FROM $POOL_TABLE WHERE tenant_id = '$TENANT'
          ORDER BY agent_kind NULLS LAST, pool_id;" \
    | awk -F'|' '{printf "%-28s %-8s %-12s %-10s\n", $1, $2, $3, $4}'

  RESERVAS=$(psqlq "SELECT count(*) FROM $POOL_TABLE
                     WHERE tenant_id = '$TENANT' AND COALESCE(session_reservation,0) > 0;")
  note "Q2: pools com session_reservation > 0 → ${RESERVAS:-?}"
  note "    0 = piso/teto é FEATURE (compete por mérito de produto, não por urgência de defeito)."

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
head1 "Q4 — baldes de admissão: o compartilhado contém sessão humana?"

SHARED_N=$(redis SCARD "$TENANT:admission:shared")
note "SCARD $TENANT:admission:shared = ${SHARED_N:-?}"
redis --scan --pattern "$TENANT:admission:reserved:*" | sort | while read -r k; do
  [ -z "$k" ] && continue
  printf '  %-52s SCARD=%s\n' "$k" "$(redis SCARD "$k")"
done
KIND_AI=$(redis SCARD "$TENANT:admission:kind:ai")
note "SCARD $TENANT:admission:kind:ai = ${KIND_AI:-?}"
if [ -n "$SHARED_N" ] && [ "$SHARED_N" -gt 0 ] 2>/dev/null; then
  note "shared(${SHARED_N}) − kind:ai(${KIND_AI:-0}) ≈ sessões NÃO-IA no balde compartilhado."
  note "Qualquer valor > 0 é evidência direta de B-2 (sessão humana gateada por licença)."
else
  note "INCONCLUSIVO para B-2: balde vazio agora. Repetir com atendimento humano ativo."
fi
note "Atribuição por pool no shared:"
redis HGETALL "$TENANT:admission:shared_pools" | paste - - 2>/dev/null | sed 's/^/    /'

# ─────────────────────────────────────────────────────────────────────────────
head1 "Defeito A — snapshot × verdade do semáforo (retrato 'antes')"

printf '%-28s %-9s %-7s %-7s %-7s %-7s\n' POOL SNAP_AVAIL SNAP_BUSY SNAP_TOT ACTIVE_C FILA
# Lista capturada ANTES do laço (defesa em profundidade contra consumo de stdin).
SNAP_KEYS=$(redis --scan --pattern "$TENANT:pool:*:snapshot" | sort)
for k in $SNAP_KEYS; do
  [ -z "$k" ] && continue
  POOL=$(printf '%s' "$k" | sed "s/^$TENANT:pool://; s/:snapshot$//")
  SNAP=$(redis GET "$k")
  AV=$(jnum "$SNAP" available)
  BU=$(jnum "$SNAP" busy)
  TI=$(jnum "$SNAP" total_instances)
  AC=$(redis GET "$TENANT:pool:$POOL:active_count")
  QL=$(redis ZCARD "$TENANT:pool:$POOL:queue")
  printf '%-28s %-9s %-7s %-7s %-7s %-7s\n' \
         "$POOL" "${AV:--}" "${BU:--}" "${TI:--}" "${AC:-0}" "${QL:-0}"
done

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
  note "Nenhuma instância ocupada. Para reproduzir A em movimento, rodar durante"
  note "um atendimento — ou comparar ACTIVE_C > 0 com ocupação 0 (deriva parada)."
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
note "LEITURA: se a soma de SNAP_AVAIL entre pools que compartilham a MESMA instância"
note "for maior que (max_concurrent − ocupação) daquela instância, o defeito A está reproduzido."
note "Se ACTIVE_C divergir da ocupação real do semáforo, é a deriva do contador por pool."
hr
