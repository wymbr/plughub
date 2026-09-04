#!/usr/bin/env bash
# probe_ctx_read_audience.sh — CTX-01 (censo) + CTX-02 (auditoria de runtime).
#
# As duas metades respondem perguntas diferentes e falham diferente:
#
#   censo      — o que os YAMLs do disco DIRIAM. Sabe em que step cada
#                interpolação está, que é o que decide a plateia.
#   auditoria  — o que o runtime REALMENTE vê. O bridge executa o snapshot do
#                slot, não o YAML; as duas divergem por construção, e foi essa
#                divergência que deixou o wrap-up gravando nada por três dias.
#
# ⚠️ O ramo que mais importa é o **D**: ele separa *"a auditoria não achou nada"*
# de *"a auditoria não rodou"*. Sem ele, uma env faltando produz silêncio
# idêntico ao de um parque limpo — e silêncio é o desfecho que este arco inteiro
# existe para não confundir com conformidade.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

RAIZ="$PWD"
CONFIG="${CONFIG_API_URL:-http://localhost:3600}"
TENANT="${PLUGHUB_TENANT_ID:-tenant_demo}"
ENG_CT="${ENG_CT:-plughub-demo-skill-flow-service-1}"
FALHAS=0
INCONCLUSIVOS=0

ok()  { echo "  OK           $*"; }
bad() { echo "  REPROVA      $*"; FALHAS=$((FALHAS+1)); }
inc() { echo "  INCONCLUSIVO $*"; INCONCLUSIVOS=$((INCONCLUSIVOS+1)); }

echo "== probe_ctx_read_audience =="
echo

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# ── insumos do censo: os catálogos VIVOS, não os embutidos ───────────────────
curl -s -m 10 -H "X-Tenant-ID: $TENANT" "$CONFIG/config/masking/context_map?tenant_id=$TENANT" > "$TMP/cm.json"
curl -s -m 10 -H "X-Tenant-ID: $TENANT" "$CONFIG/config/masking/types?tenant_id=$TENANT"       > "$TMP/tipos.json"
if ! jq -e '.value' "$TMP/cm.json" >/dev/null 2>&1 || ! jq -e '.value.types' "$TMP/tipos.json" >/dev/null 2>&1; then
  inc "config-api não serviu os catálogos — o censo NÃO foi refeito"
  echo; echo "======================"; echo "INCONCLUSIVO (1) — não é verde"; exit 2
fi

CENSO=$(docker run --rm -v "$RAIZ:/w" -v "$TMP:/t" -w /w/packages/schemas node:20-alpine \
          node_modules/.bin/vite-node /w/infra/test/q_ctx_read_audience_census.ts 2>/dev/null)

if ! echo "$CENSO" | jq -e '.refs' >/dev/null 2>&1; then
  inc "A. o censo não produziu saída utilizável"
else
  REFS=$(echo "$CENSO" | jq -r '.refs')
  CLI=$(echo  "$CENSO" | jq -r '.por_plateia.customer')
  SIS=$(echo  "$CENSO" | jq -r '.por_plateia.system')
  MUD=$(echo  "$CENSO" | jq -r '.mudaria')
  OMI=$(echo  "$CENSO" | jq -r '.omitiria')
  ORF=$(echo  "$CENSO" | jq -r '.legitimos_orfaos | length')
  NAO=$(echo  "$CENSO" | jq -r '.tags_nao_declaradas | length')

  # Testemunha de presença: um censo que varre zero arquivos acha zero problemas.
  if [ "${REFS:-0}" -lt 30 ]; then
    bad "A. só $REFS interpolações varridas — o censo não está lendo os skills"
  else
    ok "A. $REFS interpolações varridas (cliente=$CLI sistema=$SIS)"
  fi

  # A classificação é o produto, não a contagem. Um censo que só somasse os
  # sensíveis mediria "quantos o filtro pegaria" — proposição adjacente à que
  # importa, que é "quantos DEVERIAM ser pegos".
  if [ "${CLI:-0}" -ge 1 ] && [ "${SIS:-0}" -ge 1 ]; then
    ok "B. classifica por plateia — há sítios de cliente E de sistema na amostra"
  else
    bad "B. a amostra não tem as duas plateias (cliente=$CLI sistema=$SIS); a classificação não foi exercida"
  fi

  echo "  ....         C. mudariam=$MUD (omitiriam=$OMI) · legítimos órfãos=$ORF · tags não declaradas=$NAO"
  if [ "${ORF:-1}" -eq 0 ]; then
    ok "C. nenhuma exceção declarada está órfã (toda linha da tabela ainda se aplica)"
  else
    bad "C. $ORF exceção(ões) declarada(s) que não se aplicam mais — a tabela tem de encolher"
    echo "$CENSO" | jq -r '.legitimos_orfaos[]' | head -6 | sed 's/^/       /'
  fi
  if [ "${MUD:-0}" -ge 1 ]; then
    echo "$CENSO" | jq -r '.a_declarar[] | "       a declarar: \(.skill):\(.step) \(.tag) [\(.tipo)] → \(.mascara)"'
  fi

# ── G — F4/§D5: a plateia `model` nao tem politica, e a populacao e ZERO ─────
# Um prompt SAI da plataforma. Enquanto a §D5 nao decidir o que pode ir num, o que
# protege nao e uma regra — e o fato de NINGUEM estar mandando PII para la.
#
# ⚠️ Este ramo existe porque `mudaria` NAO cobre a plateia `model`: `maskForSite`
# devolve `undecided` para ela, entao esses pontos nunca aparecem naquela conta e
# um censo que so a olhasse concluiria que nao ha risco num prompt. Proposicao
# adjacente, veredicto inutil — a familia que o CLAUDE.md nomeia.
#
# Zero aqui e o que torna legitimo NAO decidir a D5 agora: decidir contra
# populacao zero e o erro que este repositorio ja registrou. Deixar de ser zero e
# o gatilho da fase, e o gate e quem avisa.
  # ── F5 — publicado, NAO julgado ────────────────────────────────────────────
  # Nao ha politica para `$.pipeline_state.*`: o mapa tipa tag de ContextStore, nao
  # chave de pipeline_state, entao toda chave aqui e indeclaravel hoje. Um ramo que
  # reprovasse exigiria o que a F5 ainda nao construiu; um que aprovasse afirmaria
  # seguranca que ninguem verificou. Publica-se o numero para quem desenhar o
  # carimbo de proveniencia — e para que a diferenca regex x estrutural fique a vista.
  PS_T=$(echo "$CENSO" | jq -r '.pipeline_state.total // 0')
  PS_C=$(echo "$CENSO" | jq -r '.pipeline_state.por_plateia.customer // 0')
  PS_K=$(echo "$CENSO" | jq -r '.pipeline_state.chaves_ao_cliente | length')
  echo "  ....         F5 (sem veredicto). pipeline_state: $PS_T em campo que sai · $PS_C ao cliente · $PS_K chaves distintas"

  MOD=$(echo  "$CENSO" | jq -r '.ao_modelo // 0')
  SENS=$(echo "$CENSO" | jq -r '.modelo_com_tipo_que_mascara | length')
  if [ "${SENS:-0}" -eq 0 ]; then
    ok "G. §D5 sem populacao: $MOD interpolacoes ao MODELO, nenhuma de tipo que mascara"
  else
    bad "G. $SENS interpolacao(oes) mandam tipo que MASCARA para um prompt — a §D5 deixou de ser hipotetica"
    echo "$CENSO" | jq -r '.modelo_com_tipo_que_mascara[] | "       \(.skill):\(.step) \(.tag) [\(.tipo)]"'
  fi
fi

# ── D — a auditoria de runtime está CONFIGURADA ──────────────────────────────
# Pergunta ao COMPOSE (o que um `up -d` reproduz) e ao CONTAINER (o que roda
# agora). Só o segundo passaria com uma env posta à mão, que um `up -d` apaga.
# ⚠️ Lê o bloco do SERVIÇO, não o arquivo inteiro. A primeira versão fazia
# `grep -c` no compose todo — e o `mcp-server-plughub` declara a MESMA env, então
# a contagem nunca chegava a zero e a mutação que removia a linha do engine
# passava despercebida. O ramo media *"alguém declara"* quando a pergunta era
# *"o engine declara"*: proposição adjacente, veredicto inútil.
DECL=$(python3 - <<'PY' 2>/dev/null
import io, re
t = io.open("docker-compose.demo.yml", encoding="utf-8").read()
i = t.find("\n  skill-flow-service:")
if i < 0:
    print(0); raise SystemExit
j = re.search(r"\n  [a-z0-9_-]+:\n", t[i + 3:])
bloco = t[i:i + 3 + (j.start() if j else len(t))]
print(1 if "CONFIG_API_URL:" in bloco else 0)
PY
)
VIVO=$(docker exec "$ENG_CT" sh -lc 'echo -n "$CONFIG_API_URL"' 2>/dev/null)
if [ -z "$VIVO" ]; then
  bad "D. o engine roda SEM CONFIG_API_URL — a auditoria de plateia não roda, e o log vazio dela NÃO é evidência de nada"
elif [ "${DECL:-0}" -lt 1 ]; then
  bad "D. o container tem a env mas o compose NÃO a declara — um \`up -d\` a apaga"
else
  # O alcance é medido com o MESMO cliente que o engine usa (o `fetch` do Node),
  # e não com `wget`. Não é preciosismo: a primeira versão deste ramo usou wget e
  # deu VERMELHO FALSO — a env estava certa, o serviço respondia, e o que falhou
  # foi a ferramenta do probe. Um gate que reprova por si mesmo ensina a mesma
  # desconfiança que um gate que aprova por si mesmo.
  ALC=$(docker exec "$ENG_CT" node -e '
    const u = process.env.CONFIG_API_URL + "/config/masking/types?tenant_id=" + process.argv[1]
    fetch(u).then(r => r.text()).then(t => console.log(t.slice(0, 60)))
            .catch(e => console.log("ERRO " + String(e)))
  ' "$TENANT" 2>/dev/null)
  case "$ALC" in
    *types*|*namespace*) ok "D. auditoria configurada: env no compose, no container, e o config-api responde ao fetch DO ENGINE" ;;
    *) bad "D. CONFIG_API_URL=$VIVO setada, mas o config-api não responde de dentro do engine: $(echo "$ALC" | head -c 90)" ;;
  esac
fi

# ── E — a lógica compartilhada e o runtime, verdes ───────────────────────────
V1=$(docker run --rm -v "$RAIZ:/w" -w /w/packages/schemas node:20-alpine \
       npx vitest run src/ctx-audience.test.ts 2>&1 | grep -oE 'Tests +[0-9]+ passed' | head -1)
V2=$(docker run --rm -v "$RAIZ:/w" -w /w/packages/skill-flow-engine node:20-alpine \
       npx vitest run src/__tests__/ctx-audit.test.ts 2>&1 | grep -oE 'Tests +[0-9]+ passed' | head -1)
if [ -n "$V1" ] && [ -n "$V2" ]; then
  ok "E. derivação ($V1) e runtime ($V2)"
else
  bad "E. suíte da derivação ou do runtime reprovou (derivação='$V1' runtime='$V2')"
fi

# ── F — o filtro da F3 esta DEPLOYADO? ───────────────────────────────────────
# `tsc` verde e suite verde provam o CODIGO. Nenhum dos dois prova que o processo
# que atende contato tem o filtro: o engine roda o `dist/` da IMAGEM, e um `up -d`
# recria o container a partir dela. E a separacao declaracao x imagem x execucao
# que o CLAUDE.md manda fazer — e aqui ela nao e teorica: no dia em que a F3 foi
# escrita, o container ainda servia a versao SO-AUDITORIA.
#
# ⚠️ Nao implantado NAO e falha de codigo — e fato de deploy. Por isso sai
# INCONCLUSIVO para a metade viva, nunca REPROVA: um gate que fica vermelho por
# faltar um build ensina todo mundo a ignora-lo.
DIST=/app/packages/skill-flow-engine/dist
TEM_FILTRO=$(docker exec "$ENG_CT" sh -lc "grep -rl filtrarLeituraCtx $DIST 2>/dev/null | head -1" 2>/dev/null)
TEM_VELHO=$(docker exec "$ENG_CT" sh -lc "grep -rl auditarLeituraCtx $DIST 2>/dev/null | head -1" 2>/dev/null)

if [ -n "$TEM_FILTRO" ]; then
  # Implantado: exerce contra o CATALOGO VIVO, com controle positivo. Um filtro que
  # mascara tudo passa em qualquer prova que so verifique mascaramento.
  OUT=$(docker exec "$ENG_CT" node -e '
    const { filtrarLeituraCtx } = require("'"$DIST"'/ctx-audit.js")
    const cli = { stepType: "notify", visibility: "all", stepId: "probe" }
    const sis = { stepType: "invoke", stepId: "probe" }
    const t = process.argv[1]
    Promise.all([
      filtrarLeituraCtx("1111222233334444", "session.numero_cartao", cli, t),
      filtrarLeituraCtx("1111222233334444", "session.numero_cartao", sis, t),
    ]).then(([c, s]) => console.log(JSON.stringify({ cliente: c, sistema: s })))
     .catch(e => console.log(JSON.stringify({ erro: String(e) })))
  ' "$TENANT" 2>/dev/null | grep -o '{.*}' | tail -1)
  # O proprio filtro LOGA em stdout ao aplicar, e a primeira versao deste ramo
  # colou o log ao JSON e saiu INCONCLUSIVO com a prova na tela. Pega-se a linha
  # do objeto, nao a saida inteira.
  CLI_V=$(echo "$OUT" | jq -r '.cliente // empty' 2>/dev/null)
  SIS_V=$(echo "$OUT" | jq -r '.sistema // empty' 2>/dev/null)
  if [ "$CLI_V" = "***4444" ] && [ "$SIS_V" = "1111222233334444" ]; then
    ok "F. F3 VIVA no engine: cliente=$CLI_V · sistema=$SIS_V (controle positivo)"
  elif [ -z "$CLI_V" ]; then
    inc "F. o filtro esta na imagem mas nao foi exercitavel — $(echo "$OUT" | head -c 120)"
  else
    bad "F. o filtro esta VIVO e respondeu errado: cliente=$CLI_V sistema=$SIS_V"
  fi
elif [ -n "$TEM_VELHO" ]; then
  inc "F. o engine roda a versao SO-AUDITORIA (achou auditarLeituraCtx, nao filtrarLeituraCtx) — a F3 esta no codigo e NAO na imagem; falta rebuild"
else
  inc "F. nao encontrei nenhuma das duas versoes em $DIST — o container serve outra coisa"
fi

echo
echo "======================"
if [ "$FALHAS" -gt 0 ]; then echo "REPROVADO ($FALHAS)"; exit 1; fi
if [ "$INCONCLUSIVOS" -gt 0 ]; then echo "INCONCLUSIVO ($INCONCLUSIVOS) — não é verde"; exit 2; fi
echo "VERDE"
