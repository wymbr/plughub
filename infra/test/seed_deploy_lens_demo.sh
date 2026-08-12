#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Seed de demonstração — lente `deploy` do bench de Agentes (Arc 6 Fase 2).
#
# Ilumina a lente `deploy` com dado ALINHADO: segments (AI, role=primary) +
# evaluation_finalized (modo Oficial) compartilhando session_id, e flow_id =
# skill_id REAL — para que (a) a SÉRIE atribua por agent_key e (b) os MARKERS
# do agent-registry batam com o mesmo skill_id (resolve o ponto §8).
#
# Cria 3 dias de qualidade (queda → recuperação) em torno de um deploy registrado
# via API do agent-registry.
#
# ── DATAS RELATIVAS (2026-08-12) ──────────────────────────────────────────────
# Antes as datas eram LITERAIS (15/17/19-06-2026). Isso envelhece: em agosto a
# lente vinha vazia com o período default, e a demo dependia de alguém lembrar de
# mexer no seletor de período no meio da apresentação — o passo mais frágil do
# roteiro. Agora ancoram em HOJE, então o dado cai sempre dentro de uma janela
# recente e o seletor pode ficar quieto.
#
# Pinagem para reprodutibilidade: `ANCHOR=2026-06-19 bash …` fixa o último ponto.
#
# Uso:  bash infra/test/seed_deploy_lens_demo.sh
# Limpeza: automática no topo (prefixo dlz_). Necessária porque re-rodar com datas
#   novas, sem apagar, deixaria as linhas ANTIGAS vivas — o ReplacingMergeTree só
#   dedup dentro da mesma partição, e a partição é por data. Duas execuções em
#   dias diferentes produziriam pontos fantasma na curva.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

CH="${CH:-http://localhost:8123}"; CH_USER="${CH_USER:-plughub}"; CH_PASS="${CH_PASS:-plughub}"
DB="${DB:-plughub_demo}"
TENANT="${TENANT:-tenant_demo}"
REGISTRY="${REGISTRY:-http://localhost:3300}"
ANALYTICS="${ANALYTICS:-http://localhost:3500}"

# Skill REAL existente no registry (skill_id == flow_id que vamos gravar nos segments).
SKILL="${SKILL:-skill_atendimento_sac_v1}"
POOL="${POOL:-sac_ia}"
CURL="curl -s --max-time 15"
ch() { $CURL -u "$CH_USER:$CH_PASS" "$CH/" --data-binary "$1"; }

# ── Datas ─────────────────────────────────────────────────────────────────────
# `date -d` é GNU. Se não existir, ABORTA: datas silenciosamente erradas
# produziriam uma lente vazia que se lê como "a feature não funciona".
ANCHOR="${ANCHOR:-$(date -u +%F)}"
d() { date -u -d "$ANCHOR - $1 day" +%F 2>/dev/null; }
D3=$(d 2); D2=$(d 4); D1=$(d 6); FROM=$(d 8); TO=$(date -u -d "$ANCHOR + 1 day" +%F 2>/dev/null)
if [[ -z "$D1" || -z "$TO" ]]; then
  echo "⚠️  INCONCLUSIVO: \`date -d\` indisponível (precisa de GNU coreutils)." >&2
  exit 2
fi
echo "══ datas relativas a $ANCHOR: $D1 · $D2 (deploy) · $D3  |  janela $FROM..$TO ══"

# ── Limpeza das execuções anteriores ─────────────────────────────────────────
echo "══ limpando linhas dlz_ de execuções anteriores ══"
ch "ALTER TABLE $DB.segments DELETE WHERE tenant_id='$TENANT' AND session_id LIKE 'dlz_%' SETTINGS mutations_sync=1" >/dev/null
ch "ALTER TABLE $DB.evaluation_finalized DELETE WHERE tenant_id='$TENANT' AND session_id LIKE 'dlz_%' SETTINGS mutations_sync=1" >/dev/null
echo "  ✓ limpo"

echo "══ seed segments (AI, primary, flow_id=$SKILL) ══"
ch "INSERT INTO $DB.segments
    (segment_id,session_id,tenant_id,participant_id,pool_id,agent_type_id,flow_id,instance_id,role,agent_type,sequence_index,started_at,date)
    FORMAT JSONEachRow
{\"segment_id\":\"dlz_seg_1\",\"session_id\":\"dlz_s1\",\"tenant_id\":\"$TENANT\",\"participant_id\":\"dlz_p1\",\"pool_id\":\"$POOL\",\"agent_type_id\":\"$SKILL\",\"flow_id\":\"$SKILL\",\"instance_id\":\"dlz_i1\",\"role\":\"primary\",\"agent_type\":\"ai\",\"sequence_index\":0,\"started_at\":\"$D1 10:00:00.000\",\"date\":\"$D1\"}
{\"segment_id\":\"dlz_seg_2\",\"session_id\":\"dlz_s2\",\"tenant_id\":\"$TENANT\",\"participant_id\":\"dlz_p2\",\"pool_id\":\"$POOL\",\"agent_type_id\":\"$SKILL\",\"flow_id\":\"$SKILL\",\"instance_id\":\"dlz_i2\",\"role\":\"primary\",\"agent_type\":\"ai\",\"sequence_index\":0,\"started_at\":\"$D2 10:00:00.000\",\"date\":\"$D2\"}
{\"segment_id\":\"dlz_seg_3\",\"session_id\":\"dlz_s3\",\"tenant_id\":\"$TENANT\",\"participant_id\":\"dlz_p3\",\"pool_id\":\"$POOL\",\"agent_type_id\":\"$SKILL\",\"flow_id\":\"$SKILL\",\"instance_id\":\"dlz_i3\",\"role\":\"primary\",\"agent_type\":\"ai\",\"sequence_index\":0,\"started_at\":\"$D3 10:00:00.000\",\"date\":\"$D3\"}" >/dev/null && echo "  ✓ 3 segments"

echo "══ seed evaluation_finalized (Oficial, mesmos session_id) ══"
# Queda no dia do deploy ($D2) e recuperação depois ($D3) — para a linha ter forma.
ch "INSERT INTO $DB.evaluation_finalized
    (instance_id,result_id,session_id,tenant_id,campaign_id,final_score,finalize_reason,contestation_state,evaluated_agent_type,segment_id,form_version,round,process_duration_ms,timestamp,date)
    FORMAT JSONEachRow
{\"instance_id\":\"dlz_e1\",\"result_id\":\"dlz_r1\",\"session_id\":\"dlz_s1\",\"tenant_id\":\"$TENANT\",\"campaign_id\":\"dlz_camp\",\"final_score\":0.82,\"finalize_reason\":\"uncontested\",\"contestation_state\":\"uncontested\",\"evaluated_agent_type\":\"ai_agent\",\"segment_id\":\"dlz_seg_1\",\"form_version\":1,\"round\":1,\"process_duration_ms\":1000,\"timestamp\":\"$D1 10:05:00.000\",\"date\":\"$D1\"}
{\"instance_id\":\"dlz_e2\",\"result_id\":\"dlz_r2\",\"session_id\":\"dlz_s2\",\"tenant_id\":\"$TENANT\",\"campaign_id\":\"dlz_camp\",\"final_score\":0.61,\"finalize_reason\":\"uncontested\",\"contestation_state\":\"uncontested\",\"evaluated_agent_type\":\"ai_agent\",\"segment_id\":\"dlz_seg_2\",\"form_version\":1,\"round\":1,\"process_duration_ms\":1000,\"timestamp\":\"$D2 10:05:00.000\",\"date\":\"$D2\"}
{\"instance_id\":\"dlz_e3\",\"result_id\":\"dlz_r3\",\"session_id\":\"dlz_s3\",\"tenant_id\":\"$TENANT\",\"campaign_id\":\"dlz_camp\",\"final_score\":0.88,\"finalize_reason\":\"uncontested\",\"contestation_state\":\"uncontested\",\"evaluated_agent_type\":\"ai_agent\",\"segment_id\":\"dlz_seg_3\",\"form_version\":1,\"round\":1,\"process_duration_ms\":1000,\"timestamp\":\"$D3 10:05:00.000\",\"date\":\"$D3\"}" >/dev/null && echo "  ✓ 3 evaluation_finalized"

echo "══ registra deploy da skill (markers) via agent-registry ══"
# ⚠️ O deploy é carimbado com `now()` pelo registry — não há como backdatá-lo por
# esta API. Com as datas relativas isso deixou de ser problema: HOJE cai dentro da
# janela, e o marker aparece à direita da curva. (Com as datas literais de junho o
# marker caía 2 meses fora e não era desenhado.)
#
# ⚠️ `x-service-token` é OBRIGATÓRIO desde o G-PROBE (gate dual das mutações de
# config). Sem ele o registry devolve `unauthorized` — e até 2026-08-12 o script
# imprimia essa resposta e seguia para o ✅ final, então o seed anunciava sucesso
# sem nunca ter criado marker nenhum.
REG_TOKEN="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"
DEP=$($CURL -X POST "$REGISTRY/v1/skills/$SKILL/deploy" \
  -H "Content-Type: application/json" -H "X-Tenant-ID: $TENANT" -H "X-User-Id: seed_demo" \
  -H "x-service-token: $REG_TOKEN" \
  -d "{\"pool_ids\":[\"$POOL\"],\"notes\":\"seed deploy-lens demo\"}")
echo "  resposta: $(echo "$DEP" | head -c 300)"
DEPLOY_OK=1
case "$DEP" in
  *unauthorized*) DEPLOY_OK=0
    echo "  ❌ token recusado. Ajuste AGENT_REGISTRY_SERVICE_TOKEN= (valor em docker-compose.demo.yml)." ;;
  *"não encontrada"*|*"not found"*) DEPLOY_OK=0
    echo "  ❌ a skill $SKILL não existe no registry; ajuste SKILL=." ;;
esac

# ── Verificação — e ela GATEIA ────────────────────────────────────────────────
# ⚠️ A entidade da lente `deploy` é o **POOL**, não o skill: "a unidade da curva é o
# pool_id" (`reports_query.py:_compare_deploy_lens`), porque o mesmo skill pode rodar
# em N pools e deploy é pool-centric. Até 2026-08-12 esta linha consultava
# `entities=$SKILL`, devolvia `pts:0` — e o script imprimia ✅ logo abaixo. Uma
# verificação que não pode reprovar compra confiança sem dar nada.
echo "══ verificação pela API da lente (entidade = POOL) ══"
Q="$ANALYTICS/reports/agents/compare?tenant_id=$TENANT&from_dt=$FROM&to_dt=$TO&lens=deploy&entities=$POOL"
echo "  $Q"
RESP=$($CURL "$Q")
echo "$RESP" | (jq -c '{lens:.meta.lens,min:.meta.min_sample,pts:(.data.entities[0].series|length),markers:(.deploy_markers|length)}' 2>/dev/null || echo "$RESP" | head -c 300)
PTS=$(echo "$RESP" | jq -r '(.data.entities[0].series|length) // 0' 2>/dev/null)
[[ "$PTS" =~ ^[0-9]+$ ]] || PTS=0

echo
if [[ "$PTS" -eq 0 ]]; then
  echo "❌ SÉRIE VAZIA (pts=0) — o seed inseriu linhas mas a lente não as vê."
  echo "   Hipóteses, em ordem: (a) a entidade pedida não é um pool com dado;"
  echo "   (b) o JOIN evaluation_finalized.segment_id → segments não casou;"
  echo "   (c) filtro de origin ('live') ou de accessible_pools."
  echo "   Diagnóstico direto:"
  echo "     SELECT count() FROM $DB.evaluation_finalized f JOIN $DB.segments s"
  echo "       ON f.segment_id = s.segment_id WHERE f.session_id LIKE 'dlz_%';"
  exit 1
fi

MARKERS=$(echo "$RESP" | jq -r '(.deploy_markers|length) // 0' 2>/dev/null)
[[ "$MARKERS" =~ ^[0-9]+$ ]] || MARKERS=0

echo "✅ seed pronto — $PTS ponto(s) na série do pool '$POOL', $MARKERS marker(s)."
[[ "$DEPLOY_OK" -eq 0 ]] && echo "   ⚠️  mas SEM marker novo (ver o erro acima): a curva sai sem o triângulo."
echo "   No bench: Analytics → Agents, lente 'Deploy (quality)', marque o POOL '$POOL'"
echo "   (janela $FROM–$TO). Queda em $D2 + recuperação em $D3."
echo "   ⚠️  N=3 < min_sample=30 → o aviso 'Low sample' aparece de propósito."

# Duas contagens que surpreendem, e não são defeito:
if [[ "$PTS" -gt 3 ]]; then
  echo
  echo "ℹ️  $PTS pontos > os 3 que ESTE seed grava: a série é do POOL, e o"
  echo "   seed_epoch_demo.sh escreve no MESMO '$POOL' (mais 2 dias). A curva diária"
  echo "   mostra a união dos dois — esperado, e vale saber antes de narrar a tela."
fi
if [[ "$MARKERS" -gt 1 ]]; then
  echo
  echo "ℹ️  $MARKERS markers: \`SkillDeployment\` é APPEND-LOG e a limpeza do topo só"
  echo "   apaga linhas do ClickHouse — cada execução deste seed acrescenta um deploy,"
  echo "   todos carimbados com \`now()\`. Vários triângulos no mesmo dia = execuções"
  echo "   repetidas, não histórico. Para a demo, rode o seed UMA vez no dia."
fi
