#!/usr/bin/env bash
# ==============================================================================
# probe_dashboard_card_title.sh — o titulo do cartao e DERIVADO, nao assado?
# ==============================================================================
#
# POR QUE ELE EXISTE (2026-08-28)
# -------------------------------
# A Home mostrou por meses um cartao chamado `catalog.volume-by-channel.label` —
# a chave i18n crua, gravada dentro do template salvo. O `AddCardModal` resolvia
# `t()` no momento da CRIACAO e assava o resultado; quando o namespace `dashboards`
# ainda nao tinha carregado, `t()` devolve a propria chave, e ela virava o titulo
# para sempre. A mesma assadura congela a LINGUA: cartao criado em PT continua em
# PT com a interface em EN.
#
# Titulo derivavel e RENDERING, nao fato. O fato ja esta gravado (`query.endpoint`).
# Este probe guarda as duas metades da regra:
#   · o que e derivavel resolve do catalogo, na lingua CORRENTE;
#   · o que o usuario digitou NAO e tocado (as testemunhas 5, 6 e 10).
#
# Sem a segunda metade, "re-traduz tudo" passaria — e apagaria titulo custom, que
# e um dado que so o usuario tem.
#
# COMO ELE MEDE
# -------------
# Compila o `catalog.ts` REAL (nao uma copia) dentro de um container Node da stack
# e roda as asserções contra as funcoes exportadas. Nao ha runner de teste no
# platform-ui; e por isso que o tsc emprestado de um container e o caminho, e nao
# um `vitest` que ninguem instalou.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# `CATALOG_SRC` existe para uma coisa so: conferir que este probe SABE reprovar,
# apontando-o para uma versao anterior do arquivo. Sem essa porta, "verde" aqui
# seria indistinguivel de "o probe nao mede nada".
SRC="${CATALOG_SRC:-$ROOT/packages/platform-ui/src/dashboard/catalog.ts}"

inc() { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; }

printf '\033[1mprobe: titulo de cartao de dashboard e derivado do catalogo?\033[0m\n'

[ -f "$SRC" ] || { inc "catalog.ts nao encontrado em $SRC"; exit 2; }

# Container Node da stack + um `tsc` qualquer dentro dele. Preferimos o
# skill-flow-service; se ele nao estiver de pe, qualquer outro serve.
CT=""
for c in $(docker ps --format '{{.Names}}' 2>/dev/null); do
  case "$c" in
    *skill-flow-service*|*mcp-server*|*agent-registry*) CT="$c"; break ;;
  esac
done
[ -n "$CT" ] || { inc "nenhum container Node da stack em execucao (o probe compila o TS dentro de um)"; exit 2; }

TSC="$(docker exec "$CT" sh -c 'find / -name tsc -type f -path "*typescript/bin*" 2>/dev/null | head -1')"
[ -n "$TSC" ] || { inc "sem typescript dentro de $CT — nao da para compilar o catalog.ts"; exit 2; }
printf '  container: %s\n  tsc: %s\n\n' "$CT" "$TSC"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp "$SRC" "$WORK/catalog.ts"

cat > "$WORK/check.ts" <<'TS'
import { resolveCardTitle, normalizeCardTitles, titleForNewCard } from './catalog'

// `t` de mentira, mas com as DUAS linguas de verdade: e o que separa "titulo
// derivavel" (re-traduz) de "titulo custom" (preserva).
const LABEL: Record<string, Record<string, string>> = {
  'catalog.volume-by-channel.label': { en: 'Volume by Channel', 'pt-BR': 'Volume por Canal' },
  'catalog.session-volume.label':    { en: 'Session Volume',    'pt-BR': 'Volume de Sessoes' },
}
function mkT(cur: string) {
  return (key: string, opts?: Record<string, unknown>) =>
    LABEL[key]?.[(opts?.lng as string) ?? cur] ?? key
}
const tEN = mkT('en'), tPT = mkT('pt-BR')
const card = (title: string, endpoint = '/reports/display/volume-by-channel') =>
  ({ title, query: { endpoint } })

let fail = 0

// Export ausente vira SENTENCA, nao stack trace. Sem esta guarda, um catalog.ts sem
// as funcoes derruba o script com TypeError -- vermelho certo, motivo ilegivel.
const EXPORTS: Record<string, unknown> = { resolveCardTitle, normalizeCardTitles, titleForNewCard }
for (const nome of Object.keys(EXPORTS)) {
  if (typeof EXPORTS[nome] !== 'function') {
    console.log('  [31mFALHA[0m  catalog.ts nao exporta ' + nome +
                ' -- o titulo volta a ser assado na criacao')
    console.log('')
    console.log('[31mVERMELHO[0m')
    ;(globalThis as { process?: { exit(c: number): void } }).process?.exit(1)
  }
}

function eq(got: unknown, want: unknown, what: string) {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  console.log(`  ${ok ? '[32mOK[0m  ' : '[31mFALHA[0m'}  ${what}`)
  if (!ok) { console.log(`         got=${JSON.stringify(got)} want=${JSON.stringify(want)}`); fail = 1 }
}

// ── a metade "deriva" ────────────────────────────────────────────────────────
eq(resolveCardTitle(card('catalog.volume-by-channel.label'), tEN), 'Volume by Channel',
   'chave i18n crua assada -> rotulo do catalogo (o defeito da Home)')
eq(resolveCardTitle(card(''), tEN), 'Volume by Channel', 'titulo vazio -> rotulo EN')
eq(resolveCardTitle(card(''), tPT), 'Volume por Canal',  'titulo vazio -> rotulo pt-BR')
eq(resolveCardTitle(card('Volume por Canal'), tEN), 'Volume by Channel',
   'rotulo assado em PT nao fica preso com a UI em EN')
eq(titleForNewCard(), '', 'cartao novo nasce SEM titulo (nada assado na criacao)')
eq(titleForNewCard('  Meu grafico '), 'Meu grafico', 'titulo digitado sobrevive a criacao')

// ── a metade "nao toca no que e do usuario" (testemunhas) ────────────────────
eq(resolveCardTitle(card('Meu grafico'), tEN), 'Meu grafico', 'titulo custom preservado (EN)')
eq(resolveCardTitle(card('Meu grafico'), tPT), 'Meu grafico', 'titulo custom preservado (pt-BR)')
eq(resolveCardTitle(card('Session Volume', '/reports/display/volume-by-channel'), tEN),
   'Session Volume', 'rotulo de OUTRO endpoint conta como custom, nao re-traduz')

// ── normalizacao do dado salvo (o que o "Save template" persiste) ────────────
eq(normalizeCardTitles(
     [card('catalog.volume-by-channel.label'), card('Volume por Canal'),
      card('Meu grafico'), card('')], tEN).map(c => c.title),
   ['', '', 'Meu grafico', ''],
   'normalizeCardTitles zera o derivavel e mantem o custom')

if (fail) { console.log('\n[31mVERMELHO[0m'); } else { console.log('\n[32mVERDE[0m — titulo derivado do catalogo; custom intocado'); }
// `process` sem @types/node: o cast evita o TS2580 sem instalar tipos.
;(globalThis as { process?: { exit(c: number): void } }).process?.exit(fail)
TS

docker exec "$CT" sh -c 'rm -rf /tmp/probe_card_title && mkdir -p /tmp/probe_card_title' >/dev/null 2>&1
docker cp "$WORK/catalog.ts" "$CT:/tmp/probe_card_title/catalog.ts" >/dev/null 2>&1
docker cp "$WORK/check.ts"   "$CT:/tmp/probe_card_title/check.ts"   >/dev/null 2>&1

OUT="$(docker exec "$CT" sh -c "cd /tmp/probe_card_title && node '$TSC' --target es2020 --module commonjs --skipLibCheck --moduleResolution node catalog.ts check.ts >/tmp/probe_card_title/tsc.log 2>&1; node check.js" 2>&1)"
RC=$?
docker exec "$CT" sh -c 'rm -rf /tmp/probe_card_title' >/dev/null 2>&1

if [ -z "$OUT" ]; then
  inc "a compilacao nao produziu saida — o catalog.ts nao compilou isolado"
  exit 2
fi
printf '%s\n' "$OUT"
exit "$RC"
