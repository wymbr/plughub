#!/usr/bin/env bash
# Seed — cliente demo com histórico PESQUISÁVEL (valida H1 lista, H1 drill e H3 busca).
#
# O demo só tem webchat, que chega SEM identificação (caller.customer_id vazio) — a aba
# History mostra "Customer not identified". Para exercitar a busca precisamos de:
#   (a) um customer_id com contatos FECHADOS + mensagens (conteúdo mascarado), e
#   (b) a sessão viva chaveada nesse customer_id.
#
# Este script resolve (a) inserindo direto em analytics.sessions/messages (mesma prática
# dos outros seed_*.sh — analytics é sink, não config). Para (b), ver as instruções no
# fim: injete caller.customer_id pela aba Context do Console (admin/supervisor pode
# escrever caller.*), que é o mesmo write-back que o cadastro manual (C1a) vai automatizar.
#
# ── DATAS RELATIVAS (2026-08-12) ──────────────────────────────────────────────
# Eram literais (01/05/10/12-07-2026). O histórico do cliente é lido com filtro de
# período, então em agosto a aba vinha vazia e a demo dependia de mexer no seletor
# ao vivo. Agora ancoram em HOJE. Pinagem: `ANCHOR=2026-07-12 bash …`.
#
# Limpeza: automática (ids fixos sess_hist_* / proc_hist_*). NECESSÁRIA porque
# re-rodar com datas novas sem apagar deixaria as linhas antigas — o
# ReplacingMergeTree só dedup dentro da partição, e a partição é por data.
#
# Uso (raiz do repo, demo no ar):  bash infra/test/seed_customer_history_demo.sh
set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
DB="plughub_demo"
POOL="${POOL:-retencao_humano}"
CID="${CID:-cus_demo_maria}"
# NB: `< /dev/null` é obrigatório — com `exec -T` o clickhouse-client herda o stdin do
# terminal e fica ESPERANDO EOF após um INSERT ... VALUES inline (trava sem isso).
ch() { $COMPOSE exec -T clickhouse clickhouse-client "$@" < /dev/null; }
CH="ch"

# `date -d` é GNU. Ausente ⇒ aborta: data vazia daria INSERT inválido ou uma aba
# vazia que se lê como "a busca não funciona".
ANCHOR="${ANCHOR:-$(date -u +%F)}"
D1=$(date -u -d "$ANCHOR - 11 day" +%F 2>/dev/null)   # contato 001
D2=$(date -u -d "$ANCHOR - 7 day"  +%F 2>/dev/null)   # contato 002
D3=$(date -u -d "$ANCHOR - 2 day"  +%F 2>/dev/null)   # contato 003
D4=$(date -u -d "$ANCHOR - 1 day"  +%F 2>/dev/null)   # jornada em aberto
if [[ -z "$D1" || -z "$D4" ]]; then
  echo "⚠️  INCONCLUSIVO: \`date -d\` indisponível (precisa de GNU coreutils)." >&2
  exit 2
fi
echo "0) Datas relativas a $ANCHOR: contatos $D1 / $D2 / $D3 · jornada aberta $D4"

echo "0b) Limpando linhas de execuções anteriores ..."
$CH -q "ALTER TABLE ${DB}.sessions DELETE WHERE tenant_id='$TENANT'
        AND (session_id LIKE 'sess_hist_%' OR session_id LIKE 'proc_hist_%')
        SETTINGS mutations_sync=1"
$CH -q "ALTER TABLE ${DB}.messages DELETE WHERE tenant_id='$TENANT'
        AND session_id LIKE 'sess_hist_%' SETTINGS mutations_sync=1"

echo "1) Inserindo 3 contatos webchat FECHADOS para customer_id=$CID ..."
$CH -q "INSERT INTO ${DB}.sessions
  (tenant_id, session_id, channel, pool_id, customer_id, opened_at, closed_at, outcome, status, close_reason) VALUES
  ('$TENANT','sess_hist_001','webchat','$POOL','$CID','$D1 10:00:00','$D1 10:12:00','resolved','closed','agent_hangup'),
  ('$TENANT','sess_hist_002','webchat','$POOL','$CID','$D2 14:30:00','$D2 14:41:00','escalated','closed','agent_transfer'),
  ('$TENANT','sess_hist_003','webchat','$POOL','$CID','$D3 09:15:00','$D3 09:20:00','resolved','closed','customer_hangup')"

echo "2) Inserindo mensagens (conteúdo pesquisável) ..."
$CH -q "INSERT INTO ${DB}.messages
  (message_id, tenant_id, session_id, author_id, author_role, channel, content_type, visibility, content, timestamp, date) VALUES
  ('mh1','$TENANT','sess_hist_001','cust','customer','webchat','text','all','Estou com um problema na minha fatura de cobrança deste mês','$D1 10:00:30','$D1'),
  ('mh2','$TENANT','sess_hist_001','ag','agent','webchat','text','all','Vou verificar a sua cobrança agora mesmo, um momento','$D1 10:01:00','$D1'),
  ('mh3','$TENANT','sess_hist_002','cust','customer','webchat','text','all','Quero cancelar o meu plano por causa do valor da mensalidade','$D2 14:31:00','$D2'),
  ('mh4','$TENANT','sess_hist_002','ag','agent','webchat','text','all','Posso te oferecer um desconto para manter o plano ativo','$D2 14:32:00','$D2'),
  ('mh5','$TENANT','sess_hist_003','cust','customer','webchat','text','all','Minha internet está muito lenta desde ontem à noite','$D3 09:16:00','$D3'),
  ('mh6','$TENANT','sess_hist_003','ag','agent','webchat','text','all','Vou abrir um chamado técnico para a sua conexão de internet','$D3 09:17:00','$D3')"

echo "2b) Inserindo uma JORNADA (processo) EM ABERTO do cliente — 2 sessões sob a mesma raiz ..."
# root aberto (webhook, suspenso) + filho fechado → journey significativa (count>1) com
# open_count=1 → aparece na seção "Processos em aberto" da HistoricoTab (HJ). Distinta dos
# contatos avulsos acima (que ficam na lista de contatos).
$CH -q "INSERT INTO ${DB}.sessions
  (tenant_id, session_id, channel, pool_id, customer_id, root_session_id, opened_at, closed_at, outcome, status, close_reason) VALUES
  ('$TENANT','proc_hist_root','webhook','portabilidade_processo_ia','$CID','proc_hist_root','$D4 09:00:00',NULL,NULL,'suspended',''),
  ('$TENANT','proc_hist_child','webchat','retencao_humano','$CID','proc_hist_root','$D4 09:05:00','$D4 09:20:00','resolved','closed','agent_hangup')"

echo "2c) Semeando clientes no cadastro (identity.customers, PG) — p/ a busca do C1a ..."
# O identity.customers é separado do ClickHouse; a aba Cliente busca aqui. Schema criado
# pelo channel-gateway no boot (ensure_schema). ON CONFLICT p/ re-rodar.
$COMPOSE exec -T postgres psql -U plughub -d plughub_demo -v ON_ERROR_STOP=0 -c \
  "INSERT INTO identity.customers (customer_id, tenant_id, status, attributes) VALUES
     ('$CID','$TENANT','identified','{\"nome\": \"Maria Demo\"}'),
     ('cus_demo_joao','$TENANT','prospect','{\"nome\": \"João Teste\"}')
   ON CONFLICT (customer_id) DO UPDATE SET attributes = identity.customers.attributes || EXCLUDED.attributes;" \
  < /dev/null 2>&1 | tail -1 || echo "   (identity schema ausente? channel-gateway no ar?)"

echo "3) Conferência (sessões fechadas do cliente):"
$CH -q "SELECT session_id, channel, outcome, opened_at, closed_at
        FROM ${DB}.sessions FINAL
        WHERE tenant_id='$TENANT' AND customer_id='$CID'
        ORDER BY opened_at DESC FORMAT PrettyCompact"

cat <<EOF

Seed OK — customer_id = $CID (3 contatos fechados, 6 mensagens).

Para chavear a sessão viva neste cliente (identificar):
  Console → aba Context → botão "+" (add tag) → chave: caller.customer_id  valor: $CID  (confiança 1.0)
  (você está como Admin, que pode escrever caller.*)

Em ~3s a aba History passa a listar os 3 contatos. Então teste o H3:
  • buscar "cobrança"   → deve achar o contato 001
  • buscar "cancelar"   → contato 002
  • buscar "internet"   → contato 003
  • clique num resultado → abre a transcrição mascarada (drill H1)
  • toggle de filtros (data/canal/desfecho) e o × para limpar
EOF
