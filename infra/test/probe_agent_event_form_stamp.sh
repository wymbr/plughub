#!/usr/bin/env bash
# probe_agent_event_form_stamp.sh
#
# D14 do `adr-dialog-tree-options` + S1 do `adr-deploy-time-content-snapshot`.
#
# PROPOSICAO: todo evento de negocio derivado de um DialogForm carrega, em `tags`,
# QUAL VOCABULARIO o descreve — `dialog_form_id` e (quando ha pin) `dialog_form_version`.
#
# Por que isto precisa de gate proprio, e nao cabe nos testes unitarios: o carimbo e
# posto no CALL SITE do emissor (`segment.ts`), nao dentro de `deriveAgentEvents`. Um
# refactor que reescreva a chamada perde a tag sem quebrar teste nenhum — e a perda e
# MUDA: a linha continua sendo gravada, so deixa de dizer de que arvore ela veio. Foi
# exatamente assim que a serie ficou misturada antes deste arco (medido 2026-09-05:
# `servico.troca_titularidade` da forma plana convivendo com
# `servico.cadastro.troca_titularidade` da forma em arvore, no mesmo pool e no mesmo dia).
#
# ⚠️ RAMOS, e por que o INCONCLUSIVO existe: sem evento no periodo, "todos carimbados"
# e verdade vazia — o teste passaria por AUSENCIA DE AMOSTRA, que e a familia do teste
# que nao pode reprovar. Entao ausencia de populacao e ramo proprio, nunca verde.
#
# A epoca (`STAMP_EPOCH`) existe porque o carimbo e forward-only: linha anterior a ele
# nao tem tag e nao ha migracao possivel (a forma vigente no passado nao e recuperavel —
# a tabela `pools` e atualizada no lugar e nao guarda historico de hook). Mesma forma do
# `SEGMENT_SLA_EPOCH`: corte declarado em data, nunca fallback que mistura duas fontes
# num numero so.
set -u

CH="${PLUGHUB_CLICKHOUSE_URL:-http://localhost:8123}"
CH_USER="${PLUGHUB_CLICKHOUSE_USER:-plughub}"
CH_PASS="${PLUGHUB_CLICKHOUSE_PASSWORD:-plughub}"
CH_DB="${PLUGHUB_CLICKHOUSE_DB:-plughub_demo}"
TENANT="${PLUGHUB_TENANT_ID:-tenant_demo}"

# Data em que o carimbo entrou em producao. Ver CHANGELOG 2026-09-05.
STAMP_EPOCH="${PLUGHUB_FORM_STAMP_EPOCH:-2026-09-05 19:30:00}"

FAIL=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=1; }
info() { printf '  \033[33m•\033[0m %s\n' "$1"; }

q() { curl -s "${CH}/?user=${CH_USER}&password=${CH_PASS}&database=${CH_DB}" -d "$1"; }

printf '\033[1mprobe_agent_event_form_stamp — o evento diz de que arvore ele veio\033[0m\n\n'

# ── Ramo A: ha populacao? (controle positivo — sem ele, tudo abaixo passa vazio) ──
TOTAL=$(q "SELECT count() FROM agent_business_events
          WHERE tenant_id='${TENANT}' AND emitted_at >= '${STAMP_EPOCH}'
            AND category_l3 IN ('motivo','servico')")
TOTAL=$(printf '%s' "$TOTAL" | tr -d '[:space:]')

printf '\033[1mA — ha evento derivado de DialogForm depois da epoca?\033[0m\n'
if [ -z "$TOTAL" ]; then
  bad "ClickHouse nao respondeu — INCONCLUSIVO, e inconclusivo NAO e verde"
  printf '\n\033[31m\033[1mINCONCLUSIVO\033[0m\n'
  exit 1
fi
if [ "$TOTAL" = "0" ]; then
  info "nenhum evento desde ${STAMP_EPOCH} — SEM AMOSTRA"
  info "rode um wrap-up de retencao e repita; verde por ausencia seria mentira"
  printf '\n\033[33m\033[1mINCONCLUSIVO\033[0m — sem populacao para julgar.\n'
  exit 1
fi
ok "${TOTAL} evento(s) na janela — ha o que julgar"

# ── Ramo B: todos carimbados com a FORMA ──────────────────────────────────────
SEM_FORM=$(q "SELECT count() FROM agent_business_events
              WHERE tenant_id='${TENANT}' AND emitted_at >= '${STAMP_EPOCH}'
                AND category_l3 IN ('motivo','servico')
                AND empty(tags['dialog_form_id'])")
SEM_FORM=$(printf '%s' "$SEM_FORM" | tr -d '[:space:]')

printf '\n\033[1mB — todo evento nomeia o vocabulario que o descreve\033[0m\n'
if [ "$SEM_FORM" = "0" ]; then
  ok "0 de ${TOTAL} sem \`tags['dialog_form_id']\`"
else
  bad "${SEM_FORM} de ${TOTAL} SEM a forma — a serie volta a ser ilegivel quando o pool trocar de formulario"
fi

# ── Ramo C: o PIN chegou (a versao, nao so o id) ──────────────────────────────
SEM_VER=$(q "SELECT count() FROM agent_business_events
             WHERE tenant_id='${TENANT}' AND emitted_at >= '${STAMP_EPOCH}'
               AND category_l3 IN ('motivo','servico')
               AND empty(tags['dialog_form_version'])")
SEM_VER=$(printf '%s' "$SEM_VER" | tr -d '[:space:]')

printf '\n\033[1mC — o pin de versao atravessou (delegate → submit → evento)\033[0m\n'
if [ "$SEM_VER" = "0" ]; then
  ok "0 de ${TOTAL} sem \`tags['dialog_form_version']\`"
else
  # Ausencia de versao e DEGRADACAO PREVISTA (dialog-api fora do ar no delegate),
  # nao defeito — por isso avisa em vez de reprovar. O que nao pode e o id faltar.
  info "${SEM_VER} de ${TOTAL} sem versao — pin ausente naquele delegate (dialog-api indisponivel?)"
  info "nao reprova: sem pin, cada leitura resolve a ultima publicada, que e o comportamento anterior"
fi

# ── Ramo D: a forma carimbada EXISTE na dialog-api naquela versao ─────────────
# Carimbo que aponta para documento inexistente e pior que carimbo nenhum: da a
# impressao de rastreabilidade. Confere o par (id, versao) mais recente.
printf '\n\033[1mD — o par (forma, versao) carimbado e resolvivel no store\033[0m\n'
PAR=$(q "SELECT concat(tags['dialog_form_id'], '|', tags['dialog_form_version'])
         FROM agent_business_events
         WHERE tenant_id='${TENANT}' AND emitted_at >= '${STAMP_EPOCH}'
           AND category_l3 IN ('motivo','servico')
           AND notEmpty(tags['dialog_form_id']) AND notEmpty(tags['dialog_form_version'])
         ORDER BY emitted_at DESC LIMIT 1")
PAR=$(printf '%s' "$PAR" | tr -d '[:space:]')
if [ -z "$PAR" ]; then
  info "nenhum par (forma, versao) na janela — ramo D sem amostra"
else
  FID=${PAR%%|*}
  VER=${PAR##*|}
  DIALOG="${PLUGHUB_DIALOG_API_URL:-http://localhost:3760}"
  CODE=$(curl -s -o /dev/null -w '%{http_code}' \
         "${DIALOG}/v1/dialog/forms/${FID}?version=${VER}" -H "X-Tenant-ID: ${TENANT}")
  if [ "$CODE" = "200" ]; then
    ok "${FID} v${VER} resolve na dialog-api (HTTP 200)"
  else
    bad "${FID} v${VER} NAO resolve (HTTP ${CODE}) — o carimbo aponta para documento que o store nao tem"
  fi
fi

printf '\n'
if [ "$FAIL" = "0" ]; then
  printf '\033[32m\033[1mVERDE\033[0m — todo evento de taxonomia diz de que arvore veio.\n'
else
  printf '\033[31m\033[1mVERMELHO\033[0m\n'
fi
exit "$FAIL"
