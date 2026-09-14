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
# Credencial (2026-08-27): o `pool_auth` deixou de devolver irrestrito na ausencia
# de header. O shim anexa o Bearer SO nas chamadas a analytics-api. Ver _auth.sh.
source "$(dirname "$0")/_auth.sh"; plughub_auth_curl_shim

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

echo "══ registra o deploy (marker) por PROMOTE do pool ══"
# PID-08 (2026-09-14): este seed chamava `POST /v1/skills/:id/deploy`, que gravava um
# `SkillDeployment` sem tocar o slot — o marker afirmava na curva do pool um deploy que
# não mudava nada que roda (5 linhas assim foram apagadas). O deploy é por POOL: re-promove
# o que JÁ roda (mesmo skill, mesmo snapshot, mesma config), então o marker é verdadeiro e
# a produção não muda. Carimbado com `now()` pelo registry, como antes.
#
# ⚠️ `x-service-token` é OBRIGATÓRIO (gate de escrita do registry); com ele o autor é o
# `x-user-id` declarado.
REG_TOKEN="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"
RH=(-H "Content-Type: application/json" -H "X-Tenant-ID: $TENANT" -H "X-User-Id: seed_demo" -H "x-service-token: $REG_TOKEN")
SLOTS=$($CURL "$REGISTRY/v1/pools/$POOL/slots" -H "X-Tenant-ID: $TENANT")
NEXT_BODY=$(printf '%s' "$SLOTS" | python3 -c 'import json,sys
d = json.load(sys.stdin); cur = (d.get("slots") or d).get("current") or {}
if not cur.get("skill_id") or cur.get("yaml_snapshot") is None:
    print(""); raise SystemExit
print(json.dumps({"skill_id": cur["skill_id"], "config_json": cur.get("config_json") or {}, "yaml_snapshot": cur["yaml_snapshot"]}))' 2>/dev/null)
# ⚠️ Re-promover copia o `current` para o `previous`: se o `previous` guarda OUTRA versão,
# o alvo de rollback do pool é destruído. Medido no demo em 2026-09-14: `sac_ia` tem
# previous ≠ current. Por isso a guarda recusa por default e só segue com REPROMOTE_OK=1.
PREV_DIFERE=$(printf '%s' "$SLOTS" | python3 -c 'import json,sys
d = json.load(sys.stdin); s = d.get("slots") or d
chave = lambda x: json.dumps([(x or {}).get(k) for k in ("skill_id", "config_json", "yaml_snapshot")], sort_keys=True)
p = s.get("previous")
print("1" if p and p.get("skill_id") and chave(p) != chave(s.get("current")) else "0")' 2>/dev/null)
DEPLOY_OK=1
if [ -z "$NEXT_BODY" ]; then
  DEPLOY_OK=0
  echo "  ❌ o pool $POOL não tem slot current executável — não há o que re-promover (resposta: ${SLOTS:0:200})"
elif [ "$PREV_DIFERE" != "0" ] && [ "${REPROMOTE_OK:-0}" != "1" ]; then
  DEPLOY_OK=0
  echo "  ❌ o slot previous de $POOL guarda outra versão: re-promover apagaria o alvo de rollback."
  echo "     Nenhum marker registrado. Para aceitar a perda do rollback: REPROMOTE_OK=1 bash $0"
else
  SN=$($CURL -X PUT "$REGISTRY/v1/pools/$POOL/slots/next" "${RH[@]}" -d "$NEXT_BODY")
  PR=$($CURL -X POST "$REGISTRY/v1/pools/$POOL/promote" "${RH[@]}" -d '{}')
  echo "  set-next: $(echo "$SN" | head -c 160)"
  echo "  promote : $(echo "$PR" | head -c 160)"
  case "$PR" in
    *'"promoted"'*) ;;
    *) DEPLOY_OK=0; echo "  ❌ o promote não respondeu 'promoted' — marker NÃO registrado." ;;
  esac
fi

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
