#!/usr/bin/env bash
# report_registry_fossil_tables.sh — o que sobrou em `plughub_demo` da época em que o
# agent-registry morava lá.
#
# **READ-ONLY POR DESENHO.** Ele IMPRIME os `ALTER TABLE` que resolveriam, e não os
# executa. A separação entre medir e agir é o ponto: mover tabela é irreversível na
# prática (quem depender dela quebra depois, longe daqui), e a lista de candidatos é
# heurística — só um humano decide se `X` em `plughub_demo` é o fóssil do registry ou
# uma tabela viva de OUTRO serviço que por acaso tem o mesmo nome.
#
# CONTEXTO. O agent-registry roda `prisma db push --accept-data-loss` no boot, que
# dropa tabelas do `public` fora do seu Prisma. Por isso foi movido para um banco
# próprio (`plughub_registry`, docker-compose.demo.yml:551-555). O que ele havia criado
# em `plughub_demo` ficou lá: mesma estrutura, dados congelados, **nenhum escritor**.
#
# POR QUE ISSO IMPORTA MAIS QUE ESPAÇO EM DISCO. Em 2026-08-02 descobriu-se que
# `measure_capacity_licensing_baseline.sh` media Q1/Q2 do arco de capacidade contra
# `plughub_demo.public.pools` — o fóssil. Foi Q2 que decidiu ADIAR a fatia 4. A consulta
# não deu erro, não veio vazia, e as linhas eram plausíveis: o modo de falha perfeito.
# Enquanto a tabela responder a `FROM pools`, a próxima medição erra igual.
#
# O CONSERTO PROPOSTO É RENOMEAR, NÃO DROPAR. Mover para um schema de quarentena faz
# `FROM pools` em `plughub_demo` falhar ALTO (relation does not exist) em vez de
# devolver dado velho — troca uma resposta errada silenciosa por um erro barulhento,
# que é o que este arco inteiro persegue. E preserva os dados, caso alguém precise
# comparar história.
#
# Uso:  bash infra/test/report_registry_fossil_tables.sh
# Pré:  postgres no ar.

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
LIVE_DB="plughub_registry"      # onde o agent-registry realmente escreve
FOSSIL_DB="plughub_demo"        # onde ele escrevia antes da separação
QUARANTINE="fossil_pre_registry_split"

q() { $DC exec -T postgres psql -U plughub -d "$1" -tA -c "$2" < /dev/null 2>&1; }

echo "── pré-condições ───────────────────────────────────────────────────────────"
if [ "$(q "$LIVE_DB" 'SELECT 1')" != "1" ]; then
  echo "   ⚠️  INCONCLUSIVO: $LIVE_DB inacessível — sem ele não há com o que comparar."
  exit 2
fi
# O discriminador do banco VIVO. Se ele mudar de lugar de novo, este relatório tem de
# parar em vez de comparar contra o banco errado — que é o defeito que ele investiga.
if [ "$(q "$LIVE_DB" "SELECT to_regclass('public._prisma_migrations') IS NOT NULL;")" != "t" ]; then
  echo "   ⚠️  INCONCLUSIVO: $LIVE_DB não tem _prisma_migrations."
  echo "      O DATABASE_URL autoritativo:"
  echo "        $DC exec -T agent-registry sh -lc 'echo \$DATABASE_URL'"
  exit 2
fi
echo "   ✅ $LIVE_DB é o banco do agent-registry"

LIVE_TABLES="$(q "$LIVE_DB" \
  "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1;")"
FOSSIL_TABLES="$(q "$FOSSIL_DB" \
  "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1;")"
CANDIDATES="$(comm -12 <(printf '%s\n' "$LIVE_TABLES") <(printf '%s\n' "$FOSSIL_TABLES"))"

echo
echo "── candidatos: existem nos DOIS bancos, em public ──────────────────────────"
if [ -z "${CANDIDATES// }" ]; then
  echo "   ✅ nenhum — não há resíduo da separação. Nada a fazer."
  exit 0
fi

# HOMONÍMIA NÃO É PROVA. Estar nos dois bancos torna a tabela SUSPEITA, não culpada:
# outro serviço de `plughub_demo` pode ter criado, legitimamente, algo com o mesmo nome.
#
# ⚠️ CORRIGIDO 2026-08-02, na primeira execução: a coluna dizia "DEPENDENTES_VIVOS" e
# contava QUALQUER FK apontando para a tabela — inclusive as vindas das OUTRAS
# candidatas. `pools` marcou 1, e o dependente era `pool_skill_slots`, que é fóssil
# também. Um número que soma o que deveria excluir não é evidência de nada, e este
# relatório existe justamente para não repetir o erro que investiga.
# Agora: dependentes FORA da lista de candidatos, e os NOMES, não só a contagem.
NOT_IN=$(printf "'%s'," $CANDIDATES); NOT_IN="${NOT_IN%,}"

printf '\n%-24s %8s %8s  %s\n' TABELA LIN_FOSSIL LIN_VIVA 'DEPENDENTES FORA DA LISTA'
printf '%s\n' "$CANDIDATES" | while read -r t; do
  [ -z "$t" ] && continue
  n_f=$(q "$FOSSIL_DB" "SELECT count(*) FROM public.\"$t\";")
  n_l=$(q "$LIVE_DB"   "SELECT count(*) FROM public.\"$t\";")
  ext=$(q "$FOSSIL_DB" "
    SELECT coalesce(string_agg(DISTINCT r.relname, ', '), '—')
      FROM pg_constraint c
      JOIN pg_class  r ON r.oid = c.conrelid
      JOIN pg_class  f ON f.oid = c.confrelid
     WHERE c.contype='f' AND f.relname='$t'
       AND r.relname <> '$t' AND r.relname NOT IN ($NOT_IN);")
  printf '%-24s %8s %8s  %s\n' "$t" "${n_f:-?}" "${n_l:-?}" "${ext:-?}"
done

echo
echo "── leitura ─────────────────────────────────────────────────────────────────"
echo "   · DEPENDENTES FORA DA LISTA ≠ '—'  → PARE: há tabela viva de $FOSSIL_DB"
echo "     apontando para esta. Não é fóssil, ou não é só fóssil."
echo "   · '—' em todas → o grafo de FK é FECHADO entre as candidatas, assinatura de"
echo "     um conjunto criado junto e abandonado junto. Mover TODAS de uma vez preserva"
echo "     as FKs (SET SCHEMA leva a constraint junto)."
echo "   · LIN_FOSSIL ≪ LIN_VIVA → divergiram, como se espera de dado congelado."
echo "     Igualdade NÃO absolve: um fóssil recém-separado é idêntico ao vivo."
echo "   · A prova de que não há escritor é de CÓDIGO, não deste relatório: nenhum"
echo "     serviço emite SQL contra estas tabelas em $FOSSIL_DB (a analytics-api usa a"
echo "     REST do agent-registry). Conferir de novo antes de aplicar."

echo
echo "── remédio PROPOSTO (não executado) ────────────────────────────────────────"
echo "   Quarentena por SCHEMA, não DROP: preserva o dado e faz \`FROM pools\` falhar"
echo "   alto em vez de devolver o retrato velho. Reversível (SET SCHEMA public)."
echo
echo "   $DC exec -T postgres psql -U plughub -d $FOSSIL_DB <<'SQL'"
echo "   CREATE SCHEMA IF NOT EXISTS $QUARANTINE;"
printf '%s\n' "$CANDIDATES" | while read -r t; do
  [ -z "$t" ] && continue
  echo "   ALTER TABLE public.\"$t\" SET SCHEMA $QUARANTINE;"
done
echo "   SQL"
echo
echo "   DEPOIS de aplicar, o portão que prova que funcionou — tem de FALHAR:"
echo "     $DC exec -T postgres psql -U plughub -d $FOSSIL_DB -c 'SELECT 1 FROM pools LIMIT 1;'"
echo "     (esperado: ERROR relation \"pools\" does not exist — o erro É o conserto)"
echo "   E o que NÃO pode ter mudado:"
echo "     bash infra/test/measure_capacity_licensing_baseline.sh tenant_demo"
echo "     (usa $LIVE_DB; deve seguir idêntico — se quebrar, algo ainda apontava para o fóssil)"
