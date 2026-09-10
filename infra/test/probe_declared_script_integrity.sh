#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# probe_declared_script_integrity — o roteiro chega ao cliente COMO FOI ESCRITO.
#
# ── O defeito que este probe fecha, medido em 2026-09-10 ───────────────────────
#
# Num contato real do `limite_ia` o cliente recebeu
#
#     "Qual é o seu CPF? (só números, ex: (##) ****-####)"
#
# enquanto a forma publicada (`dialog_limite_roteiro`, nó `coletar_contato`) declara
#
#     "Qual é o seu CPF? (só números, ex: 52998224725)"
#
# A rede de texto livre (§D12, camada 3) mascarou o EXEMPLO do próprio roteiro, em
# voo — e ainda o tipou errado: o padrão de CPF exige pontuação, então 11 dígitos
# crus casaram o de TELEFONE. O cliente leu um gabarito de telefone onde se pedia um
# CPF. População medida no ClickHouse antes do conserto: **15 contatos**, todos entre
# 2026-09-04 20:55 (o dia em que a rede entrou) e 2026-09-10 11:05.
#
# O conserto é o carimbo de proveniência (`DECLARED_CONTENT_TOOLS` +
# `PipelineStateManager.setResult`), e ele tem testes de unidade. Este probe julga a
# outra metade: **o que o cliente REALMENTE recebeu**. Um carimbo certo com uma
# fiação errada passa nos testes e continua mutilando o roteiro na tela.
#
# ── DUAS grandezas, nunca um ramo só ──────────────────────────────────────────
#
#   EXPOSIÇÃO  quantos textos de roteiro publicados a rede ALTERARIA se olhasse
#              → informação: não é defeito, é a lista de quem depende do carimbo
#   DANO       quantas mensagens ENTREGUES desde a época carregam a versão mutilada
#              → defeito
#
# Contá-las num número só é o erro da D14.1: exposição não é sofrimento.
#
# ── A ÉPOCA, e por que ela não é conveniência ─────────────────────────────────
#
# O conserto é forward-only: mensagem entregue não se corrige por deploy, e as 15
# linhas antigas ficam no ledger para sempre. Sem época, este probe nasceria
# permanentemente vermelho por dano histórico — e um gate que não pode ficar verde
# ensina todo mundo a ignorá-lo. Precedentes: `SEGMENT_SLA_EPOCH`, `RSM01_EPOCH`.
#
# ── Testemunhas obrigatórias (o verde tem de poder ser vermelho) ──────────────
#
#   1. alguma forma publicada com texto      — senão não há população
#   2. a rede COMPILADA e capaz de alterar   — a canária é a própria linha do defeito
#
# Sem (2), "exposição zero" seria indistinguível de "a rede não carregou", e o probe
# sairia verde justamente quando o instrumento está morto.
#
# Uso:  bash infra/test/probe_declared_script_integrity.sh
# Pré:  dialog-api, ClickHouse e o container do skill-flow-service no ar.
# SAÍDA: 0 = íntegro · 1 = roteiro mutilado entregue · 2 = INCONCLUSIVO
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail
export PYTHONIOENCODING=utf-8

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIALOG="${DIALOG:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
CH_DB="${CH_DB:-plughub_demo}"
CT_ENGINE="${CT_ENGINE:-plughub-demo-skill-flow-service-1}"
CT_CH="${CT_CH:-plughub-demo-clickhouse-1}"

# Época do carimbo de proveniência: o deploy que parou de deixar a rede palpitar
# sobre roteiro publicado. Antes disto o dano é histórico e irreversível.
# Medida, não arredondada: é o `StartedAt` do container que passou a rodar o
# carimbo (`docker inspect plughub-demo-skill-flow-service-1`). Um valor "redondo"
# alguns minutos depois excluiria o próprio contato que provou o conserto.
EPOCA="${DECLARED_CONTENT_EPOCH:-2026-09-10T13:01:29Z}"

# A linha do defeito, usada como CANÁRIA da rede. Se a rede não a alterar, ela não
# está medindo nada — e o zero que ela produzir não é conformidade.
CANARIA="${CANARIA:-Qual é o seu CPF? (só números, ex: 52998224725)}"

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
inc() { echo "  ${YEL}—${RST} ⛔ INCONCLUSIVO: $*"; exit 2; }

echo "${BLD}══ integridade do roteiro declarado — publicado × entregue ══${RST}"

command -v curl    >/dev/null || inc "curl ausente"
command -v python3 >/dev/null || inc "python3 ausente"
command -v docker  >/dev/null || inc "docker ausente"
docker exec "$CT_ENGINE" node -e "0" >/dev/null 2>&1 || inc "container '$CT_ENGINE' sem node"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# ── 1. As formas PUBLICADAS ───────────────────────────────────────────────────
LISTA="$(curl -s -H "X-Tenant-ID: $TENANT" "$DIALOG/v1/dialog/forms" 2>/dev/null)"
IDS="$(printf '%s' "$LISTA" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
it = (d.get('forms') or d.get('items')) if isinstance(d,dict) else d
for f in (it or []):
    if f.get('status') == 'published': print(f.get('form_id',''))
" 2>/dev/null | grep -c . || true)"
[ "${IDS:-0}" -gt 0 ] || inc "nenhuma forma PUBLICADA em $DIALOG — sem população a medir"

: > "$TMP/textos.jsonl"
printf '%s' "$LISTA" | python3 -c "
import json,sys
d=json.load(sys.stdin)
it = (d.get('forms') or d.get('items')) if isinstance(d,dict) else d
for f in (it or []):
    if f.get('status') == 'published': print(f.get('form_id',''))
" | while read -r fid; do
  [ -n "$fid" ] || continue
  curl -s -H "X-Tenant-ID: $TENANT" "$DIALOG/v1/dialog/forms/$fid" 2>/dev/null \
    | python3 "$RAIZ/infra/test/_declared_script_texts.py" >> "$TMP/textos.jsonl" 2>/dev/null
  echo >> "$TMP/textos.jsonl"
done

python3 - "$TMP/textos.jsonl" > "$TMP/textos.json" <<'PY'
import json, sys
fora = []
for linha in open(sys.argv[1], encoding="utf-8"):
    linha = linha.strip()
    if not linha:
        continue
    try:
        fora.extend(json.loads(linha))
    except Exception:
        pass
json.dump(fora, sys.stdout, ensure_ascii=False)
PY

N_TXT="$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1],encoding='utf-8'))))" "$TMP/textos.json" 2>/dev/null || echo 0)"
[ "${N_TXT:-0}" -gt 0 ] || inc "as $IDS formas publicadas não renderam texto algum — leitura quebrada"
echo "      formas publicadas: $IDS · textos extraídos: $N_TXT"

# ── 2. A rede, importada do produto e provada VIVA ────────────────────────────
docker cp "$RAIZ/infra/test/_declared_script_net.mjs" \
          "$CT_ENGINE:/app/packages/skill-flow-engine/_declared_script_net.mjs" >/dev/null 2>&1 \
  || inc "não consegui copiar o auxiliar da rede para $CT_ENGINE"

rede() {  # stdin = JSON de textos ; stdout = TSV das linhas alteradas
  docker exec -i -w /app/packages/skill-flow-engine "$CT_ENGINE" \
    node ./_declared_script_net.mjs
}

python3 -c "
import json,sys
json.dump([{'form_id':'__canaria__','node_id':'__canaria__','texto':sys.argv[1]}], sys.stdout, ensure_ascii=False)
" "$CANARIA" | rede > "$TMP/canaria.tsv" 2>"$TMP/canaria.err"
if [ ! -s "$TMP/canaria.tsv" ]; then
  echo "      $(head -2 "$TMP/canaria.err" 2>/dev/null)"
  inc "a CANÁRIA atravessou intacta — a rede não compilou ou mudou de forma.
      Sem ela, 'exposição zero' seria indistinguível de instrumento morto."
fi
echo "      canária da rede: VIVA (a linha do defeito ainda é alterável)"

rede < "$TMP/textos.json" > "$TMP/expostos.tsv" 2>/dev/null
N_EXP="$(grep -c . "$TMP/expostos.tsv" 2>/dev/null || true)"

# ── 3. O DANO: a versão mutilada chegou a alguém desde a época? ───────────────
N_DANO=0
: > "$TMP/dano.txt"
while IFS=$'\t' read -r form node original alterado; do
  [ -n "${alterado:-}" ] || continue
  N="$(docker exec "$CT_CH" clickhouse-client -d "$CH_DB" \
        --param_agulha="$alterado" --param_epoca="$EPOCA" \
        -q "SELECT count() FROM messages
             WHERE position(content, {agulha:String}) > 0
               AND timestamp >= parseDateTimeBestEffort({epoca:String})" 2>/dev/null | tr -d '\r')"
  case "${N:-x}" in ''|*[!0-9]*) inc "ClickHouse não respondeu para '$form/$node' ('$N')" ;; esac
  if [ "$N" -gt 0 ]; then
    N_DANO=$((N_DANO + N))
    printf '%s · %s — %s mensagem(ns)\n        publicado: %s\n        entregue:  %s\n' \
      "$form" "$node" "$N" "$original" "$alterado" >> "$TMP/dano.txt"
  fi
done < "$TMP/expostos.tsv"

# ── 4. Veredicto ──────────────────────────────────────────────────────────────
echo
if [ "${N_EXP:-0}" -gt 0 ]; then
  echo "── informação (não é defeito) ──────────────────────────────────────────────"
  echo "   $N_EXP linha(s) de roteiro publicado que a rede ALTERARIA se olhasse."
  echo "   Elas dependem do carimbo de proveniência estar certo — é a lista de quem"
  echo "   quebra primeiro se alguém desfizer o conserto, não uma lista de defeitos."
  cut -f1,2,3 "$TMP/expostos.tsv" | sed 's/^/      /' | head -20
fi

echo
echo "── veredicto ───────────────────────────────────────────────────────────────"
if [ "$N_DANO" -eq 0 ]; then
  echo "   ${GRN}✅${RST} nenhum roteiro publicado chegou mutilado desde $EPOCA."
  echo "      Conferido contra $N_EXP linha(s) expostas, com a rede provada viva —"
  echo "      a testemunha que separa isto de uma medição que não mediu nada."
  exit 0
fi
echo "   ${RED}❌${RST} $N_DANO mensagem(ns) entregues com o roteiro MUTILADO desde $EPOCA:"
sed 's/^/      /' "$TMP/dano.txt"
echo
echo "   O carimbo de proveniência não está alcançando este caminho. Ver"
echo "   \`DECLARED_CONTENT_TOOLS\` (@plughub/schemas) e \`setResult\` (skill-flow-engine)."
exit 1
