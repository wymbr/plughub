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
  BLQ=$(echo  "$CENSO" | jq -r '.bloquearia')
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

  echo "  ....         C. bloqueariam=$BLQ · legítimos órfãos=$ORF · tags não declaradas=$NAO"
  if [ "${ORF:-1}" -eq 0 ]; then
    ok "C. nenhuma exceção declarada está órfã (toda linha da tabela ainda se aplica)"
  else
    bad "C. $ORF exceção(ões) declarada(s) que não se aplicam mais — a tabela tem de encolher"
    echo "$CENSO" | jq -r '.legitimos_orfaos[]' | head -6 | sed 's/^/       /'
  fi
  if [ "${BLQ:-0}" -ge 1 ]; then
    echo "$CENSO" | jq -r '.a_declarar[] | "       a declarar: \(.skill):\(.step) \(.tag) [\(.tipo)]"'
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

echo
echo "======================"
if [ "$FALHAS" -gt 0 ]; then echo "REPROVADO ($FALHAS)"; exit 1; fi
if [ "$INCONCLUSIVOS" -gt 0 ]; then echo "INCONCLUSIVO ($INCONCLUSIVOS) — não é verde"; exit 2; fi
echo "VERDE"
