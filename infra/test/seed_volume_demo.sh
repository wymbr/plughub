#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# seed_volume_demo.sh — volume PARAMETRIZÁVEL para as lentes de Analytics
#
# POR QUE EXISTE. Todo seed do repositório era `INSERT` hardcoded de dezenas de
# linhas: com N=1 as lentes *funcionavam* mas não *mostravam nada*. Três sintomas
# concretos, todos registrados no roteiro da demo (§4):
#   • `availability` com um único login → curva de ocupação sem forma;
#   • o modo epoch avisando "Low sample" porque N=6 < `min_sample`=30;
#   • `quality_criteria` **sem seed em todo o repo** → radar vazio, lente fora do
#     roteiro por impossibilidade, não por escolha.
#
# Este script gera N contatos ao longo de DAYS dias e preenche as SETE tabelas que
# as lentes leem, com atribuição coerente entre elas (o mesmo `segment_id` liga
# segmento → avaliação → dimensões; o mesmo `instance_id` liga login → pausa).
# Sem essa coerência o volume aparece nas contagens e some nos JOINs.
#
# DETERMINÍSTICO: `random.seed(SEED)`. Duas execuções com o mesmo SEED e ANCHOR
# produzem exatamente as mesmas linhas — a demo não muda de números entre o ensaio
# e a apresentação, e uma diferença observada é sinal, não ruído.
#
# ── Uso ───────────────────────────────────────────────────────────────────────
#   bash infra/test/seed_volume_demo.sh                # N=200, 14 dias
#   N=500 DAYS=30 bash infra/test/seed_volume_demo.sh  # mais volume
#   ANCHOR=2026-08-12 SEED=7 bash infra/test/seed_volume_demo.sh   # pinado
#   CLEAN_ONLY=1 bash infra/test/seed_volume_demo.sh   # só apaga o que gerou
#
# Limpeza: TODOS os ids têm prefixo `vol_`, e o script apaga antes de inserir.
# Necessário porque as tabelas são `ReplacingMergeTree` particionadas por data: em
# outra data, a linha antiga NÃO é substituída — ela sobrevive ao lado.
#
# Veredicto: 0 = gerou e conferiu · 1 = conferência reprovou · 2 = inconclusivo.
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

CH="${CH:-http://localhost:8123}"; CH_USER="${CH_USER:-plughub}"; CH_PASS="${CH_PASS:-plughub}"
DB="${DB:-plughub_demo}"
TENANT="${TENANT:-tenant_demo}"

N="${N:-200}"
DAYS="${DAYS:-14}"
SEED="${SEED:-42}"
ANCHOR="${ANCHOR:-$(date -u +%F)}"
CLEAN_ONLY="${CLEAN_ONLY:-0}"

HUMAN_POOL="${HUMAN_POOL:-retencao_humano}"
AI_POOL="${AI_POOL:-sac_ia}"
AI_SKILL="${AI_SKILL:-skill_atendimento_sac_v1}"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
ch()  { curl -s --max-time 60 -u "$CH_USER:$CH_PASS" "$CH/" --data-binary "$1"; }
# O INSERT vai no CORPO, junto com os dados — não como query-param. Montar a URL
# com `tr ' ' '+'` funciona por acidente e quebra no primeiro caractere que exija
# escape; aqui não há URL para escapar.
chf() { { echo "INSERT INTO $DB.$1 FORMAT JSONEachRow"; cat "$2"; } \
        | curl -s --max-time 180 -u "$CH_USER:$CH_PASS" "$CH/" --data-binary @-; }

command -v python3 >/dev/null || { echo "⚠️  INCONCLUSIVO: python3 ausente." >&2; exit 2; }
date -u -d "$ANCHOR" +%F >/dev/null 2>&1 || { echo "⚠️  INCONCLUSIVO: \`date -d\` (GNU) ausente." >&2; exit 2; }

echo "══ seed_volume_demo — N=$N contatos · $DAYS dias · âncora $ANCHOR · seed $SEED ══"

# ── Limpeza ───────────────────────────────────────────────────────────────────
echo "── limpando linhas vol_ de execuções anteriores"
for spec in \
  "sessions:session_id" "segments:segment_id" \
  "evaluation_results:result_id" "evaluation_finalized:result_id" \
  "evaluation_dimension_scores:result_id" "session_signal:signal_id" \
  "agent_login_intervals:interval_id" "agent_pause_intervals:interval_id"
do
  t="${spec%%:*}"; c="${spec##*:}"
  ch "ALTER TABLE $DB.$t DELETE WHERE tenant_id='$TENANT' AND $c LIKE 'vol_%' SETTINGS mutations_sync=1" >/dev/null
done
echo "   ✓ limpo"

if [[ "$CLEAN_ONLY" == "1" ]]; then echo "✅ CLEAN_ONLY — nada gerado."; exit 0; fi

# ── Geração ───────────────────────────────────────────────────────────────────
echo "── gerando"
N="$N" DAYS="$DAYS" SEED="$SEED" ANCHOR="$ANCHOR" TENANT="$TENANT" TMP="$TMP" \
HUMAN_POOL="$HUMAN_POOL" AI_POOL="$AI_POOL" AI_SKILL="$AI_SKILL" python3 <<'PY'
import json, os, random
from datetime import date, datetime, timedelta

N       = int(os.environ["N"]);   DAYS = int(os.environ["DAYS"])
TENANT  = os.environ["TENANT"];   TMP  = os.environ["TMP"]
HP, AP  = os.environ["HUMAN_POOL"], os.environ["AI_POOL"]
SKILL   = os.environ["AI_SKILL"]
ANCHOR  = date.fromisoformat(os.environ["ANCHOR"])
random.seed(int(os.environ["SEED"]))

# ── Elenco ────────────────────────────────────────────────────────────────────
# Três humanos com PERFIS DIFERENTES de propósito: um board em que todo mundo tem
# o mesmo número não prova que a lente compara — prova que ela repete.
HUMANS = [
    # user_id,        login,                    resolução, AHT,  nota
    ("usr_vol_ana",   "ana.souza@plughub.local",   0.78, 1.00, 0.84),
    ("usr_vol_bruno", "bruno.lima@plughub.local",  0.52, 1.40, 0.66),
    ("usr_vol_carla", "carla.dias@plughub.local",  0.94, 0.78, 0.95),
]
# ── Por que as taxas são SORTEADAS POR COTA, não por moeda ────────────────────
# Medido na UI em 2026-08-12, com os valores originais (0.82/0.68/0.91) e n≈35 por
# agente: a tela mostrou 88% / 49% / 89%. Bruno saiu 19pp abaixo do projetado
# (2,5σ) e — pior para a demo — **Ana e Carla ficaram indistinguíveis** (88 vs 89),
# anulando exatamente o ponto de ter perfis diferentes.
#
# `random.random() < rrate` tem desvio-padrão ~8pp nesse n. Como o objetivo aqui
# não é simular incerteza e sim EXIBIR um contraste, o sorteio é ruído puro: ele só
# pode estragar. A cota abaixo entrega a taxa projetada com erro ≤1 contato, e a
# ordem Carla > Ana > Bruno passa a ser garantida, não provável.
#
# O spread também foi aberto (0.94 / 0.78 / 0.52): três barras precisam ser
# distinguíveis A DOIS METROS DE UM PROJETOR, não no segundo decimal.
def quota_cycle(rate, rng):
    """Lista de 100 booleanos com exatamente round(rate*100) verdadeiros, embaralhada."""
    n = round(rate * 100)
    seq = [True] * n + [False] * (100 - n)
    rng.shuffle(seq)
    return seq
PAUSES = [("intervalo","Intervalo"),("almoco","Almoço"),
          ("treinamento","Treinamento"),("reuniao","Reunião")]
ESCAL  = [("customer_request","Solicitação do cliente"),("out_of_scope","Fora do escopo"),
          ("needs_authorization","Falta de alçada"),("technical_issue","Problema técnico"),
          ("specialist_needed","Requer especialista"),("retention","Retenção / insatisfação")]
DIMS   = [("acolhimento","Acolhimento",0.30),("diagnostico","Diagnóstico",0.30),
          ("resolucao","Resolução",0.25),("conformidade","Conformidade",0.15)]

rows = {k: [] for k in ("sessions","segments","evaluation_results","evaluation_finalized",
                        "evaluation_dimension_scores","session_signal",
                        "agent_login_intervals","agent_pause_intervals")}
def ts(dt): return dt.strftime("%Y-%m-%d %H:%M:%S.000")

# Cotas: uma por humano e uma por versão de IA. `cursor` conta os contatos já
# atribuídos a cada chave, para andar pelo ciclo.
CYCLE = {uid: quota_cycle(r, random) for uid, _l, r, _a, _q in HUMANS}
CYCLE["ai:1.0"] = quota_cycle(0.72, random)   # a v1.0 resolve menos
CYCLE["ai:2.0"] = quota_cycle(0.86, random)   # a v2.0 é melhor — é o ponto do epoch
cursor = {k: 0 for k in CYCLE}
def take(key):
    v = CYCLE[key][cursor[key] % 100]; cursor[key] += 1; return v

# Volume por dia com forma de semana: fim de semana ~35%. Uma curva chapada não
# exercita o eixo temporal de nenhuma lente.
days    = [ANCHOR - timedelta(days=DAYS - 1 - i) for i in range(DAYS)]
weights = [0.35 if d.weekday() >= 5 else 1.0 for d in days]
tot     = sum(weights)
per_day = [max(1, round(N * w / tot)) for w in weights]

# ── Disponibilidade: um login por humano por dia útil, com pausas dentro ──────
for d in days:
    if d.weekday() >= 5: continue
    for uid, login, *_ in HUMANS:
        inst = f"human-{uid}"
        lin  = datetime.combine(d, datetime.min.time()) + timedelta(hours=9)
        lout = lin + timedelta(hours=8)
        rows["agent_login_intervals"].append(dict(
            interval_id=f"vol_li_{uid}_{d}", tenant_id=TENANT, instance_id=inst,
            user_id=uid, user_login=login, agent_type_id="human_agent", pool_id=HP,
            logged_in_at=ts(lin), logged_out_at=ts(lout),
            duration_ms=8*3600*1000, date=str(d)))
        for k in range(random.randint(2, 4)):
            pid, plabel = random.choice(PAUSES)
            pstart = lin + timedelta(hours=random.randint(1, 7), minutes=random.randint(0, 55))
            pmin   = 60 if pid == "almoco" else random.choice([10, 15, 20, 30])
            rows["agent_pause_intervals"].append(dict(
                interval_id=f"vol_pi_{uid}_{d}_{k}", tenant_id=TENANT, instance_id=inst,
                agent_type_id="human_agent", pool_id=HP, reason_id=pid, reason_label=plabel,
                note=None, paused_at=ts(pstart), resumed_at=ts(pstart + timedelta(minutes=pmin)),
                duration_ms=pmin*60*1000, date=str(d)))

# ── Contatos ──────────────────────────────────────────────────────────────────
# A versão do skill vira na METADE da janela: é o que dá ao modo epoch duas
# épocas comparáveis, e à lente diária um "antes e depois" real.
flip, seq = days[len(days)//2], 0
for d, count in zip(days, per_day):
    for _ in range(count):
        seq += 1
        is_ai = random.random() < 0.42
        sid   = f"vol_s{seq:05d}"
        segid = f"vol_seg{seq:05d}"
        op    = datetime.combine(d, datetime.min.time()) + timedelta(
                    hours=random.randint(9, 17), minutes=random.randint(0, 59))

        if is_ai:
            ver   = "2.0" if d >= flip else "1.0"
            base  = 0.79 if ver == "2.0" else 0.70          # a v2.0 é melhor
            dur   = int(random.gauss(210, 60) * 1000)
            resolved = take(f"ai:{ver}")
            pool, atype, aid, uid, login = AP, "ai", SKILL, "", ""
        else:
            uid, login, rrate, amult, qbase = random.choice(HUMANS)
            base  = qbase
            dur   = int(random.gauss(480 * amult, 120) * 1000)
            resolved = take(uid)
            pool, atype, aid, ver = HP, "human", "human_agent", ""

        dur = max(30_000, dur)
        esc = (not resolved) and random.random() < 0.55
        er, _erl = random.choice(ESCAL) if esc else ("", "")
        outcome = "resolved" if resolved else ("escalated" if esc else "failed")

        # `issue_status` DERIVA do outcome — nunca é sorteado à parte.
        # Antes: `random.choice(WRAPUP) if resolved else …`, que carimbava
        # `issue_status='cancelado'` em metade dos segmentos `outcome='resolved'`.
        # Dado autocontraditório: a lente `wrapup` (outcome × issue_status) exibiria
        # uma célula "resolvido/cancelado" que não pode existir na operação real, e
        # ninguém olhando a tela saberia dizer se o defeito era do seed ou da lente.
        if not is_ai:
            issue = "resolvido" if resolved else ("escalado" if esc else
                    random.choice(["pendente", "cancelado"]))
        else:
            issue = ""

        rows["sessions"].append(dict(
            session_id=sid, tenant_id=TENANT, channel="webchat", pool_id=pool,
            customer_id=f"vol_cus_{seq % 60:03d}", opened_at=ts(op),
            closed_at=ts(op + timedelta(milliseconds=dur)), close_reason="agent_hangup",
            outcome=outcome, status="closed", wait_time_ms=random.randint(0, 45_000),
            handle_time_ms=dur, date=str(d)))
        rows["segments"].append(dict(
            segment_id=segid, session_id=sid, tenant_id=TENANT,
            participant_id=f"vol_p{seq:05d}", pool_id=pool, agent_type_id=aid,
            flow_id=(SKILL if is_ai else ""), deploy_version=ver, channel="webchat",
            user_id=uid, user_login=login, instance_id=(f"human-{uid}" if uid else f"vol_ai_{seq%5}"),
            role="primary", agent_type=atype, sequence_index=0,
            started_at=ts(op), ended_at=ts(op + timedelta(milliseconds=dur)),
            duration_ms=dur, outcome=outcome, close_reason="agent_hangup",
            issue_status=issue, escalation_reason=er,
            wrapup_summary=("Cliente atendido; caso registrado." if issue else None),
            date=str(d)))

        # ── Avaliação ─────────────────────────────────────────────────────────
        # IA a 95%: o modo epoch precisa de ≥30 POR VERSÃO (min_sample), e é a
        # única forma de o gráfico parar de exibir "Low sample". Humano a 25%,
        # que é a taxa de amostragem plausível de uma campanha real.
        if random.random() < (0.95 if is_ai else 0.25):
            rid, iid = f"vol_r{seq:05d}", f"vol_i{seq:05d}"
            score = min(0.99, max(0.25, random.gauss(base, 0.09)))
            when  = op + timedelta(milliseconds=dur, minutes=5)
            common = dict(result_id=rid, instance_id=iid, session_id=sid, tenant_id=TENANT,
                          evaluator_id="agente_avaliacao_v1", form_id="form_vol_sac",
                          campaign_id="camp_vol_demo", eval_status="completed",
                          timestamp=ts(when), date=str(d))
            rows["evaluation_results"].append(dict(
                **common, overall_score=round(score, 4), locked=1, compliance_flags=[]))
            rows["evaluation_finalized"].append(dict(
                instance_id=iid, result_id=rid, session_id=sid, tenant_id=TENANT,
                campaign_id="camp_vol_demo", final_score=round(score, 4),
                finalize_reason="uncontested", contestation_state="uncontested",
                evaluated_agent_type=("ai_agent" if is_ai else "human_agent"),
                segment_id=segid, form_version=1, round=1, process_duration_ms=1000,
                timestamp=ts(when), date=str(d)))
            # As 4 dimensões — é isto que destrava a lente `quality_criteria`, que
            # até 2026-08-12 não tinha seed em NENHUM lugar do repositório.
            for did, dname, w in DIMS:
                ds = min(1.0, max(0.0, random.gauss(score, 0.11)))
                rows["evaluation_dimension_scores"].append(dict(
                    **common, dimension_id=did, dimension_name=dname,
                    score=round(ds, 4), weight=w))

        # ── NPS (grão segmento) ───────────────────────────────────────────────
        if random.random() < 0.38:
            nps = random.choices([10,9,8,7,6,5,3], weights=[26,24,14,10,8,10,8])[0]
            rows["session_signal"].append(dict(
                signal_id=f"vol_sig{seq:05d}", tenant_id=TENANT, session_id=sid,
                grain="segment", segment_id=segid,
                agent_key=(uid or SKILL), pool_id=pool, source="survey",
                metric="nps", value_num=nps, value_label=None,
                scale_min=0, scale_max=10,
                session_at=ts(op), captured_at=ts(op + timedelta(milliseconds=dur)),
                origin_session_id=None, journey_id=None, date=str(d)))

for name, data in rows.items():
    with open(f"{TMP}/{name}.jsonl", "w", encoding="utf-8") as fh:
        for r in data:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"   {name:32s} {len(data):6d}")
PY
[[ $? -eq 0 ]] || { echo "⚠️  INCONCLUSIVO: a geração falhou." >&2; exit 2; }

# ── Carga ─────────────────────────────────────────────────────────────────────
echo "── carregando no ClickHouse"
LOAD_FAIL=0
for t in sessions segments evaluation_results evaluation_finalized \
         evaluation_dimension_scores session_signal \
         agent_login_intervals agent_pause_intervals; do
  OUT=$(chf "$t" "$TMP/$t.jsonl")
  if [[ -n "$OUT" ]]; then
    echo "   ❌ $t: $(echo "$OUT" | head -c 200)"; LOAD_FAIL=1
  else
    echo "   ✓ $t"
  fi
done
[[ "$LOAD_FAIL" -eq 1 ]] && { echo "❌ carga falhou — nada a conferir."; exit 1; }

# ── Conferência — e ela GATEIA ────────────────────────────────────────────────
# Cada asserção existe porque a lente correspondente falha de um jeito MUDO:
# devolve série vazia ou "Low sample", que se lê como "a feature não funciona".
echo
echo "── conferência"
PASS=0; FAIL=0
q()   { ch "$1"; }
ok()  { echo "   ✅ $1"; PASS=$((PASS+1)); }
bad() { echo "   ❌ $1"; FAIL=$((FAIL+1)); }

CS=$(q "SELECT count() FROM $DB.segments FINAL WHERE tenant_id='$TENANT' AND segment_id LIKE 'vol_%'")
[[ "${CS:-0}" -ge $((N * 8 / 10)) ]] && ok "segments: $CS" || bad "segments: $CS (esperado ~$N)"

for v in 1.0 2.0; do
  C=$(q "SELECT count() FROM $DB.evaluation_finalized f FINAL
         INNER JOIN (SELECT segment_id, deploy_version FROM $DB.segments FINAL
                     WHERE tenant_id='$TENANT' AND deploy_version='$v') s
         ON f.segment_id = s.segment_id
         WHERE f.tenant_id='$TENANT' AND f.result_id LIKE 'vol_%'")
  # 30 não é número redondo: é o `min_sample` da lente. Abaixo dele o epoch
  # exibe "Low sample" e o gráfico se recusa a afirmar — de propósito.
  [[ "${C:-0}" -ge 30 ]] \
    && ok "epoch v$v: $C avaliações (≥ min_sample=30 — sem 'Low sample')" \
    || bad "epoch v$v: $C < 30 — a lente sairá com 'Low sample'. Aumente N."
done

# O CONTRASTE entre agentes é a propriedade que a demo precisa, e ela falha de um
# jeito silencioso: três barras quase iguais parecem um board funcionando. Medido
# em 2026-08-12 na UI, com taxas sorteadas por moeda, Ana e Carla saíram 88% e 89%
# — indistinguíveis a dois metros de um projetor. Esta asserção existe para que
# isso não volte sem ficar vermelho.
read -r RMIN RMAX <<<"$(q "
  SELECT round(min(r)*100) , round(max(r)*100) FROM (
    SELECT user_id, countIf(outcome='resolved')/count() AS r
    FROM $DB.segments FINAL
    WHERE tenant_id='$TENANT' AND segment_id LIKE 'vol_%' AND agent_type='human'
    GROUP BY user_id)" | tr '\t' ' ')"
if [[ "${RMIN:-}" =~ ^[0-9]+$ && "${RMAX:-}" =~ ^[0-9]+$ ]]; then
  SPREAD=$((RMAX - RMIN))
  [[ "$SPREAD" -ge 25 ]] \
    && ok "contraste entre humanos: ${RMIN}% a ${RMAX}% (spread ${SPREAD}pp — visível no projetor)" \
    || bad "contraste de apenas ${SPREAD}pp (${RMIN}%–${RMAX}%): as barras ficam indistinguíveis
        e o board não demonstra comparação. Abra o spread em HUMANS[] ou aumente N."
else
  bad "não consegui medir o contraste entre humanos (leitura: '${RMIN:-}' '${RMAX:-}')"
fi

CI=$(q "SELECT count() FROM $DB.segments FINAL WHERE tenant_id='$TENANT' AND segment_id LIKE 'vol_%'
        AND outcome='resolved' AND issue_status NOT IN ('resolvido','')")
[[ "${CI:-1}" -eq 0 ]] \
  && ok "wrapup coerente: nenhum segmento 'resolved' com disposição contraditória" \
  || bad "$CI segmentos com outcome='resolved' e issue_status divergente — a lente wrapup
        exibiria uma célula que não existe na operação real"

CD=$(q "SELECT count() FROM $DB.evaluation_dimension_scores FINAL WHERE tenant_id='$TENANT' AND result_id LIKE 'vol_%'")
[[ "${CD:-0}" -gt 0 ]] \
  && ok "quality_criteria: $CD notas de dimensão (a lente sai do ❌ do roteiro)" \
  || bad "quality_criteria: 0 — o radar seguirá vazio"

CL=$(q "SELECT count(DISTINCT instance_id) FROM $DB.agent_login_intervals FINAL WHERE tenant_id='$TENANT' AND interval_id LIKE 'vol_%'")
[[ "${CL:-0}" -ge 3 ]] && ok "availability: $CL agentes com histórico de login" || bad "availability: $CL agentes"

CP=$(q "SELECT count(DISTINCT reason_id) FROM $DB.agent_pause_intervals FINAL WHERE tenant_id='$TENANT' AND interval_id LIKE 'vol_%'")
[[ "${CP:-0}" -ge 3 ]] && ok "pause_reason: $CP motivos distintos" || bad "pause_reason: $CP motivos"

CE=$(q "SELECT count() FROM $DB.segments FINAL WHERE tenant_id='$TENANT' AND segment_id LIKE 'vol_%' AND escalation_reason != ''")
[[ "${CE:-0}" -gt 0 ]] \
  && ok "escalation_reason: $CE segmentos (fecha o item 4c do roteiro)" \
  || bad "escalation_reason: 0 — a lente fica vazia"

CN=$(q "SELECT count() FROM $DB.session_signal FINAL WHERE tenant_id='$TENANT' AND signal_id LIKE 'vol_%'")
[[ "${CN:-0}" -gt 0 ]] && ok "nps: $CN sinais" || bad "nps: 0"

echo
echo "══ $PASS ok · $FAIL falha(s) ══"
if [[ $FAIL -gt 0 ]]; then
  echo "❌ o volume entrou mas alguma lente ficaria vazia ou sob o min_sample."
  exit 1
fi
cat <<EOF
✅ volume pronto — $N contatos em $DAYS dias, terminando em $ANCHOR.

   Analytics → Agents, período "últimos $DAYS dias":
     resolution / sessions_aht  → três humanos com perfis DIFERENTES, por COTA
                                  (Carla ~94% · Ana ~78% · Bruno ~52%)
     availability / pause_reason→ 8 h/dia útil, 2–4 pausas
     escalation_reason          → taxonomia real de \`agent_activity\`
     quality / quality_criteria → radar de 4 dimensões
     deploy (Por versão)        → v1.0 → v2.0 na metade da janela, SEM "Low sample"

   Determinístico: mesmo SEED + ANCHOR ⇒ mesmas linhas.
   Desfazer:  CLEAN_ONLY=1 bash infra/test/seed_volume_demo.sh
EOF
