#!/usr/bin/env bash
#
# probe_config_scope_provenance.sh — a Config API diz QUAL escopo está em vigor,
# e uma escrita global que não alcança ninguém não responde só `ok` (ALW-06/CNS-14).
#
# POR QUE ESTE PROBE EXISTE
# =========================
# A resolução de config é `LIMIT 1` com o tenant na frente: **o override de tenant
# vence o global POR INTEIRO**. A consequência foi medida três vezes em 2026-09-02,
# e nenhuma delas ficou vermelha em lugar nenhum:
#
#   1. um `PUT` de `masking.types` no escopo GLOBAL respondeu **200 e não teve efeito**
#      — `tenant_demo` tem override daquela chave. Só apareceu ao contar os tipos nos
#      dois escopos (14 × 13);
#   2. `masking.context_map` tem só a linha `__global__`, e a CNS-08 recusa com 422
#      qualquer `PUT` de tenant nessa chave — ou seja, o comportamento **difere por
#      chave** dentro do MESMO namespace, e nenhuma tela dizia isso;
#   3. o `seed_context_map.py` precisou de guarda PRÉVIA porque conferir só o efeito
#      pega o sintoma **depois** de já ter gravado.
#
# E o dano não é o `PUT` perdido, é a DERIVA. Medido em `tenant_demo`:
#   · `masking.types`         → override **byte-idêntico** ao global (mesmo md5).
#     Informação zero, e engole toda edição futura da plataforma naquela chave.
#   · `masking.context_rules` → já divergiu: um rótulo corrigido no global
#     (`*.resume_token`) nunca chegou ao tenant. Hoje custou um rótulo; da próxima
#     vez custa uma regra de máscara.
#
# O QUE ESTE PROBE PODE REPROVAR (a pergunta que todo verde tem de responder)
# ===========================================================================
#   A  `/_provenance` voltar a ser inalcançável                        → VERMELHO
#      (é o modo de falha que matou a `GET /{namespace}/raw`: literal
#       registrada DEPOIS da paramétrica devolve 404 "não há dado")
#   B  o veredicto discordar do Postgres lido direto                   → VERMELHO
#   C  `diverges` errar em qualquer das 3 situações                    → VERMELHO
#      (idêntico / diferente / só um lado existe)
#   D  `shadowed_by` mentir, nos DOIS sentidos                         → VERMELHO
#      · sombreado e não nomeia  → o silêncio volta
#      · não sombreado e nomeia  → alarme falso, que ensina a ignorar
#      · e o CONTROLE DE EFEITO: depois da escrita global sombreada, a leitura
#        efetiva do tenant tem de continuar no valor ANTIGO. Sem ele o relatório
#        seria decorativo — provaria a lista, não o fato que a lista descreve
#   E  qualquer rota literal voltar a ser sombreada por uma paramétrica → VERMELHO
#      (classe inteira, não a ocorrência conhecida — lista de exceção envelhece)
#   F  as chaves de i18n do banner sumirem ou dessincronizarem         → VERMELHO
#      (inclui chave REPETIDA no mesmo objeto: JSON aceita, o parser fica com a
#       última, e a paridade EN × pt-BR não detecta porque os dois quebram igual)
#
# INCONCLUSIVO é ramo próprio: sem config-api ou sem Postgres o probe NÃO declara
# verde. Verde por ausência de amostra é a família "teste que não pode reprovar".
#
# Uso:  bash infra/test/probe_config_scope_provenance.sh
set -uo pipefail

CFG="${CONFIG_API:-http://localhost:3600}"
TENANT="${TENANT:-tenant_demo}"
ADMIN="${CONFIG_ADMIN_TOKEN:-demo_config_admin_token}"
PG="${PG_CONTAINER:-plughub-demo-postgres-1}"
PGDB="${PGDB:-plughub_demo}"
NS="masking"
PKEY="__probe_scope_provenance"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0; INC=0
ok()   { echo "  ${GRN}✓${RST} $*"; }
bad()  { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
inc()  { echo "  ${YLW}?${RST} $*"; INC=$((INC+1)); }
info() { echo "    $*"; }

put() {  # put <tenant_id_json> <valor_json>
  curl -s -X PUT "$CFG/config/$NS/$PKEY" \
       -H "X-Admin-Token: $ADMIN" -H 'Content-Type: application/json' \
       -d "{\"tenant_id\":$1,\"value\":$2}"
}
prov() { curl -s "$CFG/config/$NS/_provenance?tenant_id=$TENANT"; }

limpar() {
  curl -s -o /dev/null -X DELETE "$CFG/config/$NS/$PKEY?tenant_id=$TENANT" -H "X-Admin-Token: $ADMIN"
  curl -s -o /dev/null -X DELETE "$CFG/config/$NS/$PKEY"                   -H "X-Admin-Token: $ADMIN"
}
# A limpeza roda mesmo se o probe morrer no meio: fixture deixada para trás vira
# override de verdade, e este probe existe justamente por causa de override órfão.
trap limpar EXIT

echo "${BLD}probe_config_scope_provenance — escopo em vigor e escrita sombreada${RST}"
echo "  config-api=$CFG  tenant=$TENANT"
echo

# ── pré-condições ────────────────────────────────────────────────────────────
if [ "$(curl -s -o /dev/null -w '%{http_code}' "$CFG/v1/health")" != "200" ]; then
  inc "config-api fora do ar — INCONCLUSIVO"; echo; echo "INCONCLUSIVO"; exit 2
fi
if ! docker exec "$PG" psql -U plughub -d "$PGDB" -c 'SELECT 1' >/dev/null 2>&1; then
  inc "Postgres inalcançável ($PG/$PGDB) — o oráculo independente do ramo B não existe"
fi

limpar   # começa limpo, sem herdar fixture de execução anterior

# ── A — /_provenance é alcançável ────────────────────────────────────────────
echo "${BLD}A) /_provenance responde, e não é a paramétrica disfarçada${RST}"
A_BODY="$(prov)"
A_TEM_KEYS="$(printf '%s' "$A_BODY" | python3 -c 'import json,sys
try:  d=json.load(sys.stdin)
except Exception: print("nao"); raise SystemExit
print("sim" if isinstance(d.get("keys"), dict) else "nao")' 2>/dev/null || echo nao)"
if [ "$A_TEM_KEYS" = "sim" ]; then
  ok "200 com objeto \`keys\`"
else
  bad "não devolveu \`keys\` — provavelmente casou \`/{namespace}/{key}\` com key=_provenance"
  info "$(printf '%s' "$A_BODY" | head -c 200)"
fi

# ── B — o veredicto bate com o Postgres lido direto ──────────────────────────
echo
echo "${BLD}B) veredicto × Postgres (oráculo independente)${RST}"
if docker exec "$PG" psql -U plughub -d "$PGDB" -c 'SELECT 1' >/dev/null 2>&1; then
  docker exec "$PG" psql -U plughub -d "$PGDB" -t -A -F'|' -c \
    "SELECT key, tenant_id FROM platform_config WHERE namespace='$NS' ORDER BY key, tenant_id" \
    > /tmp/_prov_pg.txt 2>/dev/null
  printf '%s' "$A_BODY" > /tmp/_prov_api.json
  B_OUT="$(python3 - "$TENANT" <<'PY'
import json, sys
tenant = sys.argv[1]
api = json.load(open("/tmp/_prov_api.json")).get("keys", {})
pg = {}
for linha in open("/tmp/_prov_pg.txt"):
    linha = linha.strip()
    if not linha or "|" not in linha:
        continue
    k, t = linha.rsplit("|", 1)
    pg.setdefault(k, set()).add(t)
erros = []
for k, escopos in sorted(pg.items()):
    tem_g = "__global__" in escopos
    tem_t = tenant in escopos
    esperado = "tenant" if tem_t else "global"
    v = api.get(k)
    if v is None:
        erros.append("%s: ausente na API" % k); continue
    if v["effective_scope"] != esperado:
        erros.append("%s: API diz %s, PG diz %s" % (k, v["effective_scope"], esperado))
    if v["global_present"] != tem_g or v["tenant_present"] != tem_t:
        erros.append("%s: presenca API g=%s/t=%s, PG g=%s/t=%s"
                     % (k, v["global_present"], v["tenant_present"], tem_g, tem_t))
sobrando = sorted(set(api) - set(pg))
if sobrando:
    erros.append("API relata keys que o PG nao tem: %s" % sobrando)
print("OK %d keys conferidas" % len(pg) if not erros else "ERRO " + " ; ".join(erros))
PY
)"
  case "$B_OUT" in
    OK*)  ok "${B_OUT#OK }" ;;
    *)    bad "${B_OUT#ERRO }" ;;
  esac
else
  inc "sem Postgres — ramo B não executado (não conta como verde)"
fi

# ── C — `diverges` nas três situações ────────────────────────────────────────
echo
echo "${BLD}C) diverges: idêntico → false · diferente → true · só um lado → null${RST}"
ler_c() {  # ler_c <campo>
  prov | python3 -c "import json,sys; d=json.load(sys.stdin)['keys'].get('$PKEY'); print('AUSENTE' if d is None else json.dumps(d['$1']))"
}
put 'null'          '{"n":1}' >/dev/null
put "\"$TENANT\""   '{"n":1}' >/dev/null
C1="$(ler_c diverges)"
[ "$C1" = "false" ] && ok "conteúdo idêntico nos dois escopos → diverges=false" \
                    || bad "conteúdo idêntico → esperado false, veio $C1"

put "\"$TENANT\""   '{"n":2}' >/dev/null
C2="$(ler_c diverges)"
[ "$C2" = "true" ]  && ok "conteúdo diferente → diverges=true" \
                    || bad "conteúdo diferente → esperado true, veio $C2"

curl -s -o /dev/null -X DELETE "$CFG/config/$NS/$PKEY?tenant_id=$TENANT" -H "X-Admin-Token: $ADMIN"
C3="$(ler_c diverges)"; C3E="$(ler_c effective_scope)"
if [ "$C3" = "null" ] && [ "$C3E" = '"global"' ]; then
  ok "só o global existe → diverges=null e effective_scope=global"
else
  bad "só o global → esperado null/global, veio $C3/$C3E"
fi

# ── D — shadowed_by, nos dois sentidos, com controle de EFEITO ───────────────
echo
echo "${BLD}D) shadowed_by na escrita GLOBAL${RST}"
D_NEG="$(put 'null' '{"n":10}' | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin).get("shadowed_by")))')"
[ "$D_NEG" = "[]" ] && ok "sem override → shadowed_by=[] (negativo: nada de alarme falso)" \
                    || bad "sem override → esperado [], veio $D_NEG"

put "\"$TENANT\"" '{"n":20}' >/dev/null
D_POS="$(put 'null' '{"n":30}' | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin).get("shadowed_by")))')"
if printf '%s' "$D_POS" | grep -q "\"$TENANT\""; then
  ok "com override → shadowed_by nomeia o tenant ($D_POS)"
else
  bad "com override → esperado nomear $TENANT, veio $D_POS"
fi

# CONTROLE DE EFEITO — a lista descreve um fato, e o fato é este:
D_EFEITO="$(curl -s "$CFG/config/$NS/$PKEY?tenant_id=$TENANT" \
            | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["value"]))')"
if [ "$D_EFEITO" = '{"n": 20}' ] || [ "$D_EFEITO" = '{"n":20}' ]; then
  ok "leitura efetiva do tenant continua em {n:20} — a escrita global NÃO alcançou"
else
  bad "controle de efeito falhou: esperado {n:20}, veio $D_EFEITO"
  info "se veio {n:30}, o override deixou de vencer e o relatório perdeu o sentido"
fi

limpar

# ── E — nenhuma rota literal sombreada por paramétrica ───────────────────────
echo
echo "${BLD}E) ordem de registro: nenhuma rota literal inalcançável${RST}"
E_OUT="$(python3 "$RAIZ/infra/test/_route_shadow_check.py" "$CFG/openapi.json" 2>&1)"; E_RC=$?
if [ "$E_RC" -eq 0 ]; then
  ok "$(printf '%s' "$E_OUT" | tail -1)"
elif [ "$E_RC" -eq 1 ]; then
  bad "rota literal inalcançável — foi assim que a \`/{namespace}/raw\` morreu"
  printf '%s\n' "$E_OUT" | sed 's/^/    /'
else
  inc "verificador de ordem não pôde rodar: $E_OUT"
fi

# ── F — i18n do banner: presente, pareado e sem chave repetida ───────────────
echo
echo "${BLD}F) i18n do banner (en × pt-BR)${RST}"
F_OUT="$(cd "$RAIZ" && python3 - <<'PY'
import collections, io, json, sys
BASE = "packages/platform-ui/src/i18n/locales/%s/masking.json"
ESPERADAS = {"title", "writesToTenant", "noneShadowed", "shadowedIntro",
             "diverges", "identical", "unavailable"}
def hook(caminho):
    def h(pares):
        c = collections.Counter(k for k, _ in pares)
        rep = sorted(k for k, n in c.items() if n > 1)
        if rep:
            raise ValueError("chave repetida em %s: %s" % (caminho, rep))
        return dict(pares)
    return h
erros, blocos = [], {}
for loc in ("en", "pt-BR"):
    caminho = BASE % loc
    try:
        doc = json.loads(io.open(caminho, encoding="utf-8").read(), object_pairs_hook=hook(caminho))
    except ValueError as e:
        erros.append(str(e)); continue
    b = doc.get("provenance")
    if not isinstance(b, dict):
        erros.append("%s: bloco `provenance` ausente" % caminho); continue
    blocos[loc] = set(b)
    falta = sorted(ESPERADAS - blocos[loc])
    if falta:
        erros.append("%s: faltam %s" % (caminho, falta))
    vazias = sorted(k for k, v in b.items() if not str(v).strip())
    if vazias:
        erros.append("%s: valores vazios %s" % (caminho, vazias))
if len(blocos) == 2 and blocos["en"] != blocos["pt-BR"]:
    erros.append("paridade: so en=%s so pt=%s"
                 % (sorted(blocos["en"] - blocos["pt-BR"]), sorted(blocos["pt-BR"] - blocos["en"])))
print("OK %d chaves nos dois locales" % len(ESPERADAS) if not erros else "ERRO " + " ; ".join(erros))
PY
)"
case "$F_OUT" in
  OK*) ok "${F_OUT#OK }" ;;
  *)   bad "${F_OUT#ERRO }" ;;
esac

# ── veredicto ────────────────────────────────────────────────────────────────
echo
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}${BLD}REPROVADO${RST} — $FAIL falha(s), $INC inconclusivo(s)"; exit 1
fi
if [ "$INC" -gt 0 ]; then
  echo "${YLW}${BLD}INCONCLUSIVO${RST} — $INC ramo(s) sem amostra; nenhuma falha"; exit 2
fi
echo "${GRN}${BLD}VERDE${RST} — escopo em vigor é declarado, e escrita global sombreada é nomeada"
