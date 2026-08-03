#!/usr/bin/env bash
# smoke_config_routing_orphan_keys.sh — portão da remoção das duas chaves órfãs
# do namespace `routing` (2026-08-03): `snapshot_ttl_s` e `score_weights`.
#
# O QUE ESTE PORTÃO EXISTE PARA PEGAR. Tirar a entrada do `_SEED` **não apaga a
# linha do banco** — o seed é *seed-if-absent*, e as linhas foram gravadas na
# primeira subida. Sem o DELETE explícito, o resultado da "remoção" seria: código
# limpo, tela continuando a exibir e a oferecer edição das duas chaves. O
# arquivo mudaria, o sistema não — que é o modo de falha "«foi escrito» ≠
# «mudou»" do CLAUDE.md § Postura de Engenharia.
#
# A remoção é feita pela API oficial (invariante *"provisioning only via
# official API"*), nunca por DELETE em SQL.
#
# Uso:  bash infra/test/smoke_config_routing_orphan_keys.sh
# Pré:  stack demo no ar, config-api já reconstruída com o seed novo.
# Sai:  0 = tudo verde · 1 = falhou · 2 = INCONCLUSIVO (não conseguiu medir).

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${PLUGHUB_TENANT_ID:-tenant_demo}"
ADMIN="${PLUGHUB_CONFIG_ADMIN_TOKEN:-demo_config_admin_token}"
KEYS=(snapshot_ttl_s score_weights)

FAIL=0; INCONCL=0
ok()      { printf '  ✅ %s\n' "$1"; }
bad()     { printf '  ❌ %s\n' "$1"; FAIL=$((FAIL+1)); }
unknown() { printf '  ⚠️  INCONCLUSIVO: %s\n' "$1"; INCONCL=$((INCONCL+1)); }
step()    { printf '\n── %s\n' "$1"; }

# curl dentro do container do config-api (a porta 3600 está publicada, mas rodar
# de dentro tira o host da equação).
api() {  # $1=método  $2=path+query  → imprime "<status>\n<corpo>"
  $DC exec -T config-api sh -lc \
    "python - <<'PY'
import json, urllib.request, urllib.error
req = urllib.request.Request(
    'http://localhost:3600$2', method='$1',
    headers={'X-Admin-Token': '$ADMIN'})
try:
    r = urllib.request.urlopen(req, timeout=8)
    print(r.status); print(r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code); print(e.read().decode())
except Exception as e:
    print(0); print(repr(e))
PY" < /dev/null
}

echo "══ portão: chaves órfãs do namespace routing ══"
echo "PREVISÃO: o DELETE global devolve 200 para as DUAS chaves (o config-seed as"
echo "  gravou na primeira subida). O DELETE por tenant tende a 404 — não há"
echo "  override, o valor que a tela mostrava vinha do global. 404 no tenant é"
echo "  ESPERADO; 404 no global significaria que a linha nunca existiu, e aí a"
echo "  medição de hoje (a tela exibindo 120) precisaria de outra explicação."

# ── 1. estado ANTES ─────────────────────────────────────────────────────────
#
# RE-EXECUÇÃO (corrigido após a 1ª rodada). O portão precisa poder rodar de novo:
# um portão que só vale uma vez não é portão — na segunda vez ninguém o roda, e
# a regressão passa. Mas a segunda rodada encontra as chaves JÁ removidas, e aí
# `404` no DELETE global significa "já aplicado", não "a linha nunca existiu".
# São estados opostos com o mesmo código HTTP; o que os distingue é o estado
# ANTES, medido aqui e usado como expectativa no passo 2.
step "1. estado ANTES (o que a tela mostra hoje)"
BEFORE="$(api GET "/config/routing?tenant_id=$TENANT" | tail -n +2)"
declare -A PRESENT
if [ -z "$BEFORE" ]; then
  unknown "GET do estado ANTES veio vazio — sem baseline, o passo 2 não julga"
  for k in "${KEYS[@]}"; do PRESENT[$k]="?"; done
else
  for k in "${KEYS[@]}"; do
    if printf '%s' "$BEFORE" | grep -q "\"$k\""; then
      PRESENT[$k]=yes; ok "$k presente antes → esta rodada REMOVE"
    else
      PRESENT[$k]=no;  ok "$k já ausente antes → esta rodada apenas CONFIRMA"
    fi
  done
fi

# ── 2. DELETE pela API oficial ──────────────────────────────────────────────
step "2. DELETE (global e tenant), pela API oficial"
for k in "${KEYS[@]}"; do
  for scope in "global" "tenant"; do
    if [ "$scope" = "global" ]; then q="/config/routing/$k"
    else                             q="/config/routing/$k?tenant_id=$TENANT"; fi
    ST="$(api DELETE "$q" | head -1 | tr -d '\r')"
    case "$ST:$scope:${PRESENT[$k]}" in
      200:*:*)        ok "DELETE $scope $k → 200" ;;
      404:tenant:*)   ok "DELETE tenant $k → 404 (sem override; esperado)" ;;
      404:global:no)  ok "DELETE global $k → 404 (já removido numa rodada anterior)" ;;
      404:global:yes) bad "DELETE global $k → 404, mas a chave APARECIA no GET —
                           valor resolvido de outra origem que o DELETE não alcança" ;;
      404:global:\?)  unknown "DELETE global $k → 404 sem baseline para interpretar" ;;
      0:*:*)          unknown "DELETE $scope $k não completou (config-api fora?)" ;;
      *)              bad "DELETE $scope $k → status inesperado '$ST'" ;;
    esac
  done
done

# ── 3. estado DEPOIS ────────────────────────────────────────────────────────
step "3. estado DEPOIS — a tela não pode mais oferecer as chaves"
AFTER="$(api GET "/config/routing?tenant_id=$TENANT" | tail -n +2)"
if [ -z "$AFTER" ]; then
  unknown "GET vazio — não dá para afirmar ausência"
else
  for k in "${KEYS[@]}"; do
    if printf '%s' "$AFTER" | grep -q "\"$k\""; then
      bad "$k AINDA presente depois do DELETE"
    else
      ok "$k ausente depois"
    fi
  done
  # O namespace tem de CONTINUAR existindo: se ele sumisse inteiro, o "ausente"
  # acima seria verdadeiro pelo motivo errado (endpoint quebrado, não chave
  # removida) — ausência-por-erro parecendo ausência-por-remoção.
  if printf '%s' "$AFTER" | grep -q '"sla_default_ms"'; then
    ok "namespace routing intacto (sla_default_ms ainda lá)"
  else
    bad "namespace routing sumiu — o 'ausente' acima não vale"
  fi
fi

# ── 4. suítes dos dois pacotes tocados ──────────────────────────────────────
step "4. suítes de config-api e routing-engine"
for svc in config-api routing-engine; do
  WD="$($DC exec -T "$svc" sh -lc '
        for d in "/app/packages/'"$svc"'" /app /app/src; do
          [ -f "$d/pyproject.toml" ] && { echo "$d"; exit 0; }
        done; exit 1' 2>/dev/null < /dev/null | tr -d '\r')"
  if [ -z "$WD" ]; then unknown "$svc: pyproject não encontrado"; continue; fi
  # `pip install pytest` ANTES de rodar — as imagens de serviço não o trazem, e
  # um `build` + `up -d --force-recreate` (que esta mudança exige) apaga
  # qualquer instalação anterior feita por `exec`. Na 1ª rodada os dois pacotes
  # saíram "No module named pytest" logo depois do rebuild. É o mesmo prólogo do
  # `report_suite_skips.sh`; faltava aqui.
  OUT="$(timeout 600 $DC exec -T "$svc" sh -lc \
        "pip install -q pytest pytest-asyncio >/dev/null 2>&1;
         cd '$WD' && python -m pytest -p no:cacheprovider -q 2>&1 | tail -3" < /dev/null 2>&1)"
  printf '%s\n' "$OUT" | sed 's/^/     /'
  if printf '%s' "$OUT" | grep -qE 'no tests ran'; then
    unknown "$svc: zero testes coletados — NÃO é 'zero falha'"
  elif printf '%s' "$OUT" | grep -qE '[0-9]+ (failed|error)'; then
    bad "$svc: suíte vermelha"
  elif printf '%s' "$OUT" | grep -qE '[0-9]+ passed'; then
    ok "$svc: suíte verde"
  else
    unknown "$svc: sem linha-resumo do pytest"
  fi
done

# ── veredicto ───────────────────────────────────────────────────────────────
echo
echo "── veredicto ─────────────────────────────────────────────────────────"
if [ "$FAIL" -gt 0 ];    then echo "   ❌ $FAIL falha(s)."; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "   ⚠️  $INCONCL inconclusivo(s) — não é verde."; exit 2; fi
echo "   ✅ chaves órfãs removidas do código E do banco; suítes verdes."
exit 0
