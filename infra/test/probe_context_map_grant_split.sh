#!/usr/bin/env bash
# probe_context_map_grant_split.sh — ALW-03: o MAPA tem grant proprio, e ele ALCANCA o autor.
#
# ── A decisao que este gate torna mecanismo ──────────────────────────────────────
#
# "Cadastrar um campo do ContextStore" sao DOIS fatos com donos diferentes:
#
#   · o CATALOGO de tipos (o que `cpf_br` mascara, sua classe LGPD)  → compliance
#   · o MAPA (quais campos existem, e qual tipo cada um usa)          → quem AUTORA flow
#
# Ate 2026-09-02 os dois viviam no namespace `masking` e portanto no MESMO grant
# (`config.masking`, preset ADMIN-ONLY), enquanto o autor de flow e `developer`. Medido
# naquele dia: `skill_flows.operacao` e `.editar` nascem para admin+developer;
# `config.masking` so para admin. O ADR nomeia essa friccao como o que faz gente
# CONTORNAR o cadastro — e cadastro contornado e a §1.1 de volta (o valor visivel porque
# ninguem decidiu).
#
# ── Por que estes ramos, e nao outros ────────────────────────────────────────────
#
#   A. o campo EXISTE no catalogo de modulos
#   B. e o `developer` esta no preset — sem isso a divisao e decorativa, porque o autor
#      continua sem alcancar o cadastro. E ESTA a metade que fecha a decisao.
#   C. o portao do config-api resolve `masking.context_map` para `context_map`…
#   D. …e `masking.types` CONTINUA em `masking`. Testemunha obrigatoria: um portao que
#      devolvesse `context_map` para tudo passaria em C sozinho e teria ENTREGADO o
#      catalogo ao autor, que e o oposto da decisao.
#   E. existe PORTA — item de menu gateado pelo campo novo. Grant sem porta nao concede
#      nada, e o repositorio ja pagou por isso (a `AgentReportsPage` orfa, os 11 grants
#      do supervisor que o `roles:` do menu tornava inertes).
#   F. a tela oferece SO o catalogo. O autor escolhe o tipo; nao cria tipo.
#   G. ao vivo: um principal com `config.context_map` e SEM `config.masking` escreve o
#      mapa e e RECUSADO no catalogo. E o unico ramo que prova que a divisao vale no
#      servidor, e nao so na declaracao.
set -u
cd "$(dirname "$0")/../.." || exit 2

FAIL=0
ok()  { echo "  v $1"; }
bad() { echo "  x $1"; FAIL=1; }
huh() { echo "  ? $1"; [ "$FAIL" = "0" ] && FAIL=2; }

MODULES="infra/modules.yaml"
ROUTER="packages/config-api/src/plughub_config_api/router.py"
NAV="packages/platform-ui/src/shell/Sidebar.tsx"
PAGE="packages/platform-ui/src/modules/context-map/ContextMapPage.tsx"

echo "=== probe_context_map_grant_split — ALW-03 (o cadastro alcanca o autor) ==="
echo

# ── A/B — o campo e o PRESET ─────────────────────────────────────────────────
if [ ! -f "$MODULES" ]; then
  huh "A: $MODULES ausente"; echo; echo "INCONCLUSIVO"; exit 2
fi
PRESET="$(python3 - <<'PY' 2>/dev/null
import io, re
s = io.open("infra/modules.yaml", encoding="utf-8").read().split("\n")
i = next((n for n, l in enumerate(s) if re.match(r"\s*context_map:\s*$", l)), None)
if i is None:
    print("AUSENTE"); raise SystemExit(0)
bloco = "\n".join(s[i:i + 14])
papeis = re.findall(r"^\s+(\w+):\s*(read_write|read_only)\s*$", bloco, re.M)
print("|".join(sorted(p for p, _ in papeis)) or "SEM_PRESET")
PY
)"
case "$PRESET" in
  ""|AUSENTE) bad "A: 'config.context_map' nao existe em modules.yaml"; PRESET="" ;;
  *)          ok  "A: campo 'config.context_map' declarado" ;;
esac
echo "     preset medido: ${PRESET:-<nenhum>}"
case "|$PRESET|" in
  *"|developer|"*|"developer|"*|*"|developer") ok "B: o AUTOR (developer) esta no preset — a divisao alcanca quem autora" ;;
  *) case "$PRESET" in
       *developer*) ok "B: o AUTOR (developer) esta no preset — a divisao alcanca quem autora" ;;
       "")          bad "B: sem preset para medir" ;;
       *)           bad "B: preset e '$PRESET' — sem 'developer' a divisao e DECORATIVA: o autor continua sem alcancar o cadastro" ;;
     esac ;;
esac

# ── C/D — o portao resolve por (namespace, KEY), e so para esta key ──────────
if grep -q '("masking", "context_map"): "context_map"' "$ROUTER"; then
  ok "C: o portao mapeia 'masking.context_map' -> campo 'context_map'"
else
  bad "C: o portao NAO separa 'masking.context_map' (ver _NS_KEY_FIELD_OVERRIDES)"
fi
D_OUT="$(PYTHONPATH=packages/config-api/src python3 - <<'PY' 2>/dev/null
import sys
sys.modules.setdefault("_stub", None)
import importlib.util, pathlib, re
# Le a funcao sem importar o modulo (que puxa asyncpg/redis): AST sobre o fonte.
src = pathlib.Path("packages/config-api/src/plughub_config_api/router.py").read_text(encoding="utf-8")
ns = {}
m = re.search(r"_NS_KEY_FIELD_OVERRIDES = \{.*?\}\n", src, re.S)
n = re.search(r"_NS_FIELD_OVERRIDES = \{.*?\}\n", src, re.S)
f = re.search(r"def _ns_field\(.*?\n(?:.*?\n)*?    return _NS_FIELD_OVERRIDES\.get\(namespace, \"platform\"\)\n", src)
if not (m and n and f):
    print("NAO_EXTRAIU"); raise SystemExit(0)
exec(n.group(0) + m.group(0) + f.group(0), ns)
g = ns["_ns_field"]
print("%s|%s|%s" % (g("masking", "context_map"), g("masking", "types"), g("masking", "audit_policy")))
PY
)"
echo "     resolvido: context_map=${D_OUT%%|*}  types=$(echo "$D_OUT" | cut -d'|' -f2)  outra_key=$(echo "$D_OUT" | cut -d'|' -f3)"
case "$D_OUT" in
  "NAO_EXTRAIU"|"") huh "D: nao consegui exercer '_ns_field' — extracao falhou" ;;
  "context_map|masking|masking")
      ok "D: 'masking.types' CONTINUA em 'masking' — o portao separa a key certa, e so ela" ;;
  *)  bad "D: resolucao inesperada ($D_OUT) — se 'types' saiu de 'masking', o CATALOGO foi entregue ao autor" ;;
esac

# ── E — existe PORTA ─────────────────────────────────────────────────────────
if grep -q "field: 'context_map'" "$NAV"; then
  ok "E: ha item de menu gateado por 'config.context_map'"
else
  bad "E: nenhuma porta — grant sem item de menu nao concede nada"
fi

# ── F — a tela oferece SO o catalogo ─────────────────────────────────────────
if [ ! -f "$PAGE" ]; then
  bad "F: $PAGE ausente"
elif grep -q "dataTypes.map(dt =>" "$PAGE" && ! grep -qE "<input[^>]*tipo|novoTipo|addType" "$PAGE"; then
  ok "F: o seletor de tipo vem do CATALOGO, e a tela nao oferece criar tipo"
else
  bad "F: a tela parece permitir tipo fora do catalogo — o autor escolhe, nao cria"
fi

# ── G — ao vivo: o split vale no SERVIDOR ────────────────────────────────────
# ⚠️ 3202, nao 3200: a auth-api escuta 3200 DENTRO do container e o compose a publica
# em 3202 no host. Errar isto faz o ramo sair SEM AMOSTRA, que e honesto mas inutil.
TMPDIR_G="$(dirname "$0")/.alw03_tmp"
mkdir -p "$TMPDIR_G"
trap 'rm -rf "$TMPDIR_G"' EXIT

AUTH="${AUTH_URL:-http://localhost:3202/auth}"
CFG="${CONFIG_URL:-http://localhost:3600}"
TENANT="${TENANT_ID:-tenant_demo}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@plughub.local}"
ADMIN_PASS="${ADMIN_PASS:-changeme_admin}"

login() {
  curl -s --max-time 10 -X POST "$AUTH/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null
}
T_ADMIN="$(login "$ADMIN_EMAIL" "$ADMIN_PASS")"
if [ -z "$T_ADMIN" ]; then
  huh "G: sem login de admin — SEM AMOSTRA (nao e verde)"
else
  EMAIL="alw03probe@plughub.local"; PASS="changeme_alw03"
  OLD="$(curl -s --max-time 10 "$AUTH/users?tenant_id=$TENANT" -H "Authorization: Bearer $T_ADMIN" \
    | python3 -c "
import sys,json
try: us=json.load(sys.stdin)
except Exception: us=[]
us = us.get('users', us) if isinstance(us, dict) else us
print(next((u.get('id') or u.get('user_id','') for u in us if u.get('email')=='$EMAIL'), ''))" 2>/dev/null)"
  # A limpeza da cobaia VELHA e load-bearing, nao higiene: sem ela o `POST /users` devolve
  # "Email already registered" e o gate passa a medir um usuario de OUTRA execucao, cujo
  # `module_config` nao e o que ele acabou de pedir. Custou uma rodada em 2026-09-02, com
  # o ramo G acusando 403 no mapa por causa de uma cobaia sobrevivente de config vazio.
  [ -n "$OLD" ] && curl -s -o /dev/null --max-time 10 -X DELETE "$AUTH/users/$OLD" -H "Authorization: Bearer $T_ADMIN"

  UID_NEW="$(curl -s --max-time 10 -X POST "$AUTH/users" -H "Authorization: Bearer $T_ADMIN" \
    -H 'content-type: application/json' -d "{
      \"email\":\"$EMAIL\",\"password\":\"$PASS\",\"full_name\":\"ALW-03 probe\",
      \"tenant_id\":\"$TENANT\",\"roles\":[\"developer\"],
      \"module_config\":{\"config\":{\"context_map\":{\"access\":\"read_write\",\"scope\":[]}}}
    }" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('id') or d.get('user_id',''))" 2>/dev/null)"

  T_DEV="$(login "$EMAIL" "$PASS")"
  if [ -z "$T_DEV" ]; then
    huh "G: nao consegui criar/logar o principal de teste — SEM AMOSTRA"
  else
    # ⚠️ O gate NAO ESCREVE config real, e a versao que escrevia CORROMPEU o store.
    #
    # A primeira versao relia o mapa vivo e o reescrevia "identico". Nao era identico: o
    # payload tem ~7,6 kB com acentos e atravessava uma variavel de shell, e nesta bancada
    # (Git Bash + python de Windows) isso mangla os bytes — o rotulo
    # 'Classificada na LEITURA — sem produtor proprio' foi gravado como mojibake, e as
    # execucoes seguintes preservaram fielmente o estrago. Reparado a partir do espelho em
    # `py-contextstore/default_map.py`, que e o mesmo valor da TS.
    #
    # A correcao NAO foi 'escrever com mais cuidado' — foi PARAR DE ESCREVER. O portao vive
    # num `Depends`, logo ele decide ANTES da validacao do corpo:
    #
    #     nao autorizado          -> 403   (o portao)
    #     autorizado + corpo mau  -> 422   (a validacao, e nada e gravado)
    #
    # Entao um corpo deliberadamente invalido separa os dois casos sem tocar em dado. Medido
    # em 2026-09-02, e o mapa seguiu com 94 folhas depois da medicao.
    CORPO_INVALIDO='{"NAO_E_VALUE": 1}'
    C_MAP="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X PUT \
      "$CFG/config/masking/context_map" -H "Authorization: Bearer $T_DEV" \
      -H 'content-type: application/json' -d "$CORPO_INVALIDO")"
    C_TYPES="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X PUT \
      "$CFG/config/masking/types" -H "Authorization: Bearer $T_DEV" \
      -H 'content-type: application/json' -d "$CORPO_INVALIDO")"
    echo "     ao vivo (corpo invalido, nada e gravado): context_map=$C_MAP  types=$C_TYPES"
    case "$C_MAP" in
      422) ok "G: o autor PASSA o portao do mapa (422 = chegou na validacao) — o positivo" ;;
      403) bad "G: o autor foi BARRADO no mapa (403) — o grant nao alcanca" ;;
      2*)  bad "G: PUT com corpo invalido devolveu $C_MAP — a validacao nao roda, e o gate estaria ESCREVENDO" ;;
      *)   huh "G: mapa devolveu $C_MAP (esperado 422)" ;;
    esac
    case "$C_TYPES" in
      403) ok "G: e e RECUSADO no catalogo (403, antes da validacao) — a divisao vale no servidor" ;;
      422) bad "G: o autor PASSOU o portao do CATALOGO — a divisao nao vale no servidor" ;;
      *)   huh "G: catalogo devolveu $C_TYPES (esperado 403)" ;;
    esac
  fi
  [ -n "$UID_NEW" ] && curl -s -o /dev/null --max-time 10 -X DELETE "$AUTH/users/$UID_NEW" -H "Authorization: Bearer $T_ADMIN"
fi

echo
# ── H — o split nao TIROU capacidade de ninguem ──────────────────────────────
#
# Preset so se aplica na CRIACAO (regra do repo: 'editar o preset nao muda quem ja
# existe'). Logo, separar um campo de outro REMOVE capacidade de quem ja tinha o campo
# antigo — e isso e regressao introduzida pela mudanca, nao decisao. O backfill foi feito
# em 2026-09-02 (1 usuario); este ramo impede que a regressao volte pela porta de tras,
# num tenant novo ou num reset de base.
#
# Mede pelo ADMIN logado, que por definicao tem `config.masking`: se ele tambem tem
# `config.context_map`, a implicacao vale para o caso que mais importa.
if [ -z "${T_ADMIN:-}" ]; then
  huh "H: sem token de admin — SEM AMOSTRA"
else
  H_OUT="$(printf "%s" "$T_ADMIN" | cut -d. -f2 | PYTHONIOENCODING=utf-8 python3 -c "
import sys, base64, json
s = sys.stdin.read().strip(); s += '=' * (-len(s) % 4)
cfg = json.loads(base64.urlsafe_b64decode(s)).get('module_config', {}).get('config', {})
def acc(f):
    v = cfg.get(f)
    return (v or {}).get('access', 'none') if isinstance(v, dict) else 'none'
print('%s|%s' % (acc('masking'), acc('context_map')))
" 2>/dev/null)"
  echo "     admin: masking=${H_OUT%%|*}  context_map=${H_OUT##*|}"
  case "$H_OUT" in
    read_write\|read_write|write_only\|write_only|read_write\|write_only|write_only\|read_write)
        ok "H: quem tinha o catalogo NAO perdeu o mapa — o backfill vale" ;;
    read_write\|*|write_only\|*)
        bad "H: o admin tem o catalogo e NAO tem o mapa — o split removeu capacidade (falta backfill)" ;;
    "") huh "H: nao consegui ler os claims do admin — SEM AMOSTRA" ;;
    *)  huh "H: admin sem 'config.masking' ($H_OUT) — a implicacao nao e exercivel" ;;
  esac
fi

if [ "$FAIL" = "0" ]; then
  echo "OK — o cadastro do mapa alcanca o autor, e o catalogo continua com compliance"
elif [ "$FAIL" = "2" ]; then
  echo "INCONCLUSIVO"
else
  echo "FALHA"
fi
exit "$FAIL"
