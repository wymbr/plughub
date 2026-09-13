#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# O acesso de CONSULTA DE STATUS é membro do processo? — passo 2b
#
# ── O defeito que este probe existe para reprovar ─────────────────────────────
# `skill_limite_entrada_v1.yaml` ramifica a pendência em `avaliar_politica_retomada`:
# `policy == "auto"` (acesso 3, o resultado) vai a `unificar_journey` → `journey_merge`;
# o **default — a consulta de status (`offer`, "em análise") — vai direto ao menu, sem
# merge**. Consequência: abertura é raiz, resposta entra por merge, e **toda consulta
# nasce raiz de si mesma e fica fora do processo**, quantas forem.
#
# A tabela de pertença do ADR manda o contrário (*acesso espontâneo → journey_merge*),
# e a F1 consta ✅ "provada por aresta ativa em journey_aliases" — a aresta existe, mas
# veio do ramo `auto`. **Um gate que só exercita o ramo que funciona não pode reprovar
# o ramo que não funciona.** Este probe exercita o outro ramo.
#
# ── Vermelho-primeiro ─────────────────────────────────────────────────────────
# Rodar ANTES da correção é parte do procedimento: se ele passar antes, ele não mede
# o que diz medir. Previsão para a execução PRÉ-fix: **passo 1 = ANTIGO** (merge no
# ramo da política) e **passo 6 = sem aresta** ⇒ exit 1.
#
# ── O que é sintético, e é declarado ──────────────────────────────────────────
# O cliente nasce IMPORTADO (CPF + celular `authoritative`), pela mesma função da rota
# `/identity/import` (`_import_customer_fixture.py`). Cada acesso passa pelo step-up de
# posse DENTRO da conversa: o cliente informa o celular, o fluxo desafia, e o código
# que o modo dev loga é lido pelo shell e entregue ao cliente WS (`answer_file`).
#
# ⚠️ Até a PID-10 (2026-09-13) o probe promovia o CPF a `possessed` por OTP antes do
# acesso e PULAVA o step-up. Isso deixou de existir por construção — CPF não recebe
# código (ADR D8), e a posse durável no CPF era exatamente o furo: dali em diante,
# digitar o CPF entregava as pendências sem prova. O step-up não é o que está sob
# teste, mas não há mais caminho que o evite.
# Se o código não aparecer no log (flag dev off), o probe sai INCONCLUSIVO, nunca verde.
# Todo o resto é caminho de produção: contato webchat real, agente de entrada real.
#
# NÃO julga: o step-up de OTP, a tela, nem o acesso 3 (que já funciona e entra aqui
# só como TESTEMUNHA de que a tabela de arestas e o leitor funcionam).
#
# Veredicto: 0 = é membro · 1 = DEFEITO (não é membro) · 2 = INCONCLUSIVO.
# Uso: bash infra/test/probe_journey_merge_status_access.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail
# Credencial (2026-08-27): o `pool_auth` deixou de devolver irrestrito na ausencia
# de header. O shim anexa o Bearer SO nas chamadas a analytics-api. Ver _auth.sh.
source "$(dirname "$0")/_auth.sh"; plughub_auth_curl_shim

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="${TENANT:-tenant_demo}"
CG="${CG:-http://localhost:8010}"
AR="${AR:-http://localhost:3300}"
CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'

redis() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null; }
chq()   { $COMPOSE exec -T clickhouse clickhouse-client -d plughub_demo --query "$1" < /dev/null 2>&1; }

PASS=0; FAIL=0
ok()  { echo "   ✅ $1"; PASS=$((PASS+1)); }
bad() { echo "   ❌ $1"; FAIL=$((FAIL+1)); }
die() { echo "   ⚠️  INCONCLUSIVO: $1"; exit 2; }

echo "══ 1) ONDE está o merge no snapshot QUE RODA (não no arquivo) ══"
# O bridge executa o SNAPSHOT do slot `current`. Editar o YAML é no-op num skill já
# semeado — então esta leitura é o que separa "a correção não funciona" de "a correção
# não está lá", que têm remédios diferentes e custam um ciclo quando confundidos.
SLOTS=$($CURL "$AR/v1/pools/limite_ia/slots" -H "x-tenant-id: $TENANT" 2>/dev/null)
WHERE=$(printf '%s' "$SLOTS" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    d = json.loads(raw)
except Exception:
    print("READER_FAIL"); raise SystemExit

# O `yaml_snapshot` chega como OBJETO JSON, nao como texto YAML — e o payload traz
# `previous` AO LADO de `current`. Ler os dois juntos responderia sobre um deploy que
# nao roda; por isso o slot e escolhido explicitamente, sem varredura.
slots = (d.get("slots") or {}) if isinstance(d, dict) else {}
cur   = slots.get("current") or {}
snap  = cur.get("yaml_snapshot") or cur.get("flow") or {}
steps = snap.get("steps") if isinstance(snap, dict) else None
if not isinstance(steps, list) or not steps:
    print("READER_FAIL"); raise SystemExit

def targets(step):
    """Todo destino declarado por um step, qualquer que seja a chave que o declara."""
    out = []
    for key in ("next", "default", "on_success", "on_failure", "on_timeout",
                "on_disconnect", "on_resume", "on_response"):
        v = step.get(key)
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict) and isinstance(v.get("next"), str):
            out.append(v["next"])
    for c in (step.get("conditions") or []):
        if isinstance(c, dict) and isinstance(c.get("next"), str):
            out.append(c["next"])
    return out

by_id = {s.get("id"): s for s in steps if isinstance(s, dict)}
if "unificar_journey" not in by_id:
    print("ABSENT"); raise SystemExit
pend = by_id.get("avaliar_pendencia") or {}
pol  = by_id.get("avaliar_politica_retomada") or {}
if "unificar_journey" in targets(pend):
    print("NOVO")          # pertenca antes da politica: vale para os dois ramos
elif "unificar_journey" in targets(pol):
    print("ANTIGO")        # merge so no ramo auto
else:
    print("OUTRO_LUGAR")
' 2>/dev/null)
echo "   → posição do merge: ${WHERE:-<vazio>}"
case "$WHERE" in
  NOVO)   ok "merge no ramo da PENDÊNCIA — vale para consulta e para resultado" ;;
  ANTIGO) bad "merge no ramo da POLÍTICA — só o acesso 3 é membro (estado pré-2b)" ;;
  ABSENT) die "o slot 'current' de limite_ia não tem o step unificar_journey — a F1 não
        está deployada aqui. Nada se conclui sobre o ramo." ;;
  OUTRO_LUGAR)
          bad "o step unificar_journey existe mas NENHUM dos dois choices aponta para
        ele — ou é inalcançável, ou o fluxo mudou de forma. Investigar antes de julgar." ;;
  *)      die "não consegui LER o snapshot (${WHERE:-vazio}). Falha do LEITOR — nada se
        conclui. Resposta: ${SLOTS:0:200}" ;;
esac

echo "══ 2) preflight do leitor de arestas — a tabela responde? ══"
ALIAS_TOTAL=$(chq "SELECT count() FROM journey_aliases AS j FINAL WHERE tenant_id='$TENANT'" | tr -d '\r' | head -1)
case "$ALIAS_TOTAL" in
  ''|*[!0-9]*) die "não consegui ler journey_aliases — resposta: ${ALIAS_TOTAL:0:200}" ;;
esac
# Testemunha: se NUNCA houve merge no tenant, a ausência do passo 6 não distingue
# "o ramo não faz merge" de "merge nenhum funciona neste ambiente".
[ "$ALIAS_TOTAL" -gt 0 ] \
  && ok "journey_aliases legível e povoada ($ALIAS_TOTAL arestas) — testemunha do ramo que FUNCIONA" \
  || die "journey_aliases está VAZIA no tenant. Sem uma aresta que comprove que o
        mecanismo funciona, a ausência de aresta no passo 6 é ambígua. Rode antes:
        bash infra/test/smoke_limite_tres_acessos.sh"

echo "══ 3) setup — cliente IMPORTADO (cpf + celular autoritativos) ══"
CPF="529$(date +%s | tail -c 9)"
FONE="+5511$(date +%s | tail -c 10)"
FIX=$($COMPOSE exec -T channel-gateway python - "__probe_journey_2b__" "j2b-$CPF" "$CPF" "$FONE" \
  < infra/test/_import_customer_fixture.py 2>/dev/null | tail -1)
CUST=$(echo "$FIX" | jq -r 'select(.outcome == "created") | .customer_id // empty' 2>/dev/null)
PHASH=$(echo "$FIX" | jq -r '.phone_hash // empty' 2>/dev/null)
[ -n "$CUST" ] && [ -n "$PHASH" ] || die "a importação da fixture não criou o cliente — ${FIX:0:200}"
echo "   ✓ CPF $CPF · celular $FONE · customer $CUST (importado)"

# O código do OTP só nasce no meio da conversa. Um vigia lê a linha do modo dev (a
# DESTE celular, pelo hash) e grava o código no arquivo que o cliente WS espera.
# ⚠️ `--since` com o INSTANTE do vigia, em nanossegundos — nunca uma janela relativa.
# A v1 usava `--since 1s`, e o acesso 2b chegou a começar 0,8 s depois do código do
# acesso 1: o vigia pegou a linha VELHA, entregou um código já consumido e o verify
# falhou — o probe acusou a pertença por um defeito do instrumento (medido na IDN-13).
otp_vigia() {  # $1 = arquivo dentro do container
  local desde; desde=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  $COMPOSE exec -T channel-gateway rm -f "$1" </dev/null >/dev/null 2>&1
  ( timeout 150 $COMPOSE logs -f --no-log-prefix --since "$desde" channel-gateway 2>&1 \
      | grep -m1 --line-buffered "OTP-DEV.*kind=phone value_hash=$PHASH" \
      | sed -n 's/.*code=\([0-9]*\).*/\1/p' \
      | { read -r code; [ -n "$code" ] && $COMPOSE exec -T channel-gateway sh -c "printf %s $code > $1" </dev/null; } ) &
}

echo "══ 4) o processo existe e está EM ANÁLISE (pendência policy=offer) ══"
# Pré-condição barulhenta: sem os DialogForms publicados o N3 falha antes de delegar,
# e a pendência nunca nasce — o probe acusaria o merge por um defeito de seed.
for F in dialog_limite_solicitacao dialog_limite_aprovacao; do
  $CURL -f "http://localhost:3760/v1/dialog/forms/$F?status=published" \
        -H "X-Tenant-ID: $TENANT" >/dev/null 2>&1 \
    || die "o form '$F' não está publicado. Semeie antes:
        bash infra/test/seed_dialog_limite_forms.sh"
done
TRIG=$($CURL -X POST "$CG/v1/channels/webhook/pool/limite_processo" $JSON -d "{
  \"tenant_id\": \"$TENANT\",
  \"context\": {
    \"session.cpf\":                \"$CPF\",
    \"session.customer_id\":        \"$CUST\",
    \"session.numero_cartao\":      \"4111111111111234\",
    \"session.vencimento_cartao\":  \"1230\",
    \"session.limite_solicitado\":  \"12000\"
  }}")
PROC_SID=$(echo "$TRIG" | jq -r '.session_id // empty')
[ -n "$PROC_SID" ] || die "trigger do processo não devolveu session_id — ${TRIG:0:200}"
PROC_ROOT=""; POL=""
for _ in $(seq 1 25); do
  PEND=$($CURL "$CG/v1/channels/webhook/pending/by-customer/$CUST?tenant_id=$TENANT")
  POL=$(echo "$PEND" | jq -r '.policy // empty')
  PROC_ROOT=$(echo "$PEND" | jq -r '.root_session_id // empty')
  [ "$POL" = "offer" ] && [ -n "$PROC_ROOT" ] && break
  sleep 1
done
[ "$POL" = "offer" ] \
  || die "a pendência não chegou a 'offer' em 25 s (veio '${POL:-<ausente>}') — sem
        processo em análise não existe acesso 2 a medir."
[ -n "$PROC_ROOT" ] || die "a pendência não carrega root_session_id — o merge não teria alvo."
echo "   ✓ processo $PROC_SID · raiz $PROC_ROOT · policy=offer"

echo "══ 5) ACESSO 2 — contato webchat real, consulta de status ══"
$COMPOSE cp infra/test/_ws_chat.py channel-gateway:/tmp/_ws_chat.py >/dev/null 2>&1 \
  || die "docker compose cp falhou — sem cliente WS não há acesso 2 a dirigir"
roteiro() {  # $1 = arquivo do código
  echo "[{\"match\":\"CPF\",\"answer\":\"$CPF\"},{\"match\":\"código de verificação\",\"answer\":\"sim\"},{\"match\":\"celular\",\"answer\":\"$FONE\"},{\"match\":\"Digite o código\",\"answer_file\":\"$1\",\"wait_s\":40},{\"match\":\"já tem um pedido\",\"answer\":\"consultar\"}]"
}
otp_vigia /tmp/otp_code_a2
WSOUT=$($COMPOSE exec -T channel-gateway python3 /tmp/_ws_chat.py \
  "$TENANT" "limite_ia" "cli_$CPF" "$(roteiro /tmp/otp_code_a2)" 150 2>&1)
echo "$WSOUT" | sed 's/^/      │ /'
A2_SID=$(echo "$WSOUT" | sed -n 's/^AUTHENTICATED session_id=//p' | head -1)
[ -n "$A2_SID" ] || die "o acesso 2 não autenticou — ${WSOUT:0:200}"
echo "$WSOUT" | grep -q '^ANSWER .*consultar' \
  || die "o fluxo NÃO chegou ao menu de continuidade (nenhuma resposta 'consultar').
        Isto é falha de PRÉ-CONDIÇÃO, não do merge: sem passar pelo ramo offer o
        probe não julga nada. Suspeito nº 1: o step-up de posse não fechou (veja
        ANSWER_FILE_TIMEOUT acima = o vigia não achou o código no log; recusa do
        desafio = o celular não é autoritativo para o cliente); nº 2:
        pending_workflow_get não achou a pendência; nº 3: o cliente não soube
        responder a superfície da pergunta (veja os PROMPT/NOTIFY acima)."
ok "acesso 2 percorreu o ramo offer (consulta de status), sessão $A2_SID"

echo "══ 6) O VEREDICTO — a consulta virou membro do processo? ══"
EDGE=""
for _ in $(seq 1 30); do
  EDGE=$(chq "SELECT concat(canonical_root,'|',actor,'|',toString(active))
              FROM journey_aliases AS j FINAL
              WHERE tenant_id='$TENANT' AND source_root='$A2_SID'
              FORMAT TSV" | tr -d '\r' | head -1)
  [ -n "$EDGE" ] && break
  sleep 1
done
IFS='|' read -r E_CANON E_ACTOR E_ACTIVE <<< "${EDGE:-||}"
echo "   → aresta: canonical='${E_CANON:-<nenhuma>}' actor='${E_ACTOR:-}' active='${E_ACTIVE:-}'"
if [ -z "$EDGE" ]; then
  bad "NENHUMA aresta com source_root=$A2_SID em 30 s — a consulta de status ficou
        FORA do processo. É o defeito do passo 2b."
elif [ "$E_CANON" != "$PROC_ROOT" ]; then
  bad "aresta existe mas aponta para '$E_CANON', e a raiz do processo é '$PROC_ROOT'
        — uniu à componente errada."
elif [ "$E_ACTIVE" != "1" ]; then
  bad "aresta presente e INATIVA (active=$E_ACTIVE) — merge revertido."
else
  ok "aresta ativa $A2_SID → $PROC_ROOT (actor=$E_ACTOR) — a consulta é membro"
fi

echo "══ 6b) SEGUNDA consulta — a pertença vale para N acessos, não para um ══"
# A decisão do dono não foi "o acesso 2 é membro", foi "são N acessos". Provar UM
# prova a forma e não o número — e o modo de falha que só a segunda consulta pega é
# real: pertença chaveada por cliente (em vez de por sessão), ou token de merge
# consumido no primeiro uso, passariam no passo 6 e falhariam aqui.
otp_vigia /tmp/otp_code_a2b
WSOUT2=$($COMPOSE exec -T channel-gateway python3 /tmp/_ws_chat.py \
  "$TENANT" "limite_ia" "cli_${CPF}_b" "$(roteiro /tmp/otp_code_a2b)" 150 2>&1)
A2B_SID=$(echo "$WSOUT2" | sed -n 's/^AUTHENTICATED session_id=//p' | head -1)
echo "$WSOUT2" | grep -q '^ANSWER .*consultar' \
  && echo "   → segunda consulta na sessão $A2B_SID" \
  || bad "a SEGUNDA consulta não chegou ao menu — ${WSOUT2:0:160}"
EDGE2=""
for _ in $(seq 1 30); do
  EDGE2=$(chq "SELECT canonical_root FROM journey_aliases AS j FINAL
               WHERE tenant_id='$TENANT' AND source_root='$A2B_SID' AND active=1
               FORMAT TSV" | tr -d '\r' | head -1)
  [ -n "$EDGE2" ] && break
  sleep 1
done
[ "$EDGE2" = "$PROC_ROOT" ] \
  && ok "a segunda consulta também é membro — pertença vale por ACESSO, não por cliente" \
  || bad "segunda consulta fora do processo (canonical='${EDGE2:-<nenhuma>}') — a
        pertença não é repetível, e N consultas não seriam N membros."

echo "══ 7) confirmação pelo LEITOR de produto (union-find no /reports/journeys) ══"
# A aresta é o fato; o relatório é o que a tela consome. Os dois podem divergir (o
# union-find roda no read layer), e é a segunda superfície que o F4 vai usar.
JR=$($CURL "http://localhost:3500/reports/journeys?root_session_id=$PROC_ROOT&tenant_id=$TENANT" 2>/dev/null)
JCOUNT=$(echo "$JR" | jq -r '(.data[0].session_count // .session_count // empty)' 2>/dev/null)
echo "   → session_count do processo: ${JCOUNT:-<não lido>}"
if [ -z "$JCOUNT" ]; then
  echo "      ⚠️  não consegui ler /reports/journeys — o passo 6 continua valendo (é o fato);"
  echo "          este passo é confirmação, e a ausência dele não vira verde nem vermelho."
elif [ "$JCOUNT" -ge 3 ]; then
  ok "o relatório conta $JCOUNT sessões sob o processo (processo + 2 consultas)"
elif [ "$JCOUNT" = "2" ]; then
  bad "o relatório conta 2 — uma das duas consultas chegou ao union-find e a outra
        não. Aresta presente sem efeito no read layer é pior que aresta ausente."
else
  bad "o relatório conta $JCOUNT — as arestas não chegaram ao union-find do read layer"
fi

echo
echo "═══════════════════════════════════════════════════════════════"
echo "  processo   : $PROC_SID  (raiz $PROC_ROOT)"
echo "  acesso 2   : ${A2_SID:-<nenhum>}"
echo "  CPF        : $CPF   ·   customer $CUST"
echo "  ✅ $PASS   ❌ $FAIL"
echo "═══════════════════════════════════════════════════════════════"
[ "$FAIL" -eq 0 ] || exit 1
exit 0
