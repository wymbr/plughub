#!/usr/bin/env bash
# probe_meta_ttl_bridge_off.sh — o controle decisivo do TTL do `session:{id}:meta`.
#
# ⚠️ MUTA ESTADO DE SERVIÇO: para e religa o orchestrator-bridge. Reversível, e a
#    mensagem de alocação fica no Kafka esperando — é justamente disso que o probe
#    vive. Não rodar em ambiente que alguém esteja usando.
#
# ── por que este probe existe ────────────────────────────────────────────────
# Duas tentativas anteriores voltaram INCONCLUSIVAS pelo MESMO motivo, e ele não
# era o defeito:
#   v1  lia o TTL logo após o POST — perdeu a corrida (cada `docker exec` custa
#       ~0,5 s e a alocação cabe nisso).
#   v2  usou `max_concurrent_sessions: 1` para deixar sessões NA FILA como
#       controle — mas o skill suspende no `delegate` e devolve a vaga na hora,
#       então a fila drenou antes da 1ª leitura. As 5 nasceram "já em 14400".
# Preflight de símbolo confirmou que o W3 (`webhook.py:596`, SETEX 86_400) ESTÁ
# na imagem e roda sem erro. Logo o 86400 existe e é sobrescrito depressa demais
# para ser fotografado por fora.
#
# A variável é o BRIDGE. Com ele parado, ninguém reescreve o meta, e o valor que
# a porta webhook escreveu fica parado no Redis pelo tempo que eu quiser. Religar
# consome o mesmo evento e produz a transição na MESMA chave. Um único valor
# muda; o caminho é o mesmo.
#
# ── PREVISÕES (contadas no fonte, antes de rodar) ────────────────────────────
#   A · bridge parado, logo após o trigger  → TTL = 86400 (webhook.py:596)
#   B · bridge religado, após alocação      → TTL = 14400 (_stl(), main.py:266)
#       e a chave GANHA os campos agent_type_id / instance_id / pool_id
#   ⇒ A ausente (A ≈ 14400) refuta o defeito: algo mais escreve antes do bridge.
#   ⇒ B ausente (B ainda 86400) refuta o truncamento: o bridge não encurta.
#   ⇒ A=86400 e B=14400 ⇒ TRUNCAMENTO, com os dois números na mesma tela.
#
# Veredicto de TRÊS estados: 0 = sem truncamento · 1 = truncamento · 3 = INCONCLUSIVO
set -uo pipefail

COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
GW="${GW:-http://localhost:8010}"
TENANT="${TENANT:-tenant_demo}"
POOL="${POOL:-formfill_demo_ia}"
BRIDGE="${BRIDGE:-orchestrator-bridge}"

R() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }
fields() { printf '%s' "$1" | grep -o '"[a-z_]\+"[[:space:]]*:' | tr -d '":' | tr -d ' ' | sort -u; }

echo "══ TTL do session:meta — bridge como única variável ══"

[ "$(R PING)" = "PONG" ] || { echo "   ⛔ INCONCLUSIVO — redis inalcançável"; exit 3; }

restore() { echo "   … religando $BRIDGE"; $COMPOSE start "$BRIDGE" >/dev/null 2>&1; }
trap restore EXIT

# ── A · bridge FORA ──────────────────────────────────────────────────────────
echo
echo "── A · com o bridge parado ───────────────────────────────────────────────"
$COMPOSE stop "$BRIDGE" >/dev/null 2>&1 || {
  echo "   ⛔ INCONCLUSIVO — não consegui parar '$BRIDGE'"; exit 3; }
echo "      $BRIDGE parado"

SID="$(curl -s --max-time 15 -X POST "$GW/v1/channels/webhook/pool/$POOL" \
        -H 'content-type: application/json' \
        -d "{\"tenant_id\":\"$TENANT\",\"payload\":{}}" \
      | sed -E 's/.*"session_id":"([^"]+)".*/\1/')"
if [ -z "$SID" ] || [ "${#SID}" -lt 6 ]; then
  echo "   ⛔ INCONCLUSIVO — trigger não devolveu session_id"; exit 3; fi
KEY="session:$SID:meta"
echo "      session=$SID"

sleep 3
TTL_A="$(R TTL "$KEY")"
EPOCH_A="$(date +%s)"      # relógio REAL — ver a nota do bloco B
F_A="$(fields "$(R GET "$KEY")")"
echo "      TTL(A) = $TTL_A"
echo "      campos : $(printf '%s' "$F_A" | tr '\n' ' ')"

case "$TTL_A" in ''|*[!0-9-]*) TTL_A=-99 ;; esac
if [ "$TTL_A" -lt 0 ]; then
  echo "   ⛔ INCONCLUSIVO — a chave não existe com o bridge fora. Quem a criava"
  echo "      era o bridge, não a porta: revise o mapa de escritores."
  exit 3
fi
if [ "$TTL_A" -le 14400 ]; then
  echo "   ⛔ INCONCLUSIVO — nasceu com $TTL_A, não com 86400, e o bridge nem"
  echo "      estava no ar. O 86400 do webhook.py:596 não está chegando à chave."
  exit 3
fi
echo "   ✅ A · a porta webhook escreve prazo longo ($TTL_A) — testemunha obtida"

# ── B · bridge DE VOLTA ──────────────────────────────────────────────────────
echo
echo "── B · religando; o evento de alocação estava esperando no Kafka ─────────"
$COMPOSE start "$BRIDGE" >/dev/null 2>&1
trap - EXIT

# ⚠️ O critério NÃO pode ser "caiu mais de N segundos abaixo de A".
# A v1 usava N=120 fixo e produziu um FALSO VERMELHO na primeira execução após o
# conserto: 86397 → 86275, queda de 122 s, com o veredicto "TRUNCAMENTO". Só que o
# laço tinha levado ~122 s de RELÓGIO — cada `docker exec` custa ~1 s e o contador
# `i*2` só somava os `sleep`. O instrumento mediu a própria lentidão e chamou de
# defeito. Falso vermelho custa o mesmo que falso verde: gasta a confiança no portão.
#
# O critério honesto compara com o decaimento NATURAL medido no relógio real:
#   esperado = TTL(A) − (agora − instante de A)      ⇒ preservado
#   observado ≪ esperado                             ⇒ truncado
# e a saída imprime OS DOIS, para o leitor conferir a subtração.
#
# A saída do laço é a ESCRITA DO BRIDGE (campo `agent_type_id` aparecendo), não um
# número de voltas: sem isso o probe pode encerrar antes de o bridge agir e chamar
# "sem truncamento" o que na verdade é "não mediu".
TTL_B="$TTL_A"; MUDOU=""; ESCREVEU=""
for i in $(seq 1 45); do
  sleep 2
  t="$(R TTL "$KEY")"
  case "$t" in ''|*[!0-9-]*) continue ;; esac
  if [ "$t" -lt 0 ]; then
    echo "      chave EXPIROU/sumiu"; TTL_B=-1; MUDOU="sumiu"; break
  fi
  TTL_B="$t"
  if printf '%s' "$(R GET "$KEY")" | grep -q '"agent_type_id"'; then
    ESCREVEU="sim"
    echo "      o bridge escreveu após $(( $(date +%s) - EPOCH_A ))s de relógio"
    break
  fi
done
# ── SELFTEST: provar que este portão CONSEGUE reprovar ───────────────────────
# Ele já ficou vermelho duas vezes, e nenhuma serve de prova para a versão atual
# do critério: a 1ª foi o defeito real (com o limiar fixo antigo), a 2ª foi o
# próprio limiar errando. Um portão cujo critério nunca reprovou contra um valor
# ruim é confiança comprada sem lastro. `SELFTEST=1` injeta exatamente o
# encurtamento que o código antigo produzia (EXPIRE 14400 depois da escrita do
# bridge) e EXIGE vermelho.
if [ "${SELFTEST:-0}" = "1" ] && [ -n "$ESCREVEU" ]; then
  echo "      ⚗️  SELFTEST: injetando EXPIRE 14400 — o que o código antigo fazia"
  R EXPIRE "$KEY" 14400 >/dev/null
  TTL_B="$(R TTL "$KEY")"
fi

EPOCH_B="$(date +%s)"
DECORRIDO=$((EPOCH_B - EPOCH_A))
ESPERADO=$((TTL_A - DECORRIDO))
echo "      decorrido (relógio real) : ${DECORRIDO}s"
echo "      TTL esperado sem truncar : $ESPERADO   (= $TTL_A − $DECORRIDO)"
echo "      TTL observado            : $TTL_B"
# Tolerância de 30 s cobre a latência das leituras; truncamento aqui vale ~72_000 s,
# ordens de grandeza acima — não é um limiar disputado.
if [ "$MUDOU" != "sumiu" ] && [ $((ESPERADO - TTL_B)) -gt 30 ]; then
  MUDOU="caiu"
fi
F_B="$(fields "$(R GET "$KEY")")"
echo "      TTL(B) = $TTL_B"
echo "      campos : $(printf '%s' "$F_B" | tr '\n' ' ')"
GANHOU="$(comm -13 <(printf '%s\n' "$F_A") <(printf '%s\n' "$F_B") | tr '\n' ' ')"
PERDEU="$(comm -23 <(printf '%s\n' "$F_A") <(printf '%s\n' "$F_B") | tr '\n' ' ')"
echo "      ganhou : ${GANHOU:-<nenhum>}"
echo "      perdeu : ${PERDEU:-<nenhum>}"

echo
if [ -z "$ESCREVEU" ] && [ -z "$MUDOU" ]; then
  echo "   ⛔ INCONCLUSIVO — o bridge voltou e NÃO escreveu na chave dentro da"
  echo "      janela (nenhum agent_type_id apareceu). Sem escrita do bridge não há"
  echo "      o que julgar: isto NÃO é 'passou', é 'não mediu'."
  exit 3
fi
RC=0
if [ "$MUDOU" = "caiu" ]; then
  echo "   ❌ TRUNCAMENTO CONFIRMADO — $TTL_A → $TTL_B na alocação, mesma chave."
  echo "      O meta passa a morrer em ~4 h enquanto a workflow que ele descreve"
  echo "      fica suspensa por timeout_hours*3600+3600 (48 h de default)."
  RC=1
elif [ "$MUDOU" = "sumiu" ]; then
  echo "   ❌ pior que truncamento — a chave foi APAGADA pela alocação."
  RC=1
else
  echo "   ✅ sem truncamento — o bridge escreveu (ganhou: $GANHOU) e o prazo"
  echo "      seguiu em $TTL_B. A regra do MAIOR TTL já está sendo respeitada."
fi
[ -n "$PERDEU" ] && { echo "   ❌ e PERDEU campo: $PERDEU"; RC=1; }

if [ "${SELFTEST:-0}" = "1" ]; then
  echo
  if [ "$RC" -eq 1 ]; then
    echo "   ⚗️  SELFTEST OK — o critério reprovou o valor ruim. O portão discrimina."
    exit 0
  fi
  echo "   ⛔ SELFTEST FALHOU — injetei 14400 e o critério deu VERDE."
  echo "      Enquanto isto não reprovar, o verde da execução normal não vale nada."
  exit 1
fi

echo
echo "══ veredicto: rc=$RC ══"
exit "$RC"
