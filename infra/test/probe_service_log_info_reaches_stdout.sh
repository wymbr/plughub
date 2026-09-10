#!/usr/bin/env bash
# ==============================================================================
# probe_service_log_info_reaches_stdout.sh — INFO do `plughub.*` SAI, ou nao existe
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
#   A  CENSO VIVO — todo servico cujo entry point IMPORTA o app (`uvicorn …:app` e
#      console-script) tem `plughub.authz` habilitado em INFO, medido IMPORTANDO o
#      modulo que o CMD nomeia
#   B  FIM A FIM — chamada real com escopo vazio faz a linha da AUT-03 aparecer no
#      log do analytics-api, nomeando o `sub` de quem chamou
#   C  CONTROLE NEGATIVO — chamador COM escopo nao produz a linha (ela significa o
#      que diz, em vez de sair sempre)
#
# ⚠️ **B sem C nao vale.** Um produtor que logasse a linha em toda chamada deixaria B
# verde, e a linha deixaria de distinguir populacao nenhuma — o que ela existe para
# dizer e *"este chamador nao alcanca pool nenhum"*.
#
# POR QUE ELE EXISTE (AUT-43, 2026-09-10)
# ---------------------------------------
# A AUT-03 declarou no `done.md` que *"o caminho vazio nao ficou mudo: virou
# `logger.info` que nomeia a origem"*. Medido: **nunca chegou a log nenhum**. O CMD
# destes servicos e `uvicorn …:app`, que configura so os loggers `uvicorn*`; o root
# fica no default WARNING e **todo `logger.info` do repositorio e descartado**. E o
# defeito e ASSIMETRICO: `logger.warning` continua saindo pelo handler de ultimo
# recurso do Python, entao o log parece normal — quem olha nao ve buraco, ve silencio.
#
# Ja catalogado em `TODO.md` § "Seis servicos rodam SEM logging configurado"
# (2026-08-07), com o conserto prescrito e nunca aplicado. ⚠️ E nao eram seis: eram
# **SETE**, e a lista errava dos DOIS lados —
#
#   · 2 falsos POSITIVOS: `scheduler-api` e `mailing-api` ja configuravam, por outro
#     mecanismo (handler no logger `plughub`), e o censo de la contava `basicConfig`;
#   · 3 AUSENTES: `workflow-api`, `quality-ingest` e `quality-export`, dados como
#     sadios por REGRA (*"nos console-script a funcao que configura E o entry point"*)
#     — e nesses tres nao existe funcao que configure: zero `basicConfig`, zero
#     `addHandler`, zero `setLevel` no pacote inteiro.
#
# Censo por marcador erra nos dois sentidos, e o que ele deixa de fora nao aparece em
# contagem nenhuma. Por isso este ramo mede COMPORTAMENTO (o logger responde INFO?), e
# nao a presenca de uma chamada.
#
# ⚠️ O ramo A NAO le o fonte. `basicConfig` num modulo que o CMD nao importa e
# exatamente o defeito original (channel-gateway, 2026-08-07): o codigo esta la,
# correto, e nao roda. So a IMPORTACAO do modulo do CMD responde a pergunta certa.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
TENANT="${TENANT:-tenant_demo}"
AUTH="${AUTH:-http://localhost:3202/auth}"
AN="${AN:-http://localhost:3500}"
VAZIO_EMAIL="${VAZIO_EMAIL:-probe_rowscope@plughub.local}"
VAZIO_PASS="${VAZIO_PASS:-probe_rowscope_123}"
CHEIO_EMAIL="${CHEIO_EMAIL:-operator@plughub.local}"
CHEIO_PASS="${CHEIO_PASS:-changeme_operator}"

FALHOU=0
ok()  { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad() { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; FALHOU=1; }
inc() { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; exit 2; }
sec() { printf '\n\033[1m%s\033[0m\n' "$1"; }

command -v jq >/dev/null || inc "jq ausente"

printf '\033[1mgate: o INFO do repositorio chega a quem le o log\033[0m\n'

# ── A. censo vivo, importando o modulo que o CMD nomeia ──────────────────────
sec "A — todo servico uvicorn emite INFO do namespace \`plughub\`"

CENSO_PY="$(mktemp /tmp/_censo_log_XXXXXX.py)"
trap 'rm -f "$CENSO_PY"' EXIT
{
  echo 'import importlib, logging, sys'
  echo 'try:'
  echo '    importlib.import_module(sys.argv[1])'
  echo 'except Exception as e:'
  echo '    print("IMPORT_FALHOU %s: %s" % (type(e).__name__, e))'
  echo '    raise SystemExit(3)'
  echo 'print("INFO=%s" % logging.getLogger("plughub.authz").isEnabledFor(logging.INFO))'
} > "$CENSO_PY"

# A populacao sai dos Dockerfiles, nao de uma lista escrita a mao: servico novo entra
# sozinho — uma lista parece completa por ser uma lista (GAT-01). Duas familias:
#
#   · `CMD ["uvicorn", "<mod>:app"]`  — o uvicorn IMPORTA o modulo e nunca chama `run()`,
#     entao configurar dentro de uma funcao nao vale; tem de valer no import;
#   · `CMD ["<console-script>"]`      — o script chama `main:run`, que importa o modulo
#     antes de rodar; medir por import responde a mesma pergunta.
#
# ⚠️ ISENTA, e a isencao e DECLARADA: `CMD ["python", "-m", "<mod>"]` (auth-api,
# routing-engine, session-replayer, usage-aggregator). La o entry point EXECUTA a funcao,
# entao configurar dentro dela e legitimo — e um censo por import daria VERMELHO FALSO
# num servico que loga certo em producao. Medir essa familia exige outra pergunta (a
# linha de boot no log), e ela nao esta neste gate.
ALVOS="$(
  grep -l 'CMD \["uvicorn"' packages/*/Dockerfile 2>/dev/null | while read -r df; do
    p="$(basename "$(dirname "$df")")"
    m="$(grep -o '"[a-z_]*\.main:app"' "$df" | head -1 | tr -d '"' | sed 's/:app$//')"
    [ -n "$m" ] && echo "$p $m"
  done
  for df in packages/*/Dockerfile; do
    p="$(basename "$(dirname "$df")")"
    cs="$(grep -o 'CMD \["plughub-[a-z-]*"\]' "$df" 2>/dev/null | head -1 | sed 's/.*\["//; s/"\]//')"
    [ -n "$cs" ] || continue
    m="$(grep -o "$cs = \"[a-z_]*\.main:" "packages/$p/pyproject.toml" 2>/dev/null          | head -1 | sed 's/.*"//; s/:$//')"
    [ -n "$m" ] && echo "$p $m"
  done
)"
[ -n "$ALVOS" ] || inc "nenhum servico com CMD uvicorn/console-script encontrado — o censo nao mediu nada"

SERVICOS="$($COMPOSE ps --services 2>/dev/null)"
MEDIDOS=0
while read -r svc modulo; do
  [ -n "$svc" ] || continue
  if ! printf '%s\n' "$SERVICOS" | grep -qx "$svc"; then
    printf '  \033[33m—\033[0m            %s: sem servico com esse nome no compose (pulado)\n' "$svc"
    continue
  fi
  saida="$($COMPOSE exec -T "$svc" python - "$modulo" < "$CENSO_PY" 2>&1 | tail -1)"
  MEDIDOS=$((MEDIDOS+1))
  case "$saida" in
    INFO=True)  ok "$svc ($modulo)" ;;
    INFO=False) bad "$svc ($modulo): \`plughub.*\` em INFO NAO sai — todo logger.info do repositorio e descartado aqui" ;;
    *)          bad "$svc ($modulo): nao consegui medir — $saida" ;;
  esac
done <<EOF
$ALVOS
EOF
[ "$MEDIDOS" -gt 0 ] || inc "nenhum servico uvicorn de pe — a stack esta rodando?"
echo "  ($MEDIDOS servicos medidos)"

# ── B/C. a linha da AUT-03, com o controle negativo ao lado ──────────────────
login() {
  curl -s --max-time 15 -X POST "$AUTH/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty'
}
claim() { echo "$1" | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null | jq -r "$2"; }

T_VAZIO="$(login "$VAZIO_EMAIL" "$VAZIO_PASS")"
T_CHEIO="$(login "$CHEIO_EMAIL" "$CHEIO_PASS")"
[ -n "$T_VAZIO" ] || inc "login de $VAZIO_EMAIL falhou (fixture de escopo vazio ausente?)"
[ -n "$T_CHEIO" ] || inc "login de $CHEIO_EMAIL falhou"
S_VAZIO="$(claim "$T_VAZIO" '.sub // empty')"
S_CHEIO="$(claim "$T_CHEIO" '.sub // empty')"
N_VAZIO="$(claim "$T_VAZIO" '(.accessible_pools // [])|length')"
N_CHEIO="$(claim "$T_CHEIO" '(.accessible_pools // [])|length')"
[ "${N_VAZIO:-1}" = "0" ] || inc "$VAZIO_EMAIL tem $N_VAZIO pools — o ramo do dominio VAZIO nao seria exercido"
[ "${N_CHEIO:-0}" != "0" ] || inc "$CHEIO_EMAIL tem escopo vazio — nao serve de controle negativo"

DESDE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sleep 1
for T in "$T_VAZIO" "$T_CHEIO"; do
  curl -s -o /dev/null --max-time 20 "$AN/reports/sessions?tenant_id=$TENANT&limit=5" -H "Authorization: Bearer $T"
done
sleep 2
LOG="$($COMPOSE logs --since "$DESDE" analytics-api 2>&1)"

sec "B — a chamada de escopo VAZIO deixa rastro (promessa da AUT-03)"
N_LINHA="$(printf '%s' "$LOG" | grep -c "authz scope.*sub=$S_VAZIO")"
if [ "${N_LINHA:-0}" -ge 1 ]; then
  ok "linha \`authz scope\` presente, nomeando sub=$S_VAZIO"
else
  bad "nenhuma linha para sub=$S_VAZIO — a promessa da AUT-03 ('o caminho vazio nao ficou mudo') segue quebrada"
fi

sec "C — e o chamador COM escopo ($N_CHEIO pools) nao produz a linha"
N_FALSO="$(printf '%s' "$LOG" | grep -c "authz scope.*sub=$S_CHEIO")"
if [ "${N_FALSO:-0}" = "0" ]; then
  ok "sub=$S_CHEIO nao aparece — a linha distingue populacao, em vez de sair sempre"
else
  bad "a linha saiu tambem para quem TEM escopo ($N_FALSO x) — ela deixaria de significar o que diz"
fi

printf '\n'
[ "$FALHOU" = 0 ] && { printf '\033[32mVERDE\033[0m — o que o codigo loga em INFO chega a quem le o log.\n'; exit 0; }
printf '\033[31mVERMELHO\033[0m\n'; exit 1
