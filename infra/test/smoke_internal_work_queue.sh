#!/usr/bin/env bash
# ADR adr-internal-work-queue-author-bound — fases I1 + I3.
#
# Prova, contra o DADO (não contra o código):
#   I1a  o espelho `retencao_humano-int` foi auto-provisionado com purpose=internal,
#        dispatch_mode=pull, agent_kind=human e os canais do pai;
#   I1b  criar um pool manual com sufixo `-int` é REJEITADO (422) — o sufixo só é
#        garantia enquanto ninguém além do auto-provisionamento o usa;
#   I1c  desligar `internal_queue_enabled` sem `?force_disable=true` é RECUSADO (422)
#        — o registry não enxerga a fila, e não-verificável é tratado como pendência;
#   I1d  hook `dispatch: detached` + `side: agent` num pool SEM fila interna é
#        rejeitado (422) na configuração, não em runtime;
#   I3   o wrap-up cai no ESPELHO e não mais no `formfill_demo`: o item de pull nasce
#        em `retencao_humano-int` e o segmento humano herda esse pool_id.
#
# PRÉ-REQUISITO IMPORTANTE (seed-if-absent). `retencao_humano` já existe no DB, então
# a mudança no `infra/registry/tenant_demo.yaml` NÃO se aplica sozinha no restart — o
# RegistrySyncer só semeia DB vazio. Este script aplica via PUT, que é o caminho
# oficial. Sem isto, a flag fica só no YAML e nada acontece (falha por ausência).
#
# Uso (raiz do repo, demo no ar), em DOIS tempos — a ordem importa:
#   1) bash infra/test/smoke_internal_work_queue.sh          → aplica config e marca o
#      instante do promote em /tmp/_iwq_promoted_at
#   2) faça UM atendimento em retencao_humano no Console e encerre-o
#   3) bash infra/test/smoke_internal_work_queue.sh verify   → só lê, e olha apenas os
#      segmentos POSTERIORES ao promote
#
# Por que separado: rodar o script inteiro de novo re-promove e reseta o marcador, e a
# tabela passa a misturar segmentos de antes e depois do fix — foi assim que uma
# execução com o flow velho ainda congelado no slot pareceu resultado do novo.
set -euo pipefail

MODE="${1:-apply}"
MARKER=/tmp/_iwq_promoted_at

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CH_DB="plughub_demo"
AR="http://localhost:3300"
SVC="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"
CH="$COMPOSE exec -T clickhouse clickhouse-client"

PARENT="retencao_humano"
MIRROR="${PARENT}-int"

thw=(-H "x-tenant-id: $TENANT" -H "x-service-token: $SVC")
trh=(-H "x-tenant-id: $TENANT")

pass=0; fail=0
ok()   { echo "   ✅ $1"; pass=$((pass+1)); }
bad()  { echo "   ❌ $1"; fail=$((fail+1)); }

if [ "$MODE" = "verify" ]; then
  SINCE=$(cat "$MARKER" 2>/dev/null || echo "")
  [ -n "$SINCE" ] || { echo "Sem marcador — rode o modo 'apply' antes."; exit 1; }
  echo "== VERIFY — segmentos humanos criados APÓS o promote ($SINCE) =="
  $CH -q "SELECT pool_id, session_id, started_at, ended_at, outcome \
          FROM ${CH_DB}.segments FINAL \
          WHERE tenant_id='$TENANT' AND agent_type='human' \
            AND started_at > toDateTime('$SINCE') \
          ORDER BY started_at FORMAT PrettyCompact"
  echo
  N_MIRROR=$($CH -q "SELECT count() FROM ${CH_DB}.segments FINAL \
      WHERE tenant_id='$TENANT' AND agent_type='human' AND pool_id='$MIRROR' \
        AND started_at > toDateTime('$SINCE')")
  N_OLD=$($CH -q "SELECT count() FROM ${CH_DB}.segments FINAL \
      WHERE tenant_id='$TENANT' AND agent_type='human' AND pool_id='formfill_demo' \
        AND started_at > toDateTime('$SINCE')")
  echo "   wrap-up no espelho: $N_MIRROR    |    ainda em formfill_demo: $N_OLD"
  if [ "$N_MIRROR" -gt 0 ] && [ "$N_OLD" -eq 0 ]; then
    echo "   ✅ I3 em vigor — o wrap-up nasce no pool interno"; exit 0
  fi
  [ "$N_MIRROR" -eq 0 ] && [ "$N_OLD" -eq 0 ] && \
    echo "   ⚠️  nenhum segmento humano após o promote — o atendimento foi feito?"
  echo "   ❌ I3 NÃO está em vigor"; exit 1
fi

echo "== I1 — flag + espelho auto-provisionado =="

echo "1) PUT $PARENT { internal_queue_enabled: true, hooks.on_human_end[0].context } ..."
# `acw_timeout_hours` (I5): prazo do ACW é fato DESTE pool, como o formulário. Ele vem
# junto no PUT porque o `hooks` é substituído por inteiro — omiti-lo aqui apagaria a
# config e o engine voltaria ao default de 24 h, em silêncio para quem lê a tela.
#
# DISPATCH/ACW_HOURS são parametrizáveis pelo MESMO motivo, ao contrário: a Camada F roda
# com `detached` + prazo curto, e rodar este script para revalidar a I1/I3 devolveria o
# pool a `inline`/24 h sem dizer nada — a medição seguinte mediria outra configuração.
DISPATCH="${DISPATCH:-inline}"
ACW_HOURS="${ACW_HOURS:-24}"
echo "   (hooks: dispatch=$DISPATCH acw_timeout_hours=$ACW_HOURS — override via env)"
HOOKS="{\"on_human_start\":[],\"on_human_end\":[{\"pool\":\"wrapup_detached_ia\",\"side\":\"agent\",\"dispatch\":\"$DISPATCH\",\"context\":{\"dialog_form_id\":\"dialog_wrapup_v1\",\"acw_timeout_hours\":\"$ACW_HOURS\"}}],\"on_contact_end\":[{\"pool\":\"nps_ia\",\"side\":\"customer\",\"nps_on_disconnect\":\"skip\"}],\"post_human\":[]}"
C=$(curl -s -o /tmp/_iwq_put -w '%{http_code}' -X PUT "$AR/v1/pools/$PARENT" "${thw[@]}" \
  -H 'content-type: application/json' \
  -d "{\"internal_queue_enabled\": true, \"hooks\": ${HOOKS}}")
echo "   HTTP $C"
[ "$C" = "200" ] && ok "flag + hook.context aceitos" || { bad "PUT falhou"; cat /tmp/_iwq_put; }

echo "2a) O PAI aceitou a flag? (se vier AUSENTE, o binário/Zod do registry está velho)"
P=$(curl -s "$AR/v1/pools/$PARENT" "${trh[@]}")
FLAG=$(echo "$P" | python3 -c "import sys,json;print(json.load(sys.stdin).get('internal_queue_enabled','<AUSENTE>'))" 2>/dev/null || echo '<ERRO>')
echo "   internal_queue_enabled=$FLAG"
if [ "$FLAG" = "True" ] || [ "$FLAG" = "true" ]; then ok "flag persistida no pai"
else bad "flag NÃO persistida ($FLAG) — rebuild do agent-registry com --no-cache (arquivos novos)"; fi
HOOKCTX=$(echo "$P" | python3 -c "
import sys,json
h=(json.load(sys.stdin).get('hooks') or {}).get('on_human_end') or [{}]
print(json.dumps(h[0].get('context','<AUSENTE>')))" 2>/dev/null || echo '<ERRO>')
echo "   hooks.on_human_end[0].context=$HOOKCTX"
case "$HOOKCTX" in *dialog_form_id*) ok "PoolHookEntry.context persistido";;
  *) bad "context do hook descartado — @plughub/schemas velho na imagem";; esac

echo "2b) O espelho $MIRROR existe e tem os campos derivados?"
# NB1: o veredito precisa contar em `fail` — assert que só imprime é teste que não
# pode reprovar (o padrão que o TODO já nomeou três vezes).
# NB2: os dados vão por ARQUIVO, não por pipe. `cmd | python3 - <<'PY'` NÃO funciona:
# o heredoc substitui o pipe como stdin, o script vem do heredoc e o json.load(stdin)
# lê vazio — reportando "ausente" sobre um recurso que existe. Foi assim que a
# primeira execução deste smoke deu falso negativo no espelho.
curl -s "$AR/v1/pools/$MIRROR" "${trh[@]}" -o /tmp/_iwq_mirror
if python3 - /tmp/_iwq_mirror <<'PY'
import json,sys
raw = open(sys.argv[1]).read()
try:
    p = json.loads(raw)
    assert "pool_id" in p
except Exception:
    print(f"   espelho AUSENTE / resposta inesperada: {raw[:160]}"); sys.exit(1)
exp = {"purpose":"internal","dispatch_mode":"pull","agent_kind":"human",
       "status":"active","internal_queue_enabled":False}
bad = {k:(v,p.get(k)) for k,v in exp.items() if p.get(k) != v}
print(f"   canais={p.get('channel_types')} sla={p.get('sla_target_ms')}")
if bad:
    print(f"   campos divergentes (esperado,obtido): {bad}"); sys.exit(1)
PY
then ok "espelho derivado corretamente"; else bad "espelho ausente ou divergente"; fi

echo "3) Criar pool manual com sufixo -int deve ser REJEITADO ..."
C=$(curl -s -o /tmp/_iwq_sfx -w '%{http_code}' -X POST "$AR/v1/pools" "${thw[@]}" \
  -H 'content-type: application/json' \
  -d '{"pool_id":"pirata-int","channel_types":["webchat"],"sla_target_ms":60000}')
[ "$C" = "422" ] && ok "sufixo reservado (422)" || bad "esperado 422, veio $C"

echo "4) Desligar a flag SEM force_disable deve ser RECUSADO ..."
C=$(curl -s -o /tmp/_iwq_dis -w '%{http_code}' -X PUT "$AR/v1/pools/$PARENT" "${thw[@]}" \
  -H 'content-type: application/json' -d '{"internal_queue_enabled": false}')
[ "$C" = "422" ] && ok "desligamento recusado sem confirmação (422)" || bad "esperado 422, veio $C"

echo "5) Hook detached+agent num pool SEM fila interna deve ser rejeitado ..."
C=$(curl -s -o /tmp/_iwq_hk -w '%{http_code}' -X POST "$AR/v1/pools" "${thw[@]}" \
  -H 'content-type: application/json' \
  -d '{"pool_id":"iwq_probe","channel_types":["webchat"],"sla_target_ms":60000,
       "hooks":{"on_human_end":[{"pool":"wrapup_detached_ia","side":"agent","dispatch":"detached"}]}}')
[ "$C" = "422" ] && ok "hook author-bound exige fila interna (422)" || bad "esperado 422, veio $C"

echo
echo "== I3 — o alvo do delegate =="
WPOOL="wrapup_detached_ia"
WSKILL="skill_wrapup_detached_v1"

# O bridge executa o SNAPSHOT DO SLOT `current` do pool, NÃO o `skill.flow`. Editar o
# YAML e reiniciar republica o skill, mas deixa o slot congelado no flow ANTIGO — e o
# wrap-up segue caindo no pool velho, funcionando na tela. Foi exatamente assim que
# esta fatia passou despercebida na primeira validação: sucesso plausível pelo caminho
# antigo. Por isso o assert é sobre o SNAPSHOT, não sobre o skill.
# Publicar o YAML é NECESSÁRIO: desde 2026-07-13 skills são **seed-if-absent**, não
# upsert — o RegistrySyncer NÃO reaplica o arquivo sobre um skill que já existe com
# `flow` (o editor é autoritativo; o boot antigo apagava rascunho da UI). Reiniciar o
# bridge não muda nada; ele só loga o DRIFT. Sem este passo, editar o YAML de um skill
# já semeado é um no-op silencioso do ponto de vista de quem editou.
echo "6a) Publica $WSKILL a partir do YAML (seed-if-absent não reaplica sozinho) ..."
$COMPOSE exec -T orchestrator-bridge python3 - "$WSKILL" "$TENANT" <<'PY' || true
import json, os, sys, urllib.request, yaml, glob
skill_id, tenant = sys.argv[1], sys.argv[2]
src = None
for d in (os.environ.get("SKILLS_DIR"), "/app/skills", "/skills"):
    if d and os.path.isdir(d):
        for f in glob.glob(os.path.join(d, "*.yaml")):
            r = yaml.safe_load(open(f).read())
            if isinstance(r, dict) and r.get("id") == skill_id:
                src = r; break
    if src: break
if not src:
    print("   YAML do skill não encontrado no container"); sys.exit(1)
flow = {"entry": src["entry"], "steps": src["steps"]}
if src.get("required_context"): flow["required_context"] = src["required_context"]
if src.get("mention_commands"): flow["mention_commands"] = src["mention_commands"]
payload = {
    "skill_id": skill_id, "name": src.get("name", skill_id),
    "version": src.get("version", "1.0"),
    "description": (src.get("description") or skill_id).strip(),
    "classification": src.get("classification", {"type": "orchestrator"}),
    "flow": flow,
}
for k in ("delegation_input", "config_params", "agent_role"):
    if src.get(k): payload[k] = src[k]
base = os.environ.get("AGENT_REGISTRY_URL", "http://agent-registry:3300")
req = urllib.request.Request(
    f"{base}/v1/skills/{skill_id}", method="PUT",
    data=json.dumps(payload).encode(),
    headers={"content-type": "application/json", "x-tenant-id": tenant,
             "x-skill-publish": "true",
             "x-service-token": os.environ.get("AGENT_REGISTRY_SERVICE_TOKEN", "")},
)
with urllib.request.urlopen(req, timeout=15) as r:
    print(f"   PUT /v1/skills/{skill_id} → HTTP {r.status}")
PY
curl -s "$AR/v1/skills/$WSKILL" "${trh[@]}" -o /tmp/_iwq_skill
if grep -q 'wrapup_pool' /tmp/_iwq_skill; then ok "skill.flow publicado com o alvo dinâmico"
else bad "skill.flow ainda no alvo antigo (veja /tmp/_iwq_skill)"; fi

echo "6b) Re-snapshot do slot current de $WPOOL (set-next → promote) ..."
C=$(curl -s -o /tmp/_iwq_sn -w '%{http_code}' -X PUT "$AR/v1/pools/$WPOOL/slots/next" "${thw[@]}" \
  -H 'content-type: application/json' \
  -d "{\"skill_id\":\"$WSKILL\",\"config_json\":{\"max_concurrent_sessions\":5}}")
echo "   set-next HTTP $C"
C2=$(curl -s -o /tmp/_iwq_pr -w '%{http_code}' -X POST "$AR/v1/pools/$WPOOL/promote" "${thw[@]}")
echo "   promote  HTTP $C2"
[ "$C" = "200" ] && [ "$C2" = "200" ] || bad "set-next/promote falhou (veja /tmp/_iwq_sn /tmp/_iwq_pr)"
# Marcador: tudo que interessa medir é o que nasce DEPOIS deste instante. Sem ele, a
# leitura ao vivo mistura segmentos do flow antigo com os do novo. Relógio do
# ClickHouse (não o do host) — é ele que carimba `started_at`.
$CH -q "SELECT toString(now())" > "$MARKER" 2>/dev/null || date -u +'%Y-%m-%d %H:%M:%S' > "$MARKER"
echo "   marcador: $(cat "$MARKER")"

echo "6c) O snapshot que o bridge EXECUTA aponta para o espelho?"
curl -s "$AR/v1/pools/$WPOOL/slots" "${trh[@]}" -o /tmp/_iwq_slots
if python3 - /tmp/_iwq_slots <<'PY'
import json,sys
d = json.load(open(sys.argv[1]))
slots = d.get("slots", d) if isinstance(d, dict) else d
cur = None
if isinstance(slots, dict): cur = slots.get("current")
elif isinstance(slots, list):
    cur = next((s for s in slots if s.get("slot") == "current"), None)
if not cur:
    print("   slot `current` ausente"); sys.exit(1)
blob = json.dumps(cur.get("yaml_snapshot") or cur)
if "formfill_demo" in blob:
    print("   snapshot AINDA aponta para formfill_demo (alvo antigo)"); sys.exit(1)
if "wrapup_pool" not in blob:
    print("   snapshot sem @ctx.hook.wrapup_pool — flow velho congelado"); sys.exit(1)
print("   snapshot delega em @ctx.hook.wrapup_pool")
PY
then ok "slot current re-snapshotado com o alvo dinâmico"
else bad "slot current desatualizado — o bridge roda o snapshot, não o skill.flow"; fi

echo
echo "── config: $pass ok / $fail falhas ──"
echo
echo "== I3 (ao vivo) — próximo passo, NESTA ordem =="
echo "  1. No Console, entre em $PARENT (o WS do espelho abre junto)."
echo "  2. Faça UM atendimento e encerre-o."
echo "  3. bash infra/test/smoke_internal_work_queue.sh verify"
echo
echo "  O verify olha SÓ o que nasceu após o promote. NÃO repita o script inteiro para"
echo "  conferir — ele re-promove, reseta o marcador, e a tabela volta a misturar"
echo "  segmentos do flow antigo com os do novo."
echo
echo
echo "I2/I4 (acesso derivado + UX) exigem REBUILD do platform-ui e do analytics-api."
echo "Checagem ao vivo, na ordem em que falham:"
echo "  · entrar em $PARENT no Console deve abrir DOIS WS (o do pai e o do espelho):"
echo "      docker compose -f docker-compose.demo.yml exec -T redis \\"
echo "        redis-cli SMEMBERS '${TENANT}:pool:${MIRROR}:instances'"
echo "    vazio = o toggle não abriu o WS do espelho → o item ficará invisível."
echo "  · a fila do espelho, com o item de wrap-up:"
echo "      docker compose -f docker-compose.demo.yml exec -T redis \\"
echo "        redis-cli ZRANGE '${TENANT}:pool:${MIRROR}:queue' 0 -1"
echo "  · a inbox deve rotular pela ORIGEM (\"Pós-atendimento — $PARENT\"), nunca '$MIRROR'."
echo
echo "I5 núcleo A+B ✅ — wrap-up não preenchido tem saída: prazo (acw_timeout_hours acima)"
echo "ou supervisor (POST /api/work_queue/expire/:sessionId). Regressão da I5:"
echo "  bash infra/test/smoke_acw_expire.sh"
echo "Pendente da I5: relatório de pendências por agente — desenho fechado (ADR § D7b)."
echo "  fonte = o ledger {t}:work_task:{session}, que cobre AS DUAS formas de pendência"
echo "  (o claim não o apaga). O item nunca reivindicado segue sem segmento humano, mas"
echo "  isso deixou de bloquear: quem responde 'quem está pendente agora' é o ledger."
[ "$fail" -eq 0 ]
