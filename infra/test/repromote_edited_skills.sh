#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# repromote_edited_skills.sh — leva a edição do YAML até o que o bridge EXECUTA
#
# POR QUE EXISTE. São três operações distintas, e confundi-las custou ciclos em
# duas sessões seguidas:
#
#   seed-if-absent (restart)   cria entidade INÉDITA
#   REGISTRY_SYNC_RECONCILE    republica o `skill.flow` de skill JÁ EXISTENTE
#   set-next + promote         re-snapshota o SLOT — e é o snapshot que roda
#
# O modo de falha é cruel porque **nada fica vermelho**: `GET /v1/skills/:id`
# mostra o texto novo, o smoke passa, e o runtime segue servindo o antigo. Foi
# exatamente o risco das edições de markdown de 2026-08-12 — o smoke afirma sobre
# valores do `delegate.context`, nunca sobre o TEXTO das mensagens ao cliente.
#
# ⚠️ O QUE ESTE SCRIPT NÃO FAZ: adivinhar `config_json`. Ele LÊ o do slot `current`
# e o reenvia. Hardcodar o config (mesmo "só o max_concurrent_sessions") promove um
# deploy com configuração que ninguém pediu — em silêncio, que é o pior jeito.
#
# Verificação: compara o CONTEÚDO do snapshot com uma string esperada, não a
# existência do slot. "Foi escrito" ≠ "mudou".
#
# Uso:
#   bash infra/test/repromote_edited_skills.sh                 # os 3 pools editados
#   POOLS="limite_ia:sem_asterisco" bash …                     # um par pool:marcador
#   DRY=1 bash …                                               # só diagnostica
#
# Veredicto: 0 = snapshot em dia · 1 = promoveu e conferiu (ou falhou) · 2 = inconclusivo.
# ═══════════════════════════════════════════════════════════════════════════════
# ⚠️ UTF-8 explicito na SAIDA do python. No Windows o `stdout` decodifica com cp1252 e
# um `print` de texto acentuado estoura `UnicodeEncodeError`, derrubando o probe por
# motivo de bancada — ou, pior, mutila o texto que o shell vai comparar.
#
# ⚠️ E o que esta linha NAO conserta, porque o diagnostico foi REFEITO em 2026-09-02:
# a corrupcao que motivou a CNS-12 nao vinha do `sys.stdin` (medido: `curl | python3 ->
# arquivo` preserva `Almoco`/`Reuniao` intactos). Vinha da VARIAVEL DE SHELL — passar
# JSON nao-ASCII por `VAR=$(…)` o mutila, medido 321 bytes contra 325. Contra isso a
# unica defesa e nao passar por variavel: producao e consumo por ARQUIVO.
export PYTHONIOENCODING=utf-8

set -uo pipefail

AR="${AR:-http://localhost:3300}"
TENANT="${TENANT:-tenant_demo}"
TOKEN="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"
DRY="${DRY:-0}"
CURL="curl -s --max-time 20"
H=(-H "x-tenant-id: $TENANT" -H "x-service-token: $TOKEN" -H "content-type: application/json")

# pool → uma STRING que só existe na versão NOVA do flow. É a testemunha de que a
# edição chegou: procurar pelo nome do step provaria só que o slot existe.
#   limite_ia        — o prompt do menu de continuidade perdeu os `**`
#   limite_retorno   — as notificações de aprovado/recusado perderam os `**`
#   portabilidade_ia — o menu de continuidade perdeu os `**` em volta do número
declare -A EXPECT=(
  [limite_ia]="Limite solicitado: R\$ {{"
  [limite_retorno]="foi aprovado."
  [portabilidade_ia]="portabilidade do número {{"
)
# Marcador do defeito ANTIGO: se ainda aparecer, o snapshot é o velho. Duas
# testemunhas (presença do novo + ausência do velho) porque uma só confunde
# "não mudou" com "leitor quebrado".
declare -A FORBID=(
  [limite_ia]='Limite solicitado: **R$'
  [limite_retorno]='foi **aprovado**'
  [portabilidade_ia]='do número **{{'
)

PASS=0; FAIL=0; DID=0
ok()   { echo "   ✅ $1"; PASS=$((PASS+1)); }
bad()  { echo "   ❌ $1"; FAIL=$((FAIL+1)); }
die()  { echo "⚠️  INCONCLUSIVO: $1" >&2; exit 2; }

# ── Preflight do leitor ───────────────────────────────────────────────────────
# sac_ia é a testemunha: existe desde sempre. Sem isto, um 401 devolveria "slot
# ausente" para TODOS os pools e o veredicto culparia o promote.
WITNESS=$($CURL "$AR/v1/pools/sac_ia/slots" -H "x-tenant-id: $TENANT")
echo "$WITNESS" | grep -q 'current\|slots' || die "não consegui LER $AR/v1/pools/sac_ia/slots — ${WITNESS:0:160}"

snapshot_of() {  # $1 = pool → imprime o yaml_snapshot do slot current
  $CURL "$AR/v1/pools/$1/slots" -H "x-tenant-id: $TENANT" | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: raise SystemExit
s=d.get("slots",d) if isinstance(d,dict) else d
cur = s.get("current") if isinstance(s,dict) else next((x for x in s if x.get("slot")=="current"), None)
if not cur: raise SystemExit
print(cur.get("yaml_snapshot") or json.dumps(cur.get("flow") or "", ensure_ascii=False))
' 2>/dev/null
}
slot_meta() {  # $1 = pool → "skill_id<TAB>config_json"
  $CURL "$AR/v1/pools/$1/slots" -H "x-tenant-id: $TENANT" | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: raise SystemExit
s=d.get("slots",d) if isinstance(d,dict) else d
cur = s.get("current") if isinstance(s,dict) else next((x for x in s if x.get("slot")=="current"), None)
if not cur: raise SystemExit
print((cur.get("skill_id") or "") + "\t" + json.dumps(cur.get("config_json") or {}, ensure_ascii=False))
' 2>/dev/null
}

TARGETS="${POOLS:-limite_ia limite_retorno portabilidade_ia}"
echo "══ repromote — tenant=$TENANT · pools: $TARGETS ══"
echo

for entry in $TARGETS; do
  P="${entry%%:*}"
  echo "── $P"
  SNAP=$(snapshot_of "$P")
  if [[ -z "$SNAP" ]]; then
    bad "$P: sem slot 'current' (ou snapshot vazio) — este script não cria slot. Veja o 1c do smoke."
    continue
  fi

  WANT="${EXPECT[$P]:-}"; NOPE="${FORBID[$P]:-}"
  HAS_NEW=0; HAS_OLD=0
  [[ -n "$WANT" ]] && grep -qF -- "$WANT" <<<"$SNAP" && HAS_NEW=1
  [[ -n "$NOPE" ]] && grep -qF -- "$NOPE" <<<"$SNAP" && HAS_OLD=1

  if [[ "$HAS_NEW" == "1" && "$HAS_OLD" == "0" ]]; then
    ok "$P: snapshot JÁ tem a edição — nada a fazer"
    continue
  fi
  echo "   ⚠️  snapshot DESATUALIZADO (novo=$HAS_NEW velho=$HAS_OLD) — o bridge roda o texto antigo"
  if [[ "$DRY" == "1" ]]; then echo "   (DRY=1 — não promovendo)"; FAIL=$((FAIL+1)); continue; fi

  IFS=$'\t' read -r SKILL CFG <<<"$(slot_meta "$P")"
  [[ -n "$SKILL" ]] || { bad "$P: não li o skill_id do slot"; continue; }
  echo "   → set-next $SKILL config=$CFG"
  SN=$($CURL -o /tmp/_rp -w '%{http_code}' -X PUT "$AR/v1/pools/$P/slots/next" "${H[@]}" \
       -d "{\"skill_id\":\"$SKILL\",\"config_json\":$CFG}")
  [[ "$SN" == "200" ]] || { bad "$P: set-next HTTP $SN — $(head -c 200 /tmp/_rp)"; continue; }
  PM=$($CURL -o /tmp/_rp -w '%{http_code}' -X POST "$AR/v1/pools/$P/promote" "${H[@]}" -d '{}')
  [[ "$PM" == "200" ]] || { bad "$P: promote HTTP $PM — $(head -c 200 /tmp/_rp)"; continue; }
  DID=$((DID+1))

  sleep 2
  SNAP=$(snapshot_of "$P")
  HAS_NEW=0; HAS_OLD=0
  [[ -n "$WANT" ]] && grep -qF -- "$WANT" <<<"$SNAP" && HAS_NEW=1
  [[ -n "$NOPE" ]] && grep -qF -- "$NOPE" <<<"$SNAP" && HAS_OLD=1
  if [[ "$HAS_NEW" == "1" && "$HAS_OLD" == "0" ]]; then
    ok "$P: promovido e CONFERIDO no snapshot"
  else
    bad "$P: promovido e o snapshot SEGUE antigo (novo=$HAS_NEW velho=$HAS_OLD).
        O promote copia o \`skill.flow\` do registry — se ele não foi republicado, promover
        re-snapshota o texto VELHO. Rode antes:
          REGISTRY_SYNC_RECONCILE=true docker compose -f docker-compose.demo.yml up -d orchestrator-bridge"
  fi
done

echo
echo "══ $PASS ok · $FAIL falha(s) · $DID promoção(ões) ══"
if [[ $FAIL -gt 0 ]]; then
  echo "❌ ao menos um pool executa texto desatualizado."
  exit 1
fi
[[ $DID -gt 0 ]] \
  && echo "✅ snapshots em dia (promovi $DID). O bridge invalida o cache no registry.changed do promote." \
  || echo "✅ snapshots já estavam em dia — nenhuma promoção necessária."
exit 0
