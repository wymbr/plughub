#!/usr/bin/env bash
# probe_llm_credential_health.sh — o /v1/health do ai-gateway DIZ o estado da credencial?
#
# ── O defeito que este gate existe para não deixar voltar ────────────────────
# Até 2026-08-23 o health respondia `anthropic: "ok"` quando a STRING da chave era
# não-vazia. Nada jamais contatava o provedor. Resultado medido em 08-22:
#
#     POST /v1/reason ..... 124      upstream_model_error ... 124
#     status_401 .......... 124      e o health, 200 verde o tempo todo
#
# E como todo step `reason` cai no `on_failure` — ramo LEGÍTIMO de fluxo —, nada
# mais ficava vermelho em lugar nenhum. Um valor plausível segurando o diagnóstico.
#
# ── O QUE ESTE PROBE JULGA, E O QUE NÃO JULGA ────────────────────────────────
# Julga: que o health reporta um estado MEDIDO por conta, com contador-testemunha
#        ao lado do contador de erro, e que o código HTTP acompanha o veredicto.
# NÃO julga: o ramo `invalid` quando o ambiente tem credencial boa, nem o ramo `ok`
#        quando o ambiente tem credencial ruim — um ambiente só exibe UM dos dois.
#        A outra metade é coberta por teste unitário com Redis mockado
#        (`tests/test_account_selector.py::TestCredentialSummary`, 7 casos), e este
#        probe IMPRIME qual metade deixou de exercer. Gate que não diz isso compra
#        confiança que não tem.
#
# ── PREVISÕES (escritas ANTES de rodar) ──────────────────────────────────────
#   P1  o corpo do health tem os campos novos (`accounts`, `counters`, `notes`).
#       Ausentes ⇒ imagem ANTIGA rodando: `build` verde não recria container.
#   P2  `accounts[].credentials` ∈ {ok, invalid, error, unknown} — e o veredicto
#       ramifica em TRÊS desfechos diferentes, nenhum deles "verde por omissão".
#   P3  `counters.calls_ok` existe ao lado de `counters.errors`. Sem a testemunha,
#       `errors: {}` seria indistinguível de "ninguém chamou".
#   P4  o HTTP acompanha: credencial recusada ⇒ 503, e não 200.
#
# Veredicto de TRÊS estados: 0 = health honesto · 1 = defeito · 3 = INCONCLUSIVO
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

AIGW="${AIGW:-http://localhost:3200}"
COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"

J() { python3 -c "import json,sys;d=json.load(sys.stdin)
$1" 2>/dev/null; }

echo "══ /v1/health do ai-gateway — o estado da credencial é MEDIDO? ══"
echo

# Preflight do INSTRUMENTO, antes de julgar o objeto: sem python3 no host toda
# leitura de campo volta VAZIA, e vazio seria diagnosticado como "corpo antigo" —
# o probe acusaria a coisa errada com ar de medição.
if ! command -v python3 >/dev/null 2>&1; then
  echo "   ⛔ INCONCLUSIVO — python3 ausente no host; este probe não lê o corpo."
  exit 3
fi

BODY_FILE=/tmp/_aigw_health.json
CODE="$(curl -s -o "$BODY_FILE" -w '%{http_code}' --max-time 15 "$AIGW/v1/health")"

if [ -z "$CODE" ] || [ "$CODE" = "000" ]; then
  echo "   ⛔ INCONCLUSIVO — ai-gateway inalcançável em $AIGW"
  echo "      (porta 3200; conferir '$COMPOSE ps ai-gateway')"
  exit 3
fi

echo "── corpo devolvido (HTTP $CODE) ─────────────────────────────────────────"
python3 -m json.tool < "$BODY_FILE" 2>/dev/null | sed 's/^/   /' || {
  echo "   ⛔ INCONCLUSIVO — corpo não é JSON:"; head -c 400 "$BODY_FILE" | sed 's/^/   /'; exit 3
}
echo

# ── P1 · a imagem em execução é a nova? ──────────────────────────────────────
echo "── P1 · campos novos presentes? ─────────────────────────────────────────"
HAS_NEW="$(J 'print("yes" if all(k in d for k in ("accounts","counters","notes")) else "no")' < "$BODY_FILE")"
echo "      accounts+counters+notes : ${HAS_NEW:-?}"
if [ "$HAS_NEW" != "yes" ]; then
  echo
  echo "   ⛔ INCONCLUSIVO — corpo ANTIGO. Isto não é veredicto sobre o desenho,"
  echo "      é sinal de que o container roda a imagem anterior. Nenhum serviço"
  echo "      monta o fonte, e um build verde NAO recria container:"
  echo "         $COMPOSE build ai-gateway && $COMPOSE up -d ai-gateway"
  exit 3
fi

# ── P2 · estado por conta ────────────────────────────────────────────────────
echo
echo "── P2 · estado da credencial, por conta ─────────────────────────────────"
N_ACC="$(J 'print(len(d["accounts"]))' < "$BODY_FILE")"
echo "      contas configuradas : $N_ACC"
J 'a=d["accounts"]
for x in a: print("      %-10s key_id=%s cred=%-8s throttled=%-5s last_ok_age=%s last_err=%s" % (x["provider"],x["key_id"],x["credentials"],x.get("throttled"),x["last_ok_age_s"],x["last_error_code"]))' < "$BODY_FILE"

STATES="$(J 'print(",".join(sorted({x["credentials"] for x in d["accounts"]})))' < "$BODY_FILE")"
STATUS="$(J 'print(d["status"])' < "$BODY_FILE")"
ANTH="$(J 'print(d["anthropic"])' < "$BODY_FILE")"
echo "      estados presentes   : ${STATES:-<nenhum>}"
echo "      status / anthropic  : $STATUS / $ANTH"

# ── P3 · contador de ausência COM testemunha de presença ─────────────────────
echo
echo "── P3 · contadores (o de erro anda acompanhado?) ────────────────────────"
J 'c=d["counters"]
print("      janela        : %ss" % c.get("window_seconds"))
print("      calls_ok      : %s   (TESTEMUNHA — sem ela, errors:{} não julga)" % c.get("calls_ok"))
print("      errors        : %s" % (c.get("errors") if c.get("errors") else "{}"))' < "$BODY_FILE"

HAS_WITNESS="$(J 'print("yes" if "calls_ok" in d["counters"] else "no")' < "$BODY_FILE")"
if [ "$HAS_WITNESS" != "yes" ]; then
  echo
  echo "   ❌ DEFEITO — contador de erro sem contador-testemunha de sucesso."
  exit 1
fi

echo
J 'n=d["notes"]
print("      notas do próprio health:") if n else None
[print("        · %s" % x) for x in n]' < "$BODY_FILE"

# ── P4 · o código HTTP acompanha o veredicto ─────────────────────────────────
echo
echo "── P4 · veredicto ───────────────────────────────────────────────────────"

case "$STATUS" in
  ok)
    if [ "$CODE" != "200" ]; then
      echo "   ❌ DEFEITO — status=ok mas HTTP $CODE."; exit 1
    fi
    echo "   ✅ HEALTH HONESTO — há conta com credencial VERIFICADA (não apenas"
    echo "      configurada). O estado veio de uma chamada real ao provedor."
    echo
    echo "   ⚠️  METADE NÃO EXERCIDA: com credencial boa este ambiente não pode"
    echo "      produzir o ramo 'invalid'/503. Essa metade é julgada por"
    echo "      tests/test_account_selector.py::TestCredentialSummary."
    exit 0
    ;;
  unhealthy)
    if [ "$CODE" != "503" ]; then
      echo "   ❌ DEFEITO — status=unhealthy mas HTTP $CODE (era 503)."
      echo "      É o defeito original de volta: o 'docker ps' só lê o código."
      exit 1
    fi
    case "$STATES" in
      *invalid*)
        echo "   ✅ HEALTH HONESTO — credencial RECUSADA pelo provedor, e o health"
        echo "      diz isso: 503, conta marcada 'invalid', código do erro nomeado."
        echo "      Antes de 08-23 este mesmo ambiente respondia 200 'ok'."
        echo
        echo "   ⚠️  METADE NÃO EXERCIDA: com credencial ruim este ambiente não pode"
        echo "      produzir o ramo 'ok'. Julgado no teste unitário."
        echo
        echo "   ⛔ E o AMBIENTE segue bloqueado para qualquer medição de IA:"
        echo "      todo step 'reason' de todo skill cai no 'on_failure'."
        exit 0
        ;;
      *)
        echo "   ❌ status=unhealthy sem conta 'invalid' — provável redis fora."
        echo "      redis: $(J 'print(d["redis"])' < "$BODY_FILE")"
        exit 1
        ;;
    esac
    ;;
  unknown)
    echo "   ⛔ INCONCLUSIVO — e o health DECLARA que não julga (é o desenho)."
    echo "      Nenhum desfecho de provedor registrado. Causas, nesta ordem:"
    echo "        1. sonda de boot desligada → '$COMPOSE exec -T ai-gateway printenv PLUGHUB_LLM_BOOT_PROBE'"
    echo "           (ler do CONTAINER, não do compose nem do config-api)"
    echo "        2. container subiu antes desta versão → up -d ai-gateway"
    exit 3
    ;;
  degraded)
    # DOIS casos caem em `degraded`, e são desfechos diferentes: distinguir pelo
    # discriminador de cada forma (`anthropic`), nunca pelo status que ambas têm.
    if [ "$ANTH" = "not_configured" ]; then
      echo "   ⛔ INCONCLUSIVO — nenhuma conta de LLM configurada."
      echo "      Escolha declarada: rodar a demo sem LLM não reprova o healthcheck."
      echo "      Mas com zero contas não há credencial a julgar."
      exit 3
    fi
    if [ "$CODE" != "200" ]; then
      echo "   ❌ DEFEITO — status=degraded mas HTTP $CODE (era 200)."
      echo "      Falha transitória não deve reprovar o container."
      exit 1
    fi
    echo "   ✅ HEALTH HONESTO — última chamada falhou por causa TRANSITÓRIA, e o"
    echo "      health distingue isso de credencial recusada: degraded/200, não 503."
    echo "      A credencial em si não foi reprovada; ver last_error_code acima."
    echo
    echo "   ⚠️  METADE NÃO EXERCIDA: este ambiente não produziu nem ok nem invalid"
    echo "      nesta janela. Ambos são julgados no teste unitário."
    exit 0
    ;;
  *)
    echo "   ⛔ INCONCLUSIVO — status inesperado: '$STATUS'"
    exit 3
    ;;
esac
