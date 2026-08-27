#!/usr/bin/env bash
# ==============================================================================
# probe_role_preset_on_create.sh — usuario novo nasce com grants, ou nasce cego?
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
# Medido em 2026-08-27: `create_user` gravava `roles`, `accessible_pools`,
# `unrestricted` e `max_concurrent_sessions` — e NAO gravava `module_config`. Todo
# usuario criado pela tela nascia com config VAZIO, isto e, dentro da degradacao
# graciosa (`passesAbacRule` libera quando o config esta vazio). O menu "funcionava"
# porque o buraco o sustentava.
#
# Isso torna a inversao daquela degradacao (passo 6: vazio = nao pode nada) uma
# armadilha: sem preset, cada usuario novo nasceria CEGO — menu so com Home — e quem
# o criou leria como "a tela de Acesso quebrou". Este gate e o que segura a ordem.
#
# O QUE O DEIXARIA VERMELHO
# -------------------------
#   S1  usuario criado com um papel nasce com `module_config` VAZIO;
#   S2  um papel DECLARADO (`Role` de models.py) sem preset no catalogo — o "nascer
#       cego" esperando o primeiro usuario daquele papel;
#   S3  o preset nao respeitar o `domain` do campo (o 422 apareceria so na criacao);
#   S4  papeis acumulados nao renderem o MAIOR acesso (admin+operator tem de ter
#       `contacts.operacao`, que so o operator concede... e vice-versa).
#
# TESTEMUNHA NEGATIVA:
#   S5  papel INEXISTENTE nao pode inventar grants — nasce vazio, e isso e o certo.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_auth.sh"

fail=0
ok()   { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad()  { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; fail=1; }
inc()  { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; fail=1; }
info() { printf '               %s\n' "$1"; }
sec()  { printf '\n\033[1m%s\033[0m\n' "$1"; }

command -v jq >/dev/null || { inc "jq ausente"; exit 2; }

printf '\033[1mprobe: preset de papel na criacao de usuario\033[0m\n'
printf '  auth-api: %s   tenant: %s\n' "$AUTH" "$TENANT"

T="$(plughub_token)"

cria() {  # $1 = email, $2 = json de roles -> ecoa o id (vazio em falha)
  curl -s --max-time 15 -X POST "$AUTH/users" \
    -H "Authorization: Bearer $T" -H 'content-type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"email\":\"$1\",\"name\":\"Preset Probe\",\"password\":\"changeme_preset_probe\",\"roles\":$2}" \
    | jq -r '.id // empty'
}
apaga() { [ -n "$1" ] && curl -s -o /dev/null -X DELETE "$AUTH/users/$1" -H "Authorization: Bearer $T"; }
uid_de() { curl -s --max-time 15 "$AUTH/users?tenant_id=$TENANT" -H "Authorization: Bearer $T" \
  | jq -r --arg e "$1" '.[] | select(.email == $e) | .id' | head -1; }
cfg_de() { curl -s --max-time 15 "$AUTH/users/$1/module-config" -H "Authorization: Bearer $T"; }
campos() { printf '%s' "$1" | jq '[.[] | to_entries[]] | length' 2>/dev/null; }

# ── S2 — todo papel declarado tem preset (conferido no FONTE) ────────────────
sec "S2 - todo papel declarado tem preset no catalogo"
SEM="$(python3 - "$ROOT/packages/auth-api/src/plughub_auth_api/models.py" "$ROOT/infra/modules.yaml" <<'PY'
import re, sys, yaml
src = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r"Role\s*=\s*Literal\[([^\]]*)\]", src)
if not m:
    print("ERRO:nao achei o Literal de Role em models.py"); raise SystemExit(0)
papeis = re.findall(r'"([^"]+)"', m.group(1))
doc = yaml.safe_load(open(sys.argv[2], encoding="utf-8"))
citados = set()
for mod in doc["modules"]:
    for d in mod["permission_schema"].values():
        citados.update((d.get("role_defaults") or {}).keys())
faltando = [p for p in papeis if p not in citados]
orfaos = [c for c in sorted(citados) if c not in papeis]
print("PAPEIS:%d" % len(papeis))
print("FALTANDO:%s" % ",".join(faltando))
print("ORFAOS:%s" % ",".join(orfaos))
PY
)"
if printf '%s' "$SEM" | grep -q '^ERRO:'; then
  inc "$(printf '%s' "$SEM" | sed -n 's/^ERRO://p')"
else
  N_PAPEIS="$(printf '%s\n' "$SEM" | sed -n 's/^PAPEIS://p')"
  FALTA="$(printf '%s\n' "$SEM" | sed -n 's/^FALTANDO://p')"
  ORFAO="$(printf '%s\n' "$SEM" | sed -n 's/^ORFAOS://p')"
  if [ -n "$FALTA" ]; then
    bad "papel(is) declarado(s) SEM preset: $FALTA"
    info "Usuario criado com esse papel nasce sem grants — e o passo 6 o deixaria cego."
  else
    ok "os $N_PAPEIS papeis declarados aparecem em role_defaults"
  fi
  if [ -n "$ORFAO" ]; then
    bad "role_defaults cita papel que NAO existe em Role: $ORFAO"
    info "Preset orfao nunca e aplicado e da a impressao de cobertura que nao ha."
  else
    ok "nenhum role_defaults cita papel inexistente"
  fi
fi

# ── S3 — preset dentro do domain do campo ───────────────────────────────────
sec "S3 - todo preset esta dentro do domain do campo"
DOM="$(python3 - "$ROOT/infra/modules.yaml" <<'PY'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
ruins = []
n = 0
for mod in doc["modules"]:
    for campo, d in mod["permission_schema"].items():
        for papel, acc in (d.get("role_defaults") or {}).items():
            n += 1
            if acc not in (d.get("domain") or []):
                ruins.append("%s.%s[%s]=%s nao esta em %s"
                             % (mod["module_id"], campo, papel, acc, d.get("domain")))
print("N:%d" % n)
for r in ruins:
    print("RUIM:%s" % r)
PY
)"
N_PRESETS="$(printf '%s\n' "$DOM" | sed -n 's/^N://p')"
if printf '%s\n' "$DOM" | grep -q '^RUIM:'; then
  bad "preset fora do domain declarado:"
  printf '%s\n' "$DOM" | grep '^RUIM:' | sed 's/^RUIM:/                 /'
  info "Isto so apareceria como 422 na criacao do usuario, longe da causa."
elif [ "${N_PRESETS:-0}" -eq 0 ]; then
  inc "nenhum role_defaults no catalogo — nao ha preset a conferir"
  info "Verde aqui seria vacuo: o gate nao testou proposicao nenhuma."
else
  ok "os $N_PRESETS presets declarados estao dentro do domain"
fi

# ── S1 — o usuario NASCE com os grants ──────────────────────────────────────
# ⚠️ S1 mede o catalogo DEPLOYADO (o `auth.module_registry`, lido do YAML no boot do
# auth-api), enquanto S2/S3/S6 medem o ARQUIVO. Editar `infra/modules.yaml` sem
# reiniciar o auth-api faz os dois discordarem — e a discordancia e informacao
# ("existe" != "esta aplicado"), nao defeito do gate.
sec "S1 - usuario criado nasce com module_config preenchido (catalogo DEPLOYADO)"
for papel in operator supervisor admin developer business; do
  EM="preset_probe_${papel}@plughub.local"
  apaga "$(uid_de "$EM")"
  ID="$(cria "$EM" "[\"$papel\"]")"
  if [ -z "$ID" ]; then
    inc "nao consegui criar usuario com papel '$papel'"
    continue
  fi
  C="$(cfg_de "$ID")"
  N="$(campos "$C")"
  if [ "${N:-0}" -gt 0 ]; then
    ok "papel '$papel' nasce com $N campo(s) concedido(s)"
  else
    bad "papel '$papel' nasce com module_config VAZIO — nasceria cego no passo 6"
  fi
  apaga "$ID"
done

# ── S4 — papeis acumulados rendem o MAIOR acesso ────────────────────────────
sec "S4 - papeis acumulados rendem o MAIOR acesso, nao a interseccao"
EM="preset_probe_multi@plughub.local"
apaga "$(uid_de "$EM")"
ID="$(cria "$EM" '["operator","business"]')"
if [ -z "$ID" ]; then
  inc "nao consegui criar o usuario multi-papel"
else
  C="$(cfg_de "$ID")"
  OPER="$(printf '%s' "$C" | jq -r '.contacts.operacao.access // "none"')"
  if [ "$OPER" = "read_write" ]; then
    ok "operator+business mantem contacts.operacao=read_write (so o operator concede)"
  else
    bad "operator+business perdeu contacts.operacao (veio '$OPER') — parece interseccao"
    info "Acumular papel expressa acumular funcao; a uniao pelo MAIOR e a leitura certa."
  fi
  apaga "$ID"
fi

# ── S5 — testemunha negativa ────────────────────────────────────────────────
sec "S5 - papel inexistente nao inventa grants"
EM="preset_probe_fake@plughub.local"
apaga "$(uid_de "$EM")"
ID="$(cria "$EM" '["papel_que_nao_existe"]')"
if [ -z "$ID" ]; then
  ok "papel inexistente recusado na criacao (o Literal de Role valida)"
else
  C="$(cfg_de "$ID")"
  N="$(campos "$C")"
  if [ "${N:-0}" -eq 0 ]; then
    ok "papel inexistente nasce sem grants (correto)"
  else
    bad "papel inexistente recebeu $N campo(s) — o construtor esta inventando preset"
  fi
  apaga "$ID"
fi

# ── S6 — o seed do demo e o catalogo nao divergem ───────────────────────────
# Duas declaracoes da mesma coisa (o que cada papel recebe) que ninguem compara viram
# tres em seis meses. A comparacao e ESTATICA (arquivo x arquivo) de proposito: comparar
# com o usuario VIVO ficaria vermelho a cada edicao legitima pela tela de Acesso, e um
# gate que reprova por uso normal ensina a ignorar o vermelho.
sec "S6 - seed do demo x role_defaults do catalogo"
CMP="$(python3 "$HERE/_seed_vs_preset.py" \
       "$ROOT/infra/seed/seed_auth.py" "$ROOT/infra/modules.yaml" 2>&1)"
rc=$?
if [ "$rc" = "2" ]; then
  inc "nao consegui comparar seed x catalogo: $CMP"
elif [ "$rc" = "0" ]; then
  ok "$(printf '%s' "$CMP" | head -1)"
else
  bad "seed do demo e catalogo divergem:"
  printf '%s\n' "$CMP" | sed 's/^/                 /'
  info "Ou o preset esta errado, ou o seed concede algo que o papel nao deveria dar."
  info "Divergencia nao anotada e como as duas declaracoes viram tres."
fi

printf '\n'
if [ "$fail" -eq 0 ]; then
  printf '\033[32mVERDE\033[0m - o papel e preset de nascimento, e nenhum papel declarado nasce cego.\n'
else
  printf '\033[31mVERMELHO\033[0m - ver secoes acima.\n'
fi
exit "$fail"
