#!/usr/bin/env bash
# probe_context_map_audit.sh — GATE da fase V3 (arco ALLOWLIST,
# adr-contextstore-allowlist): mapa do ContextStore (D2) + aliases contados (D3)
# + modo AUDITORIA.
#
# O que a V3 entrega, e o que um gate de "o mapa existe" NAO julgaria:
#   a V3 entrega MEDICAO, nao fechamento. O modo auditoria existe para produzir a
#   lista real com que a V4 decide inverter para deny-by-default. Logo o teste que
#   importa nao e "o mapa carregou" — sao dois, e eles puxam para lados opostos:
#     (1) a auditoria CONTA o que deveria contar, e
#     (2) a auditoria NAO ESCONDE NADA.
#   Um gate so do (1) aprovaria uma V3 que ja comecou a esconder — que e a V4 sem
#   revisao e sem a lista que a autoriza.
#
# Nove ramos:
#   A. ORACULO   — verifyContextMap sobre o mapa da TS: 4 listas vazias E declared>0
#   B. PARIDADE  — o masking.context_map VIVO == DEFAULT_CONTEXT_MAP da TS
#   B2.VIVOxVIVO — todo `tipo` do mapa VIVO existe no catalogo VIVO
#   C. PAR       — contadores alias>0 E canonica>0 (ADR secao 7)
#   D. NEGATIVA  — tag canonica nao incrementa alias, e alias nao incrementa canonica
#   E. INOCUIDADE— a saida mascarada e IDENTICA com e sem o mapa (a V3 nao esconde)
#   F. BALDES    — tag fora do mapa cai em unknown; tag dinamica NAO cai em unknown
#   G. DEPLOY    — o portao recusa masked:"texto" (tipo inerte) e ACEITA um que mascara
#   H. NAMESPACE — a chave de auditoria nao mora sob {t}:ctx: (endereco das sessoes)
#
# Ramo A traz `declared` junto de propósito: quatro listas vazias sobre um mapa
# VAZIO nao e aprovacao, e a §7 do ADR ja registra a mesma armadilha para a V4
# ("zero sobre zero e um servico parado").
#
# Ramo D e a testemunha NEGATIVA exigida pela §7: so o contador de alias nao
# distingue "ninguem migrou" de "ninguem usa". E o par so vale se cada lado contar
# apenas o seu — um contador que incrementa os dois passaria no ramo C sem medir
# nada.
#
# Ramo E e o invariante central da fase, e e o unico ramo cuja falha significa que
# a V3 virou V4 por acidente.
#
# Ramo G existe porque a V3 acrescentou ao catalogo o primeiro tipo que NAO
# mascara (`texto`, para o mapa). Sem apertar o portao de deploy, `masked:"texto"`
# passaria: campo declarado mascarado, exibido em claro. A testemunha POSITIVA
# (um tipo que mascara segue aceito) esta junto porque um portao que recusa tudo
# tambem "passa" no negativo.
#
# Tres estados: OK / FALHA / INCONCLUSIVO (nunca OK com ramo inconclusivo).
set -u

cd "$(dirname "$0")/../.." || exit 2
DC="${DC:-docker compose -f docker-compose.demo.yml}"
CFG="${CFG:-http://localhost:3600}"
TENANT="${TENANT:-tenant_demo}"
# LIVE_JSON mora DENTRO do repo, e nao em /tmp, porque o node desta bancada resolve
# "/tmp/x" contra a raiz do cwd — com cwd em UNC isso vira \<host>\<distro>\tmp\x,
# que nao existe. Caminho absoluto derivado do proprio script serve as duas bancadas.
LIVE_JSON="$(cd "$(dirname "$0")" && pwd)/.ctxmap_live.json"; export LIVE_JSON
SCHEMAS="packages/schemas"

fail=0
inconclusive=0
say()  { echo "  $*"; }
bad()  { echo "  x $*"; fail=$((fail+1)); }
ok()   { echo "  v $*"; }
huh()  { echo "  ? $*"; inconclusive=$((inconclusive+1)); }

# Node vive no nvm nesta maquina; o PATH do shell nao o traz.
NODE_BIN="${NODE_BIN:-$HOME/.nvm/versions/node/v24.14.1/bin}"
[ -d "$NODE_BIN" ] && PATH="$NODE_BIN:$PATH"
export PATH

echo "=== probe_context_map_audit — V3 do arco ALLOWLIST ==="

if ! command -v node >/dev/null 2>&1; then
  huh "node ausente — os ramos A/B/D/E/F nao podem rodar (defina NODE_BIN)"
fi

# Compila os schemas uma vez; os ramos leem de dist/.
#
# ⚠️ `npx` NAO serve aqui: nesta bancada o cwd e um caminho UNC e o npm morre com
# ERR_INVALID_URL antes de chamar o tsc — o ramo saia INCONCLUSIVO por motivo de
# BANCADA, nunca de codigo, e o `dist/` ficava STALE sem ninguem notar. Stale e pior
# que ausente: os ramos A e B passariam a julgar a versao ANTERIOR do mapa e diriam
# "identica" sobre um arquivo que nao e o do repositorio — a familia
# "existe != e o de agora". Invoca-se o binario local direto; npx e ultimo recurso.
TSC_LOG="$(cd "$(dirname "$0")" && pwd)/.ctxmap_tsc.log"
trap 'rm -f "$LIVE_JSON" "$TSC_LOG"' EXIT
if command -v node >/dev/null 2>&1; then
  # ⚠️ relativo ao SCHEMAS, resolvido DEPOIS do cd. Montá-lo antes (com $SCHEMAS,
  # que e relativo ao repo) faz o caminho virar packages/schemas/packages/schemas/...
  # e o ramo cai no ELSE — que era o npx quebrado. Errar isto devolve o mesmo
  # INCONCLUSIVO com outra causa, que e como um conserto parece nao ter efeito.
  if [ -f "$SCHEMAS/node_modules/typescript/bin/tsc" ]; then
    ( cd "$SCHEMAS" && node ./node_modules/typescript/bin/tsc >"$TSC_LOG" 2>&1 )       || { huh "tsc dos schemas reprovou — ver $TSC_LOG"; }
  else
    ( cd "$SCHEMAS" && npx tsc >"$TSC_LOG" 2>&1 )       || { huh "tsc dos schemas reprovou (via npx) — ver $TSC_LOG"; }
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- A. ORACULO (verifyContextMap sobre o mapa da TS) --"
if [ -f "$SCHEMAS/dist/context-map.js" ]; then
  A=$(cd "$SCHEMAS" && node -e '
    const m = require("./dist/context-map.js")
    const v = m.verifyContextMap()
    const bad = ["unknown_types","mismatched_retention","ambiguous_aliases","alias_shadows_canonical"]
      .filter(k => (v[k] || []).length > 0)
    console.log(JSON.stringify({ declared: v.declared, aliases: v.aliases, bad,
      detail: Object.fromEntries(bad.map(k => [k, v[k]])) }))
  ' 2>/dev/null)
  if [ -z "$A" ]; then
    huh "oraculo nao executou"
  else
    A_DECL=$(echo "$A" | sed -E 's/.*"declared":([0-9]+).*/\1/')
    A_ALIAS=$(echo "$A" | sed -E 's/.*"aliases":([0-9]+).*/\1/')
    if echo "$A" | grep -q '"bad":\[\]'; then
      if [ "${A_DECL:-0}" -gt 0 ] && [ "${A_ALIAS:-0}" -gt 0 ]; then
        ok "mapa integro — $A_DECL campos declarados, $A_ALIAS aliases"
      else
        bad "mapa VAZIO (declared=$A_DECL aliases=$A_ALIAS) — listas vazias sobre vazio nao e aprovacao"
      fi
    else
      bad "oraculo REPROVOU: $A"
    fi
  fi
else
  huh "dist/context-map.js ausente"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- B. PARIDADE (config-api vivo == DEFAULT_CONTEXT_MAP da TS) --"
LIVE=$(curl -sS --max-time 10 -H "Accept: application/json" \
  "$CFG/config/masking?tenant_id=$TENANT" 2>/dev/null)
if [ -z "$LIVE" ]; then
  huh "config-api inalcancavel em $CFG"
elif ! echo "$LIVE" | grep -q '"context_map"'; then
  bad "masking.context_map AUSENTE na config viva (o seed nao aplicou)"
else
  echo "$LIVE" > "$LIVE_JSON"
  B=$(cd "$SCHEMAS" && node -e '
    const fs = require("fs")
    const { DEFAULT_CONTEXT_MAP } = require("./dist/context-map.js")
    const body    = JSON.parse(fs.readFileSync(process.env.LIVE_JSON, "utf8"))
    const entries = body.entries || body
    const raw     = entries.context_map
    const live    = (raw && typeof raw === "object" && "value" in raw) ? raw.value : raw
    // Comparacao canonica: ordem de chave nao e contrato, CONTEUDO e.
    const norm = o => JSON.stringify(o, Object.keys(JSON.parse(JSON.stringify(o))).length ? undefined : undefined)
    const sortDeep = o => Array.isArray(o) ? o.map(sortDeep)
      : (o && typeof o === "object")
        ? Object.fromEntries(Object.keys(o).sort().map(k => [k, sortDeep(o[k])]))
        : o
    const same = JSON.stringify(sortDeep(live)) === JSON.stringify(sortDeep(DEFAULT_CONTEXT_MAP))
    const nLive = live && live.contexto
      ? Object.values(live.contexto).reduce((a,d)=>a+Object.values(d).reduce((b,c)=>b+Object.keys(c).length,0),0) : -1
    console.log(JSON.stringify({ same, nLive }))
  ' 2>/dev/null)
  if echo "$B" | grep -q '"same":true'; then
    ok "config viva identica a TS ($(echo "$B" | sed -E 's/.*"nLive":([0-9-]+).*/\1/') campos)"
  elif [ -z "$B" ]; then
    # Sem saida do comparador nao ha o que comparar. Antes isto caia no `bad` e
    # publicava "DIVERGENCIA ... : " com o payload VAZIO — veredicto sobre uma medicao
    # que nao aconteceu, a familia que a regra transversal do ADR proibe. Medido em
    # 2026-09-01: node com cwd em UNC resolve "/tmp/x" fora do /tmp do shell, o
    # readFileSync estourava e o 2>/dev/null engolia. A diferenca importa — um diz
    # "conserte o mirror", o outro diz "conserte a bancada".
    huh "comparador vivo x TS nao executou (dist/ ausente? cwd UNC?) — sem veredicto"
  else
    bad "DIVERGENCIA config viva x TS: $B — o mirror do seed.py saiu de sincronia"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- B2. VIVO x VIVO (o mapa vivo so cita tipo que o catalogo vivo tem) --"
# Este ramo existe porque o A julga TS contra TS, e o que roda e outro par: o mapa
# VIVO contra o catalogo VIVO. Foi essa diferenca que escondeu, na propria sessao
# da V3, um `texto` presente na TS e ausente da config — os dois mirrors do seed.py
# sao hand-written e sao seed-if-absent, entao acrescentar um tipo na TS NAO chega
# ao store. O sintoma foi o portao recusar `masked:"texto"` pelo motivo ERRADO
# ("nao existe") em vez do certo ("nao mascara"): veredicto correto, proposicao
# vizinha.
if [ -n "${LIVE:-}" ] && echo "$LIVE" | grep -q '"context_map"'; then
  echo "$LIVE" > "$LIVE_JSON"
  B2=$(cd "$SCHEMAS" && node -e '
    const fs = require("fs")
    const body    = JSON.parse(fs.readFileSync(process.env.LIVE_JSON, "utf8"))
    const entries = body.entries || body
    const unwrap  = k => { const r = entries[k]; return (r && typeof r === "object" && "value" in r) ? r.value : r }
    const map = unwrap("context_map"), cat = unwrap("types")
    if (!map || !cat) { console.log(JSON.stringify({ err: "map ou types ausente na config viva" })); process.exit(0) }
    const ids  = new Set((cat.types || []).map(t => t.id))
    const falt = []
    for (const [e, ds] of Object.entries(map.contexto || {}))
      for (const [d, cs] of Object.entries(ds))
        for (const [c, leaf] of Object.entries(cs))
          if (!ids.has(leaf.tipo)) falt.push(`${e}.${d}.${c} -> ${leaf.tipo}`)
    console.log(JSON.stringify({ tiposVivos: ids.size, faltando: falt.slice(0, 8), nFalt: falt.length }))
  ' 2>/dev/null)
  if [ -z "$B2" ]; then
    huh "B2: comparacao vivo x vivo nao executou"
  elif echo "$B2" | grep -q '"err"'; then
    bad "B2: $B2"
  elif echo "$B2" | grep -q '"nFalt":0'; then
    B2_N=$(echo "$B2" | grep -oE '"tiposVivos":[0-9]+' | grep -oE '[0-9]+')
    ok "B2: todo tipo citado pelo mapa vivo existe no catalogo vivo (${B2_N:-?} tipos)"
  else
    bad "B2: o mapa vivo cita tipo que o catalogo vivo NAO tem — $B2"
  fi
else
  huh "B2: sem config viva para comparar"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- C/D. PAR DE CONTADORES + testemunha NEGATIVA --"
if [ -f "$SCHEMAS/dist/context-map.js" ]; then
  CD=$(cd "$SCHEMAS" && node -e '
    const { DEFAULT_CONTEXT_MAP, buildContextTagIndex, resolveContextTag } = require("./dist/context-map.js")
    const ix = buildContextTagIndex(DEFAULT_CONTEXT_MAP)
    const cls = tags => {
      const o = { alias: 0, canonical: 0, dynamic: 0, unknown: 0 }
      for (const t of tags) o[resolveContextTag(t, ix).origin]++
      return o
    }
    // C — o par: uma grafia legada E uma canonica viva (escrita pelo routing-engine)
    const par = cls(["caller.cpf", "core.pool.id"])
    // D — testemunha negativa, nos DOIS sentidos
    const soCanon = cls(["core.pool.id", "core.queue.position"])
    const soAlias = cls(["caller.cpf", "caller.nome"])
    console.log(JSON.stringify({ par, soCanon, soAlias }))
  ' 2>/dev/null)
  if [ -z "$CD" ]; then
    huh "classificador nao executou"
  else
    echo "$CD" | grep -q '"par":{"alias":1,"canonical":1' \
      && ok "C: par presente — alias=1 e canonica=1 na mesma leitura" \
      || bad "C: par ausente — $CD"
    echo "$CD" | grep -q '"soCanon":{"alias":0' \
      && ok "D: tag canonica NAO incrementa alias" \
      || bad "D: tag canonica contou como alias — $CD"
    echo "$CD" | grep -q '"soAlias":{"alias":2,"canonical":0' \
      && ok "D: tag legada NAO incrementa canonica" \
      || bad "D: tag legada contou como canonica — $CD"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- E. INOCUIDADE (a V3 nao esconde nada) --"
# O invariante e estrutural e da para conferi-lo na FONTE: o modo auditoria so
# pode ser fire-and-forget. Se `observeContextTags` for aguardado ou o seu
# resultado alimentar qualquer decisao das duas portas, a fase deixou de ser
# auditoria. Duas asercoes: a chamada existe nas DUAS portas, e em nenhuma delas
# o retorno e consumido.
E_SRC_A="packages/mcp-server-plughub/src/server.ts"
E_SRC_B="packages/mcp-server-plughub/src/lib/context-masking.ts"
e_ok=1
for f in "$E_SRC_A" "$E_SRC_B"; do
  if ! grep -q "observeContextTags(rawHash, tenantId)" "$f"; then
    bad "E: porta NAO instrumentada: $f"; e_ok=0
  fi
  # Consumir o retorno (const x = await observe...) tornaria a classificacao capaz
  # de influenciar a saida — que e exatamente o que a V3 nao pode fazer.
  if grep -nE "(=|return)[^\n]*observeContextTags" "$f" | grep -v "void observeContextTags" | grep -q .; then
    bad "E: o retorno de observeContextTags e CONSUMIDO em $f — a auditoria deixou de ser inocua"; e_ok=0
  fi
done
# `mode` com um valor so: nao pode haver config que ligue imposicao nesta fase.
if grep -q 'z.enum(\["audit"\])' "$SCHEMAS/src/context-map.ts"; then
  ok "E: mode tem UM valor (audit) — nenhuma config liga imposicao"
else
  bad "E: o enum de mode aceita mais de um valor — a V4 ficou ao alcance da config"; e_ok=0
fi
[ "$e_ok" = "1" ] && ok "E: as duas portas instrumentadas, retorno nao consumido"

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- F. BALDES (unknown x dinamica) --"
if [ -f "$SCHEMAS/dist/context-map.js" ]; then
  F=$(cd "$SCHEMAS" && node -e '
    const { DEFAULT_CONTEXT_MAP, buildContextTagIndex, resolveContextTag } = require("./dist/context-map.js")
    const ix = buildContextTagIndex(DEFAULT_CONTEXT_MAP)
    const o = t => resolveContextTag(t, ix).origin
    console.log(JSON.stringify({
      naoDeclarada: o("session.campo_que_ninguem_declarou"),
      lacunaFechada: o("session.vencimento_cartao"),
      agente:       o("agent.part_123.foo"),
      segmento:     o("segment.seg_9.bar"),
    }))
  ' 2>/dev/null)
  if [ -z "$F" ]; then
    huh "classificador nao executou"
  else
    echo "$F" | grep -q '"naoDeclarada":"unknown"' \
      && ok "F: tag fora do mapa e ACUSADA (unknown)" \
      || bad "F: tag fora do mapa nao foi acusada — $F"
    # 2026-08-30 (D8.3) — a assercao INVERTEU, e a inversao E a entrega.
    #
    # Ate aqui session.vencimento_cartao era a lacuna DELIBERADA da V3: campo com
    # politica viva (last_2) cujo tipo nao existia no catalogo. O ramo exigia que
    # caisse em unknown, isto e, que a auditoria a ACUSASSE. O catalogo ganhou
    # card_expiry e o campo entrou no mapa como session.cartao.vencimento, entao a
    # grafia antiga passou a resolver como ALIAS.
    #
    # A assercao NAO foi removida, porque a proposicao a proteger mudou e nao sumiu:
    # era "a lacuna e visivel", hoje e "a lacuna esta FECHADA e nao volta em
    # silencio". Um ramo apagado deixaria o campo poder virar nao-declarado outra vez
    # sem nada ficar vermelho. O caso SINTETICO (naoDeclarada) segue cobrindo "o
    # balde unknown funciona"; este cobre um campo REAL e medido, que e o que uma
    # regressao de verdade atingiria.
    echo "$F" | grep -q '"lacunaFechada":"alias"' \
      && ok "F: session.vencimento_cartao resolve como ALIAS — lacuna do catalogo FECHADA" \
      || bad "F: esperado alias (lacuna fechada), veio — $F"
    if echo "$F" | grep -q '"agente":"dynamic"' && echo "$F" | grep -q '"segmento":"dynamic"'; then
      ok "F: agent.*/segment.* no balde DINAMICO — nao inflam a lista da V4"
    else
      bad "F: prefixo dinamico caiu em unknown — inflaria o numero que autoriza a V4 — $F"
    fi
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- G. PORTAO DE DEPLOY (tipo inerte recusado, tipo que mascara aceito) --"
if [ -f "$SCHEMAS/dist/audit.js" ]; then
  G=$(cd "$SCHEMAS" && node -e '
    const { DEFAULT_DATA_TYPE_CATALOG, typeMasksSomething } = require("./dist/audit.js")
    const t  = id => DEFAULT_DATA_TYPE_CATALOG.types.find(x => x.id === id)
    const inertes    = DEFAULT_DATA_TYPE_CATALOG.types.filter(x => !typeMasksSomething(x)).map(x => x.id)
    console.log(JSON.stringify({
      textoMascara: t("texto") ? typeMasksSomething(t("texto")) : null,
      cpfMascara:   t("cpf")   ? typeMasksSomething(t("cpf"))   : null,
      inertes,
    }))
  ' 2>/dev/null)
  echo "$G" | grep -q '"textoMascara":false' \
    && ok "G: texto e INERTE (nao mascara) — o portao tem o que recusar" \
    || bad "G: texto passou por mascarador — $G"
  echo "$G" | grep -q '"cpfMascara":true' \
    && ok "G: cpf segue mascarando — testemunha POSITIVA, o portao nao recusa tudo" \
    || bad "G: um tipo que mascara foi classificado como inerte — $G"
fi
# O validador precisa MESMO consultar o predicado, nao so importa-lo.
VAL="packages/agent-registry/src/validators/skill.ts"
if grep -q "typeMasksSomething" "$VAL" && grep -q "inert.has(r.type)" "$VAL"; then
  ok "G: validateMaskedTypeRefs recusa tipo inerte por PREDICADO (nao por lista)"
else
  bad "G: o portao de deploy nao consulta typeMasksSomething — masked:\"texto\" passaria"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- H. NAMESPACE (a auditoria nao mora no endereco das SESSOES) --"
# `{t}:ctx:{sessionId}` e o hash de contexto de uma sessao. Chave de auditoria sob
# `{t}:ctx:audit:*` colide com esse namespace e todo scanner de `*:ctx:*` passa a
# devolver "audit:counts" como se fosse id de sessao — medido na propria sessao da
# V3: os probes da V1 e da V1b listaram as duas chaves no meio dos UUIDs.
# `journey:`/`customer:` moram sob `:ctx:` legitimamente (sao ESCOPOS do contexto);
# auditoria e metadado SOBRE o contexto e nao herda o endereco.
LIBMAP="packages/mcp-server-plughub/src/lib/context-map.ts"
if grep -qE '\$\{t\}:ctx:audit' "$LIBMAP"; then
  bad "H: chave de auditoria sob {t}:ctx:audit — colide com o namespace das sessoes"
elif grep -qE '\$\{t\}:ctx_audit:' "$LIBMAP"; then
  ok "H: auditoria em {t}:ctx_audit:* — fora do namespace das sessoes"
else
  huh "H: nao foi possivel localizar a definicao das chaves de auditoria"
fi
# Testemunha VIVA: nenhuma chave de auditoria pode aparecer sob *:ctx:*
CTXA=$(docker exec plughub-demo-redis-1 redis-cli --scan --pattern "*:ctx:audit*" 2>/dev/null | wc -l | tr -d ' ')
if [ "${CTXA:-0}" = "0" ]; then
  ok "H: Redis vivo sem residuo de {t}:ctx:audit*"
else
  bad "H: ${CTXA} chave(s) de auditoria ainda sob *:ctx:* no Redis vivo"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
if [ "$inconclusive" -gt 0 ]; then
  echo "INCONCLUSIVO ($inconclusive ramo(s) sem poder julgar, $fail falha(s))"
  exit 2
elif [ "$fail" -gt 0 ]; then
  echo "FALHA ($fail)"
  exit 1
else
  echo "OK — mapa integro, par de contadores com testemunha negativa, auditoria inocua"
  exit 0
fi
