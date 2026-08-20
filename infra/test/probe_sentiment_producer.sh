#!/usr/bin/env bash
# probe_sentiment_producer.sh — a trilha de sentimento roda, morre, ou nem é chamada?
#
# ── A contradição que este probe existe para resolver ────────────────────────
# O TODO/passagem diz "sentimento sem produtor", apoiado em duas leituras que
# NÃO podem estar as duas certas:
#
#   (i)  `update_partial_params` só é alcançado por `/v1/inference`, que teve 0
#        requisições — daí "sem produtor".
#   (ii) `session.py:140` faz `HGET` numa chave que é string JSON ⇒ `WRONGTYPE`,
#        e o `except` de `:162` loga "Sentiment pipeline failed".
#
# Varredura de código de 2026-08-22 refuta (i) e reabre (ii):
#   · a rota é `POST /inference`, não `/v1/inference` — e não tem chamador nenhum
#     no repositório inteiro (nem produção, nem teste);
#   · **`/v1/reason` TAMBÉM chama `update_partial_params`** (`main.py:357`), e é
#     ele que carrega as 116 requisições.
# Logo a trilha É percorrida. Então ou ela falha 116 vezes (e o log de 08-20 não
# viu), ou ela roda e grava — e o que grava é `sentiment_score = 0.0`, porque
# `main.py:352` só aproveita o campo se o `output_schema` do step o declarar, e
# **nenhum skill do repo declara**. Zero é NEUTRO: indistinguível de não-medido.
#
# ── PREVISÕES ────────────────────────────────────────────────────────────────
#   P1  requisições a /v1/reason no log: > 0        (TESTEMUNHA — sem ela nada julga)
#   P2  ramifica em três, e os três são desfechos DIFERENTES:
#        (a) N falhas "Sentiment pipeline failed" ≈ N reasons
#            ⇒ o pipeline MORRE no WRONGTYPE. O `HGET` deixa de ser "o segundo
#              problema" e passa a ser o ÚNICO bloqueio.
#        (b) 0 falhas + chaves `*sentiment_live*` existindo
#            ⇒ o pipeline RODA e grava 0.0 sob tenant vazio. Pior que (a): valor
#              plausível, ninguém fica vermelho, e o painel mostra "neutro".
#        (c) 0 falhas + 0 chaves
#            ⇒ não escreveu e não falhou: investigar antes de concluir.
#   P3  as chaves nascem com tenant VAZIO (`:pool:{p}:sentiment_live`), porque
#       nenhum chamador de `/v1/reason` envia `tenant_id` (`engine-runner.ts:204`,
#       `skill-flow-service/index.ts:249`). Por isso o scan NÃO filtra por tenant:
#       filtrar por `tenant_demo:*` devolveria vazio e eu concluiria (c) por engano.
#
# Veredicto de TRÊS estados: 0 = grava · 1 = morre · 3 = INCONCLUSIVO
set -uo pipefail

COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
TENANT="${TENANT:-tenant_demo}"

R() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }

echo "══ trilha de sentimento — produtor vivo? ══"
[ "$(R PING)" = "PONG" ] || { echo "   ⛔ INCONCLUSIVO — redis inalcançável"; exit 3; }

# ── P1 · testemunha: o caminho é percorrido? ─────────────────────────────────
echo
echo "── P1 · tráfego no ai-gateway ────────────────────────────────────────────"
LOG="$($COMPOSE logs ai-gateway 2>/dev/null | tr -d '\r')"
NLOG="$(printf '%s\n' "$LOG" | grep -c .)"
N_REASON="$(printf '%s\n' "$LOG" | grep -c 'POST /v1/reason')"
N_INFER="$(printf '%s\n' "$LOG" | grep -cE 'POST /inference')"
echo "      linhas de log        : $NLOG"
echo "      POST /v1/reason      : $N_REASON"
echo "      POST /inference      : $N_INFER   (a rota que a doc chamava de /v1/inference)"
if [ "$NLOG" -eq 0 ]; then
  echo "   ⛔ INCONCLUSIVO — log vazio; container recriado?"; exit 3
fi
if [ "$N_REASON" -eq 0 ]; then
  echo "   ⛔ INCONCLUSIVO — nenhuma requisição a /v1/reason nesta janela."
  echo "      Sem tráfego a trilha não é exercitada e P2 não julga nada."
  exit 3
fi

# ── P2 · morreu, ou gravou? ──────────────────────────────────────────────────
echo
echo "── P2 · a trilha falhou? ─────────────────────────────────────────────────"
N_FAIL="$(printf '%s\n' "$LOG" | grep -c 'Sentiment pipeline failed')"
N_FAIL2="$(printf '%s\n' "$LOG" | grep -c 'Failed to update session params')"
echo "      'Sentiment pipeline failed'      : $N_FAIL"
echo "      'Failed to update session params': $N_FAIL2   (except de fora, main.py:366)"
echo "      (as DUAS mensagens, porque falham em pontos diferentes e 08-20 só"
echo "       procurou a primeira — ausência de uma não é ausência da outra)"

echo
echo "── P3 · escreveu alguma coisa? ───────────────────────────────────────────"
# SEM filtro de tenant, de propósito: os chamadores não enviam tenant_id, então a
# chave nasce com prefixo vazio. Filtrar por tenant devolveria 0 e eu concluiria
# "não gravou" quando gravou no lugar errado — que é um ACHADO, não uma ausência.
LIVE="$(R --scan --pattern '*sentiment_live*' | head -20)"
N_LIVE="$(printf '%s\n' "$LIVE" | grep -c .)"
echo "      chaves *sentiment_live* : $N_LIVE"
[ "$N_LIVE" -gt 0 ] && printf '%s\n' "$LIVE" | sed 's/^/         /'
CTX="$(R --scan --pattern '*:ctx:*' | head -40)"
N_TAG=0
for k in $CTX; do
  [ -n "$k" ] || continue
  v="$(R HGET "$k" 'session.sentimento.current')"
  [ -n "$v" ] && { N_TAG=$((N_TAG+1)); [ "$N_TAG" -le 3 ] && echo "         $k → $v"; }
done
echo "      hashes ctx com session.sentimento.current : $N_TAG"

# ── P4 · o teste ATIVO, que é o único que julga ──────────────────────────────
#
# ⚠️ P3 sozinho NÃO julga, e a v1 deste probe concluiu (c) por causa disso:
# `sentiment_live` tem TTL de 300 s (`_SENTIMENT_LIVE_TTL`) e o ctx, 4 h. Procurar
# essas chaves depois de um tráfego de idade DESCONHECIDA mede expiração, não
# ausência de escrita. Artefato com prazo curto só se mede contra tráfego que
# você acabou de gerar.
#
# Aqui eu chamo `/v1/reason` DIRETO — é exatamente o caminho sob teste, e me deixa
# controlar `tenant_id` e `output_schema`, que são as duas variáveis da pergunta.
#
# DOIS casos, e a diferença entre eles é a resposta de desenho:
#   A · output_schema SEM `sentiment_score` (como todo skill do repo hoje)
#       ⇒ previsto: escreve, com score 0.0 — NEUTRO, indistinguível de não-medido
#   B · output_schema COM `sentiment_score`
#       ⇒ previsto: escreve o valor que o LLM devolveu
# Se A e B derem o MESMO valor, o campo não está sendo aproveitado e o diagnóstico
# muda de novo.
echo
echo "── P4 · chamada ATIVA a /v1/reason ───────────────────────────────────────"
AIGW="${AIGW:-http://localhost:3200}"
SA="probe-sent-a-$$"; SB="probe-sent-b-$$"
MSG="Já é a terceira vez que ligo e ninguém resolve. Estou muito irritado."

# ⚠️ A forma do `output_schema` é `{campo: {type: ...}}`, COPIADA de um skill real
# (`agente_fila_v1.yaml:95-97`), não inventada. A v1 mandou `{"resposta":"string"}`
# e levou 422 do Pydantic — o handler nem rodou, e o probe teria concluído
# "não escreveu" sobre uma requisição que nunca chegou ao código sob teste.
curl -s --max-time 60 -X POST "$AIGW/v1/reason" -H 'content-type: application/json' \
  -d "{\"session_id\":\"$SA\",\"tenant_id\":\"$TENANT\",\"prompt_id\":\"probe\",
       \"input\":{\"mensagem_cliente\":\"$MSG\"},
       \"output_schema\":{\"resposta\":{\"type\":\"string\"}}}" > /tmp/_sent_a.json
echo "      A (schema SEM sentiment_score): $(head -c 200 /tmp/_sent_a.json)"

curl -s --max-time 60 -X POST "$AIGW/v1/reason" -H 'content-type: application/json' \
  -d "{\"session_id\":\"$SB\",\"tenant_id\":\"$TENANT\",\"prompt_id\":\"probe\",
       \"input\":{\"mensagem_cliente\":\"$MSG\"},
       \"output_schema\":{\"resposta\":{\"type\":\"string\"},
                          \"sentiment_score\":{\"type\":\"number\"}}}" > /tmp/_sent_b.json
echo "      B (schema COM sentiment_score): $(head -c 260 /tmp/_sent_b.json)"

# DOIS modos de "não rodou", e eles exigem mensagens diferentes. A v1 casava
# `"detail"`, campo que aparece nos DOIS — então um 401 do provedor era anunciado
# como "corpo inválido (422)", mandando consertar a coisa errada. Distinguir pelo
# discriminador de cada forma, nunca por um campo que ambas têm.
if grep -q 'upstream_model_error' /tmp/_sent_a.json 2>/dev/null; then
  echo
  echo "   ⛔ INCONCLUSIVO — o PROVEDOR recusou (ver corpo acima; 401 = credencial)."
  echo "      Consequência que passa muito de sentimento: se o LLM falha, o handler"
  echo "      levanta ANTES de main.py:357, e nada pós-LLM roda — sentimento, intent,"
  echo "      confidence, estado do supervisor. E todo step reason de todo skill cai"
  echo "      no on_failure. Sem credencial válida este probe NÃO pode julgar a trilha."
  exit 3
fi
if grep -q '"loc"' /tmp/_sent_a.json 2>/dev/null; then
  echo "   ⛔ INCONCLUSIVO — /v1/reason recusou o CORPO (422 de validação)."
  echo "      O caminho sob teste não foi exercitado; conserte o corpo antes de ler P4."
  exit 3
fi

sleep 2
echo
echo "      chaves logo após as chamadas:"
LIVE2="$(R --scan --pattern '*sentiment_live*')"
N_LIVE2="$(printf '%s\n' "$LIVE2" | grep -c .)"
echo "      *sentiment_live* : $N_LIVE2"
printf '%s\n' "$LIVE2" | sed 's/^/         /'
for s in "$SA" "$SB"; do
  echo "         $TENANT:ctx:$s → $(R HGET "$TENANT:ctx:$s" 'session.sentimento.current')"
done

echo
if [ "$N_LIVE2" -gt 0 ]; then
  echo "   ✅ A TRILHA GRAVA — o produtor está vivo e no caminho do /v1/reason."
  echo "      O 'sem produtor' do TODO estava errado no mecanismo. Compare o score"
  echo "      de A e de B acima: se A saiu 0.0, o sentimento não é MEDIDO pela"
  echo "      plataforma, é auto-reportado pelo schema — e nenhum skill o declara."
  exit 0
fi

if [ "$N_FAIL" -gt 0 ]; then
  echo "   ❌ (a) A TRILHA MORRE — $N_FAIL falhas para $N_REASON reasons."
  echo "      O HGET numa chave string é o ÚNICO bloqueio, não 'o segundo problema'."
  exit 1
fi
if [ "$N_LIVE" -gt 0 ] || [ "$N_TAG" -gt 0 ]; then
  echo "   ⚠️  (b) A TRILHA RODA E GRAVA — e é o desfecho PIOR."
  echo "      Nenhum skill declara sentiment_score no output_schema, então o valor"
  echo "      gravado é 0.0 = NEUTRO, indistinguível de 'não medido'. E o tenant"
  echo "      vai vazio, então o dado não está no namespace onde alguém o procura."
  exit 1
fi
echo "   ⛔ (c) INCONCLUSIVO — não falhou e não escreveu. Há um ramo antes do"
echo "      bloco de sentimento que retorna cedo. Ler session.py:111 (self.get)"
echo "      antes de concluir qualquer coisa."
exit 3
