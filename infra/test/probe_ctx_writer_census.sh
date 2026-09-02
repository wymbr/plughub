#!/usr/bin/env bash
# probe_ctx_writer_census.sh — CNS-06: o número que dimensiona a ALW-02 tem CRITÉRIO.
#
# ── Por que este gate existe ──────────────────────────────────────────────────
#
# Havia TRÊS números para "quantos escritores diretos do ContextStore existem" — 12 na
# §1.7 do ADR, 16 numa contagem estrutural, 18 numa textual — e nenhum critério escrito.
# A ALW-02 (o choke point de escrita) é a maior tarefa do arco ALLOWLIST, e estava sendo
# dimensionada por um número que ninguém sabia reproduzir.
#
# O critério vive no cabeçalho de `_ctx_writer_census.py`. Este gate impede que ele
# volte a ser opinião:
#
#   A. o instrumento roda e produz número
#   B. o número não regride em silêncio (piso declarado)
#   C. a divergência contra o ORÁCULO é a DECLARADA — uma nova reprova
#   D. testemunha negativa: escrita em OUTRO hash não entra na conta
#
# ── O ramo C é o coração, e a divergência é o achado ─────────────────────────
#
# O oráculo (`ESCRITORES` do censo de cadastro) e o instrumento foram construídos por
# caminhos independentes e para fins diferentes. Eles DIVERGEM de propósito em três
# arquivos, e cada um dos três é uma informação:
#
#   + journey.ts   o instrumento acha, o oráculo não: são os DOIS FUNIS — o
#   + writer.py    `writeContextTag` (TS, já existia) e o `write_context_tags`
#                  (Python, ALW-02 passo 3). Contá-los é o que revela que a ALW-02 não
#                  era "construir um choke point", e sim estender o que já estava lá.
#   − server.ts    o oráculo lista, o instrumento não: os TRÊS importam e chamam
#   − session.ts   `writeContextTag`. São chamadores do funil, não escritores diretos —
#   − bpm.ts       e o critério diz que o helper conta UMA vez, no helper.
#                  (`bpm.ts` entrou nesta lista em 2026-09-02, no passo 1 da ALW-02.)
#
# Um item a mais em qualquer direção significa que alguém escreveu no ContextStore por
# um caminho novo, ou que o funil ganhou/perdeu um cliente. Nos dois casos, a ALW-02
# mudou de tamanho e alguém precisa saber. Enquanto a migração corre, o esperado é o
# instrumento ENCOLHER e o oráculo CRESCER, sítio a sítio — e cada passo baixa o piso
# aqui, nomeando quem saiu.
set -u
cd "$(dirname "$0")/../.." || exit 2

FAIL=0
ok()  { echo "  v $1"; }
bad() { echo "  x $1"; FAIL=1; }
huh() { echo "  ? $1"; FAIL=2; }

#: Piso, não alvo. Baixar exige explicar o que saiu; subir sem explicar é escritor novo
#: entrando sem passar pela decisão da ALW-02.
#:
#: 8/22 → 7/21 em 2026-09-02 (ALW-02 passo 1): `tools/bpm.ts` deixou de fazer `hset` cru
#: no ctx e passou a chamar `writeContextTag`. Saiu do instrumento e entrou no oráculo,
#: como os outros dois chamadores do funil.
#:
#: 4/16 → 2/2 no fim do passo 3: `channel-gateway` (4) e `orchestrator-bridge` (10)
#: migraram, e o ALVO FOI ATINGIDO — os 2 que restam SÃO os dois funis. Daqui em diante o
#: piso é PARADO: qualquer subida é escritor direto novo, sem exceção legítima. Se algum
#: dia um terceiro funil for legítimo, ele entra no `ESPERADO_INST` junto com o piso.
#:
#: 8/22 → 4/16 antes disso (passo 3, primeiros três serviços): `routing-engine` (2),
#: `ai-gateway` (2) e `evaluation-api` (1) passaram a chamar o funil Python. O ai-gateway
#: perdeu DOIS arquivos porque o falso positivo declarado do `sentiment_emitter:163` (a
#: escrita em `sentiment_live`) sumiu junto: ele existia por causa de uma variável `key`
#: que ficou morta quando o `hset` de ctx saiu.
#:
#: 7/21 → 8/22 antes disso (passo 3, funil): nasceu o funil Python
#: (`py-contextstore/.../writer.py`). O instrumento não distingue funil de escritor — por
#: CRITÉRIO o helper conta uma vez, no helper —, então o piso sobe por um motivo que é o
#: oposto de regressão. **Trajetória esperada daqui**: cada serviço migrado tira os seus
#: sítios do instrumento, e o piso desce até **2 arquivos / 2 sítios**, que são os DOIS
#: funis. Se parar acima disso, sobrou escritor direto.
PISO_ARQUIVOS=2
PISO_SITIOS=2

echo "=== probe_ctx_writer_census — CNS-06 (dimensiona a ALW-02) ==="
echo

OUT="$(python3 infra/test/_ctx_writer_census.py 2>/dev/null)"
if [ -z "$OUT" ]; then
  huh "A: o instrumento nao produziu saida (python3? AST?)"
  echo; echo "INCONCLUSIVO"; exit 2
fi
ok "A: instrumento rodou"

# Le a linha RESUMO, que o instrumento emite SEM acento justamente para isto: casar
# acento no shell depende de locale, e bancada decidindo veredicto e o padrao que
# este arco passou o dia consertando.
RESUMO_L=$(printf "%s" "$OUT" | grep "^RESUMO " | head -1)
ARQ=$(echo "$RESUMO_L" | tr " " "
" | grep "^arquivos=" | cut -d= -f2)
SIT=$(echo "$RESUMO_L" | tr " " "
" | grep "^sitios=" | cut -d= -f2)
[ -z "$ARQ" ] && ARQ=0
[ -z "$SIT" ] && SIT=0
echo "     medido: $ARQ arquivos, $SIT sitios (piso $PISO_ARQUIVOS/$PISO_SITIOS)"

if [ "$ARQ" -eq "$PISO_ARQUIVOS" ] && [ "$SIT" -eq "$PISO_SITIOS" ]; then
  ok "B: numero estavel no piso declarado"
elif [ "$ARQ" -lt "$PISO_ARQUIVOS" ] || [ "$SIT" -lt "$PISO_SITIOS" ]; then
  bad "B: o numero ENCOLHEU ($ARQ/$SIT) — se um escritor virou funil, baixe o piso NESTE arquivo e diga qual"
else
  bad "B: o numero CRESCEU ($ARQ/$SIT) — escritor direto novo entrou sem passar pela ALW-02"
fi

# C — a divergencia contra o oraculo e a DECLARADA
INST_ONLY=$(printf '%s' "$OUT" | sed -n 's/^     + //p' | sort)
ORAC_ONLY=$(printf '%s' "$OUT" | sed -n 's/^     - //p' | sort)
ESPERADO_INST="packages/mcp-server-plughub/src/tools/journey.ts
packages/py-contextstore/src/plughub_contextstore/writer.py"
#: Chamadores dos funis. A lista CRESCE a cada serviço migrado, e atualizá-la é a
#: fricção certa: migração é ato deliberado, e uma lista derivada não reprovaria o dia em
#: que um arquivo sair do instrumento por ter parado de escrever, em vez de por ter
#: passado a chamar o funil.
ESPERADO_ORAC="packages/ai-gateway/src/plughub_ai_gateway/copilot_emitter.py
packages/ai-gateway/src/plughub_ai_gateway/sentiment_emitter.py
packages/channel-gateway/src/plughub_channel_gateway/adapters/webhook.py
packages/evaluation-api/src/plughub_evaluation_api/router.py
packages/mcp-server-plughub/src/server.ts
packages/mcp-server-plughub/src/tools/bpm.ts
packages/mcp-server-plughub/src/tools/session.ts
packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py
packages/routing-engine/src/plughub_routing/main.py"
if [ "$INST_ONLY" = "$ESPERADO_INST" ]; then
  ok "C: so-no-instrumento sao os DOIS funis (writeContextTag + write_context_tags)"
else
  bad "C: so-no-instrumento mudou — esperados os dois funis, veio: $(echo $INST_ONLY)"
fi
if [ "$ORAC_ONLY" = "$ESPERADO_ORAC" ]; then
  ok "C: so-no-oraculo sao os CHAMADORES do funil"
else
  bad "C: so-no-oraculo mudou — veio: $(echo $ORAC_ONLY)"
fi

# D — testemunha NEGATIVA: hash que nao e ContextStore fica de fora.
# Sem ela, um casador largo demais passaria em A/B/C contando `menu:waiting:` como
# ContextStore — e ja passou: a primeira versao do instrumento trazia tres falsos
# positivos do skill-flow-engine porque casava o `ctx.` do RECEPTOR (`ctx.redis.hset`).
if printf '%s' "$OUT" | grep -q "skill-flow-engine/src/steps/"; then
  bad "D: escrita em OUTRO hash entrou na conta (menu:waiting: nao e ContextStore)"
else
  ok "D: escrita em outro hash NAO entra — o marcador e a chave, nao o receptor"
fi

# ── E — quem USA o funil REGISTRA o transporte ───────────────────────────────
#
# Acrescentado em 2026-09-02, depois de o defeito acontecer. A ALW-02 migrou os sitios de
# escrita de cinco servicos e esqueceu o `set_context_map_fetcher` em DOIS deles
# (channel-gateway, orchestrator-bridge). Ficou invisivel por dois dias porque o carregador
# do MAPA tem fallback embutido: as escritas seguiram funcionando, carimbadas com
# `atributo.fallback: true` — o sinal existia e ninguem o contava. Medido ao vivo: 16 de 16
# entradas com fallback, e campos DECLARADOS saindo como `unknown` porque o mapa embutido
# nao tem o vocabulario do tenant.
#
# Quem denunciou foi o CATALOGO de tipos, que nao tem fallback de proposito. A licao e a do
# ramo: **a resiliencia de um componente esconde a fiacao faltando de outro**, e por isso a
# fiacao precisa de teste PROPRIO, nao da ausencia de sintoma.
echo
SEM_FIACAO=""
for d in packages/*/src; do
  svc="$(echo "$d" | cut -d/ -f2)"
  grep -rq "write_context_tags" "$d" 2>/dev/null || continue
  [ "$svc" = "py-contextstore" ] && continue          # e a lib, nao um consumidor
  grep -rq "set_context_map_fetcher" "$d" 2>/dev/null || SEM_FIACAO="$SEM_FIACAO $svc"
done
if [ -z "$SEM_FIACAO" ]; then
  ok "E: todo servico que usa o funil registra o transporte no boot"
else
  bad "E: usa o funil e NAO registra o transporte:$SEM_FIACAO — as escritas vao sair com atributo.fallback:true, em silencio"
fi

echo
if [ "$FAIL" = "0" ]; then
  echo "OK — o numero da ALW-02 tem criterio, piso e divergencia explicada"
elif [ "$FAIL" = "2" ]; then
  echo "INCONCLUSIVO"
else
  echo "FALHA"
fi
exit "$FAIL"
