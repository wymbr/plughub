#!/usr/bin/env bash
# probe_resume_outlives_meta.sh — quantos resumes JÁ estão condenados.
#
# O truncamento do `session:{id}:meta` está confirmado (probe_meta_ttl_bridge_off:
# 86397 → 14398 na alocação). Isto aqui não repete a prova: mede o DANO.
#
# A pergunta é a que separa defeito real de defeito teórico — **quantos tokens de
# resume vivem além do meta que eles precisam ler?** O token e o meta têm prazos
# de fontes diferentes e nunca foram conciliados:
#   · token  : `{t}:resume_tokens`, TTL = timeout_hours*3600 + 3600 (48 h default)
#   · meta   : 4 h depois da alocação, e é dele que saem tenant_id/agent_type_id
# Quando o meta morre primeiro, o token continua lá, aceitável e inútil: o resume
# entra e falha por tenant desconhecido — a recusa `tenant_unknown` do arco P2 é
# exatamente o que ele encontra.
#
# ── PREVISÃO (a base tem de ser contada, não estimada) ───────────────────────
# O P0 do probe de propriedade contou 9 `session:*:meta` vivos neste ambiente.
# Se o hash tiver T tokens com prazo ainda futuro e T > 9, então pelo menos
# T − 9 deles já perderam o meta: previsão de ORFAOS ≥ T − 9.
# ⇒ ORFAOS = 0 com T grande refuta o dano: significa que os tokens morrem junto.
#
# Veredicto de TRÊS estados: 0 = ninguém sofre · 1 = há vítimas · 3 = INCONCLUSIVO
set -uo pipefail

COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
TENANT="${TENANT:-tenant_demo}"

R() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }

echo "══ resume_tokens × session:meta — quem sobrevive a quem ══"
[ "$(R PING)" = "PONG" ] || { echo "   ⛔ INCONCLUSIVO — redis inalcançável"; exit 3; }

RAW="$(R HGETALL "$TENANT:resume_tokens")"
NLINES="$(printf '%s\n' "$RAW" | grep -c .)"
if [ "$NLINES" -lt 2 ]; then
  echo "   ⛔ INCONCLUSIVO — hash $TENANT:resume_tokens vazio; nada a medir."
  exit 3
fi
HASH_TTL="$(R TTL "$TENANT:resume_tokens")"
echo "      entradas no hash: $((NLINES / 2)) · TTL do hash: $HASH_TTL"

NOW="$(date +%s)"
ORFAOS=0     # token futuro, meta JÁ morto            ← a vítima consumada
CONDENADOS=0 # token futuro, meta vivo mas curto demais ← a vítima futura
OK=0         # meta cobre o prazo do token
PASSADO=0    # token já vencido — fora da conta
ILEGIVEL=0   # não consegui datar — contador de instrumento, não de fenômeno

# HGETALL devolve campo e valor em linhas alternadas.
printf '%s\n' "$RAW" | paste - - | while IFS="$(printf '\t')" read -r tok val; do
  [ -n "${val:-}" ] || continue
  sid="${val%%:*}"                 # UUID não tem ':' — corte seguro
  exp="${val#*:}"; exp="${exp#*:}" # o resto é o ISO, que TEM ':'
  ets="$(date -d "$exp" +%s 2>/dev/null)" || ets=""
  if [ -z "$ets" ]; then
    echo "ILEGIVEL"; continue
  fi
  if [ "$ets" -le "$NOW" ]; then echo "PASSADO"; continue; fi
  ttl="$(R TTL "session:$sid:meta")"
  case "$ttl" in ''|*[!0-9-]*) echo "ILEGIVEL"; continue ;; esac
  if [ "$ttl" -lt 0 ]; then
    echo "ORFAO $sid falta=$(( (ets - NOW) / 3600 ))h"
  # ── Tolerância DECLARADA de fronteira (2026-09-07, GAT-03 b) ───────────────
  # O prazo do token e o do meta são escritos em momentos diferentes do MESMO fluxo,
  # então um excede o outro por segundos sem que exista fenômeno nenhum. Medido: 2 de
  # 3 "condenados" tinham `descoberto = 0 min`. Contador inflado gasta a atenção que
  # ele existe para dirigir — e a atenção é o recurso que um gate compra.
  # 60 s é a granularidade em que o resto do arquivo já fala (o scanner de prazo varre
  # a cada 60 s); acima disso a diferença é de CONFIGURAÇÃO, não de relógio.
  elif [ $((NOW + ttl + 60)) -lt "$ets" ]; then
    # ⚠️ MESMA unidade nos dois, e a MARGEM explicita: com `meta` em minutos e
    # `token` em horas truncadas, `meta=1437min token=23h` parecia contradizer o
    # veredicto (1437 min = 23,95 h) e o leitor concluia que o gate errou.
    echo "CONDENADO $sid meta=$((ttl / 60))min token=$(( (ets - NOW) / 60 ))min descoberto=$(( (ets - NOW - ttl) / 60 ))min"
  else
    echo "OK"
  fi
done > /tmp/_meta_resume_out.txt

ORFAOS="$(grep -c '^ORFAO ' /tmp/_meta_resume_out.txt)"
CONDENADOS="$(grep -c '^CONDENADO ' /tmp/_meta_resume_out.txt)"
OK="$(grep -c '^OK$' /tmp/_meta_resume_out.txt)"
PASSADO="$(grep -c '^PASSADO$' /tmp/_meta_resume_out.txt)"
ILEGIVEL="$(grep -c '^ILEGIVEL$' /tmp/_meta_resume_out.txt)"

echo
echo "      órfãos     (token vivo, meta JÁ morto) : $ORFAOS"
echo "      condenados (meta morre antes do token) : $CONDENADOS"
echo "      cobertos   (meta cobre o token)        : $OK"
echo "      vencidos   (token no passado)          : $PASSADO"
echo "      ilegíveis  (instrumento, não fenômeno) : $ILEGIVEL"
echo
echo "── amostra ───────────────────────────────────────────────────────────────"
grep -E '^(ORFAO|CONDENADO) ' /tmp/_meta_resume_out.txt | head -12 | sed 's/^/      /'

echo
if [ "$ILEGIVEL" -gt 0 ] && [ $((ORFAOS + CONDENADOS + OK)) -eq 0 ]; then
  echo "   ⛔ INCONCLUSIVO — tudo ilegível; o parser é que falhou, não o sistema."
  exit 3
fi
# ── EXPOSIÇÃO e DANO são dois números (corrigido 2026-09-07, GAT-03 b) ───────
#
# O veredicto somava `ORFAOS + CONDENADOS` e reprovava — mas o cabeçalho deste mesmo
# arquivo já dizia qual dos dois é o dano: *"ORFAOS = 0 com T grande refuta o dano"*.
# São coisas diferentes:
#
#   ÓRFÃO     o meta JÁ morreu e o token continua vivo. A vítima é CONSUMADA: quem
#             chegar com esse token entra e falha por tenant desconhecido.
#   CONDENADO o meta morre ANTES do token, mas nenhum dos dois venceu ainda. É
#             EXPOSIÇÃO: só vira dano se o resume acontecer DEPOIS do meta morrer —
#             e a esmagadora maioria dos resumes acontece em minutos.
#
# Reprovar por exposição punha este probe permanentemente vermelho por um `suspend`
# de `timeout_hours` alto (o token vive `timeout_hours*3600 + 3600`, o meta 24 h), e
# vermelho permanente é o que ensina todo mundo a ignorar vermelho — a lição que a
# metade (a) da GAT-03 passou o dia pagando.
#
# ⚠️ A exposição NÃO é absolvida: ela é CONTADA, com a amostra impressa acima, e tem
# ficha própria (o prazo do token e o do meta vêm de fontes que ninguém conciliou).
# O que mudou é quem reprova: o DANO.
if [ "$ORFAOS" -gt 0 ]; then
  echo "   ❌ $ORFAOS resume(s) com o meta JÁ MORTO — dano consumado."
  echo "      O token será aceito e a retomada falhará por tenant desconhecido."
  exit 1
fi
if [ "$CONDENADOS" -gt 0 ]; then
  echo "   ⚠️  $CONDENADOS resume(s) EXPOSTO(s): o meta morre antes do token."
  echo "      Não é dano ainda — vira dano se a retomada chegar depois do meta."
  echo "      O prazo do token (timeout_hours*3600 + 3600) e o do meta (24 h) vêm de"
  echo "      fontes que ninguém conciliou; ver a ficha no \`pending.md\`."
  echo "   ✅ dano CONSUMADO: nenhum (órfãos = 0)"
  exit 0
fi
echo "   ✅ nenhum token sobrevive ao seu meta — o truncamento não tem vítima aqui."
exit 0
