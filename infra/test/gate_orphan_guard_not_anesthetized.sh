#!/usr/bin/env bash
# ==============================================================================
# gate_orphan_guard_not_anesthetized.sh — o aviso de pool SEM VIGIA tem o que dizer
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
#   A  CENSO — quem sao as contas ATIVAS e quantos pools cada uma alcanca
#   B  nenhuma conta de FIXTURE cobre o tenant inteiro em repouso
#   C  CONTROLE POSITIVO — existe pelo menos um pool com UM unico vigia, senao o
#      aviso nunca teria o que dizer (e o verde seria por ausencia de amostra)
#   D  o escopo efemero fecha o ciclo: `mk_unrestricted_principal.sh` concede e
#      `--revogar` devolve, LIDO DE VOLTA da API
#   E  PREFLIGHT — com uma fixture sintetica de escopo total no calculo, o ramo B
#      acusaria; sem isto, B poderia estar verde por nao saber olhar
#
# POR QUE ELE EXISTE (AUT-43, 2026-09-10)
# ---------------------------------------
# `pool-coverage.ts` (AUT-14) avisa quando uma mudanca — desativar alguem, apagar,
# tirar pools do escopo — deixaria um pool sem NENHUM usuario ativo que o alcance. O
# sintoma do pool orfao e AUSENCIA: ele continua recebendo contato, enfileirando e
# consumindo licenca, invisivel no Monitor, no Console e nos relatorios.
#
# Medido em 2026-09-08 e de novo em 2026-09-10: o aviso estava **anestesiado por uma
# fixture**. `probe@plughub.local` — criada por `mk_unrestricted_principal.sh`, com a
# senha no proprio repositorio — carregava **43** pools (os 41 do registry mais 2 que
# nem existem). Com ela na populacao, desativar `admin@` (unico vigia de **36** pools)
# avisaria sobre **ZERO**. Sem ela: **36**. O guarda ficava mudo exatamente no instante
# que existe para cobrir, e a E5 do ADR de granularidade se apoiava nele.
#
# ⚠️ O conserto NAO foi ensinar o produto a reconhecer fixture — "conta de teste" nao e
# eixo do dominio, e um campo desses seria a porta larga com outro nome. Foi tirar a
# fixture do estado que anestesia: **o escopo total virou EFEMERO** (concedido por quem
# precisa, revogado no `trap EXIT`), e em repouso a conta fica com `[]`.
#
# ⚠️ C nao e enfeite. Se toda linha tiver dois ou mais vigias, B pode passar com o
# aviso igualmente inerte — so que por outra razao. Exposicao e dano sao dois fatos
# (D14.1), e este gate mede os dois.
#
# ⚠️ **O ramo D CONSERTA o que B mede, e por isso um vermelho de B nao se repete.** D
# exercita o ciclo do helper e deixa a fixture em repouso; entao, depois de um B
# vermelho, a execucao seguinte passa. Isso e proposital (o gate devolve o ambiente),
# mas muda o que o vermelho SIGNIFICA: ele nao diz *"o produto regrediu"*, diz **"esta
# instalacao esta com a fixture larga agora"** — tipicamente um gate que morreu antes do
# `trap`. Quem investigar tem de olhar a execucao que produziu o vermelho, nao a
# proxima; e por isso a mensagem de falha nomeia a conta e o comando que a devolve.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

AUTH="${AUTH:-http://localhost:3202/auth}"
REGISTRY="${REGISTRY:-http://localhost:3300}"
TENANT="${TENANT:-tenant_demo}"
ADMIN_EMAIL="${ORPHAN_ADMIN_EMAIL:-admin@plughub.local}"
ADMIN_PASS="${ORPHAN_ADMIN_PASS:-changeme_admin}"
FIXTURE_EMAIL="${UNRESTRICTED_EMAIL:-probe@plughub.local}"

command -v python3 >/dev/null || { echo "INCONCLUSIVO: python3 ausente"; exit 2; }
command -v jq      >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }

TOK="$(curl -s --max-time 15 -X POST "$AUTH/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\",\"tenant_id\":\"$TENANT\"}" \
  | jq -r '.access_token // empty')"
[ -n "$TOK" ] || { echo "INCONCLUSIVO: login de $ADMIN_EMAIL falhou"; exit 2; }

USERS="$(curl -s --max-time 15 "$AUTH/users?tenant_id=$TENANT" -H "Authorization: Bearer $TOK")"
POOLS="$(curl -s --max-time 15 "$REGISTRY/v1/pools?limit=500" -H "x-tenant-id: $TENANT")"

# ── ramos A, B, C e E, num calculo so ────────────────────────────────────────
# O calculo mora aqui e nao no `pool-coverage.ts` porque a proposicao e sobre a
# POPULACAO da instalacao, nao sobre a funcao: a logica dela tem teste de unidade
# proprio, e duplicar a logica seria a segunda casa que este repositorio recusa. O que
# se replica e so a contagem `pool -> vigias ativos`, que e uma linha.
# ⚠️ o script vai para ARQUIVO e os dados por stdin: `python3 - <<'PY' <<< "$DADOS"`
# tem duas redirecoes de stdin e a ultima vence — o heredoc com o programa seria
# descartado em silencio, e o python leria os DADOS como codigo.
CALC_PY="$(mktemp /tmp/_orphan_calc_XXXXXX.py)"
trap 'rm -f "$CALC_PY"' EXIT
cat > "$CALC_PY" <<'PY'
import json, sys

fixture_ref = sys.argv[1]
d = json.load(sys.stdin)
users = d["users"] if isinstance(d["users"], list) else []
pools = [p.get("pool_id") or p.get("id") for p in (d["pools"].get("pools") or d["pools"].get("items") or [])]
pools = [p for p in pools if p]

VERDE, VERMELHO = "\033[32m", "\033[31m"
AMARELO, FIM, NEGRITO = "\033[33m", "\033[0m", "\033[1m"
falhou = 0


def ok(m):  print(f"  {VERDE}OK{FIM}           {m}")
def bad(m):
    global falhou
    falhou = 1
    print(f"  {VERMELHO}FALHA{FIM}        {m}")
def nte(m): print(f"  {AMARELO}SEM AMOSTRA{FIM}  {m}")
def sec(m): print(f"\n{NEGRITO}{m}{FIM}")


if not pools or not users:
    print("INCONCLUSIVO: registry ou auth-api devolveram lista vazia — nada a medir")
    raise SystemExit(2)


def vigias(pop):
    """pool -> nº de usuarios ATIVOS que o alcancam. Todo pool tem chave (zero explicito)."""
    por_pool = {p: 0 for p in pools}
    for u in pop:
        if not u.get("active"):
            continue
        for p in (u.get("accessible_pools") or []):
            if p in por_pool:
                por_pool[p] += 1
    return por_pool


ativos = [u for u in users if u.get("active")]
cobre_tudo = [u for u in ativos if set(pools) <= set(u.get("accessible_pools") or [])]

print(f"{NEGRITO}gate: o aviso de pool sem vigia esta vivo?{FIM}")
sec("A — censo da populacao viva")
print(f"  {len(pools)} pools no registry · {len(ativos)} contas ativas")
for u in sorted(ativos, key=lambda x: -len(x.get("accessible_pools") or []))[:12]:
    n = len(u.get("accessible_pools") or [])
    marca = "  ← cobre TODOS" if u in cobre_tudo else ""
    print(f"    {u['email']:34s} {n:3d} pools{marca}")

sec("B — nenhuma FIXTURE cobre o tenant inteiro em repouso")
# "Fixture" e criterio do INSTRUMENTO, nunca do produto: aqui ele so decide de quem
# COBRAR, e a conta real que cobre tudo (o admin provisionado) e legitima.
def eh_fixture(email):
    return "probe" in email.split("@")[0].lower()

anestesiam = [u for u in cobre_tudo if eh_fixture(u["email"])]
if anestesiam:
    for u in anestesiam:
        bad(f"{u['email']} cobre os {len(pools)} pools — com ela ativa, o aviso de orfao "
            f"nao dispara para mudanca nenhuma")
    print(f"    devolver com: bash infra/test/mk_unrestricted_principal.sh --revogar")
else:
    reais = [u["email"] for u in cobre_tudo]
    ok(f"nenhuma fixture com escopo total"
       + (f" (cobrem tudo, e sao reais: {', '.join(reais)})" if reais else ""))

sec("C — ha pool com UM unico vigia (o aviso tem o que dizer)")
por_pool = vigias(users)
solitarios = sorted(p for p, n in por_pool.items() if n == 1)
orfaos = sorted(p for p, n in por_pool.items() if n == 0)
if solitarios:
    ok(f"{len(solitarios)} pool(s) com um vigia so — desativar essa conta os orfanaria, "
       f"e e disso que o aviso fala (ex.: {', '.join(solitarios[:3])})")
else:
    nte("todo pool tem 2+ vigias ou 0 — o aviso nao seria exercido por mudanca nenhuma; "
        "verde aqui seria por ausencia de amostra")
print(f"    orfaos HOJE: {len(orfaos)}" + (f" ({', '.join(orfaos[:5])})" if orfaos else ""))

sec("E — PREFLIGHT: o ramo B sabe acusar?")
sintetico = ativos + [{"email": "probe_sintetico@plughub.local", "active": True,
                       "accessible_pools": list(pools)}]
pegou = [u for u in sintetico if set(pools) <= set(u.get("accessible_pools") or [])
         and eh_fixture(u["email"])]
if any(u["email"] == "probe_sintetico@plughub.local" for u in pegou):
    ok("com uma fixture de escopo total injetada no calculo, B acusaria")
else:
    bad("o proprio detector nao pega a fixture sintetica — B esta cego, e o verde dele "
        "nao significa nada")

raise SystemExit(1 if falhou else 0)
PY
python3 "$CALC_PY" "$FIXTURE_EMAIL" <<< "$(jq -nc --argjson u "$USERS" --argjson p "$POOLS" '{users:$u, pools:$p}')"
RC_PY=$?

# ── D. o ciclo do escopo efemero, lido de volta ──────────────────────────────
printf '\n\033[1mD — conceder e devolver: o ciclo fecha\033[0m\n'
n_pools_fixture() {
  curl -s --max-time 15 "$AUTH/users?tenant_id=$TENANT" -H "Authorization: Bearer $TOK" \
    | jq -r ".[] | select(.email==\"$FIXTURE_EMAIL\") | (.accessible_pools|length)" | head -1
}
RC_D=0
if bash infra/test/mk_unrestricted_principal.sh >/dev/null 2>&1; then
  N_CONCEDIDO="$(n_pools_fixture)"
  bash infra/test/mk_unrestricted_principal.sh --revogar >/dev/null 2>&1
  N_REPOUSO="$(n_pools_fixture)"
  if [ "${N_CONCEDIDO:-0}" -gt 0 ] && [ "${N_REPOUSO:-1}" = "0" ]; then
    printf '  \033[32mOK\033[0m           concedido=%s pools · repouso=%s (lido de volta da API)\n' \
      "$N_CONCEDIDO" "$N_REPOUSO"
  else
    printf '  \033[31mFALHA\033[0m        concedido=%s · repouso=%s — o ciclo nao fecha, e a fixture pode ficar larga\n' \
      "${N_CONCEDIDO:-?}" "${N_REPOUSO:-?}"
    RC_D=1
  fi
else
  printf '  \033[33mINCONCLUSIVO\033[0m mk_unrestricted_principal.sh falhou — rode-o a mao para ver o motivo\n'
  RC_D=2
fi

printf '\n'
if [ "$RC_PY" = "2" ] || [ "$RC_D" = "2" ]; then printf '\033[33mINCONCLUSIVO\033[0m\n'; exit 2; fi
if [ "$RC_PY" = "0" ] && [ "$RC_D" = "0" ]; then
  printf '\033[32mVERDE\033[0m — o aviso de pool sem vigia nao esta anestesiado.\n'; exit 0
fi
printf '\033[31mVERMELHO\033[0m\n'; exit 1
