#!/usr/bin/env bash
# probe_slot_required_params.sh — slot `current` rodando skill cujo parâmetro
# obrigatório de deploy ninguém preencheu.
#
# ── Por que existe, se o promote já recusa ──────────────────────────────────
#
# O portão do promote (`lib/required-config.ts`) pega quem tenta ENTRAR sem o
# parâmetro. Ele não pega — e não tem como pegar — quem **virou** não-conforme
# parado: basta alguém acrescentar um `required: true` ao `config_params` de um
# skill já deployado, e todos os slots existentes daquele skill passam a exigir
# algo que ninguém gravou, sem que promote nenhum rode para ser recusado.
#
# São dois trabalhos diferentes sobre a mesma regra:
#   promote → quem entra      ·      este probe → quem já está dentro
#
# ── O que a violação CAUSA, e por que ela não fica vermelha sozinha ─────────
#
# O bridge injeta o `config_json` do slot como `$.config.*` no launch. Faltando a
# chave, a referência não resolve e o step que a usa falha no PRIMEIRO contato —
# o `on_failure` manda tudo para a saída de emergência. No `skill_navegacao_v1`
# isso é um pool que sobe, aparece saudável em toda métrica de capacidade, aceita
# contato e escala 100% ao humano. Não é queda: é um pool que parou de fazer o
# trabalho dele. Nenhum alarme existente cobre isso.
#
# ── ⚠️ O QUE DECIDE É O SNAPSHOT, NÃO A DECLARAÇÃO (corrigido em 2026-09-08) ─
#
# A v1 deste probe comparava `skills.config_params` com `PoolSkillSlot.config_json`
# e parava aí. Medido no mesmo dia, minutos depois de entrar em vigor: ele acusou
# `demo_ia` e `demo_llm_ia` de estarem quebrados, e os dois estavam FUNCIONANDO —
# o `yaml_snapshot` do slot `current` deles ainda era o anterior à parametrização,
# com o `form_id` LITERAL. A declaração tinha mudado; o deploy, não.
#
# Publicar um skill NÃO muda o que roda (o bridge executa o snapshot do slot), e
# por isso "o skill exige X" e "o deploy vivo precisa de X" são fatos diferentes.
# Um probe que os confunde publica um defeito que não existe — e a mensagem dele
# afirmava uma consequência ("falha no PRIMEIRO contato") que era falsa para
# aqueles dois pools.
#
# Hoje a condição tem DUAS metades: o skill declara o parâmetro como obrigatório
# **E** o snapshot que está rodando de fato referencia `$.config.<chave>`. A
# segunda metade é o que amarra o veredicto ao que executa.
#
# O estado intermediário (declaração nova, deploy velho) não some do relatório —
# ele vira uma linha de INFORMAÇÃO, porque é trabalho pendente e não defeito:
# quem promover daqui em diante é recusado pelo portão do `set-next`/`promote`
# (`lib/required-config.ts`) até preencher.
#
# É a § Postura de Engenharia por inteiro: *um instrumento pode ser falseável,
# ramificado e honesto — e ainda medir a proposição ERRADA.*
#
# ── Testemunha obrigatória ──────────────────────────────────────────────────
#
# Zero violações só é VERDE se alguém declarar alguma coisa. Se nenhum skill
# declara `config_params`, o probe não está medindo conformidade — está medindo o
# vazio, e é indistinguível de junção quebrada. ⚠️ Isso não é hipótese: em
# 2026-09-08 esta medição saiu "0 declarantes" por juntar `skills.id` (UUID) com
# `pool_skill_slots.skill_id` (slug). A tabela tem as DUAS colunas. A junção certa
# é `skills.skill_id`, e a testemunha é o que separa um caso do outro.
#
# Uso:  bash infra/test/probe_slot_required_params.sh
# Pré:  postgres do registry no ar. Read-only: não escreve nada.
#
# SAÍDA: 0 = conforme · 1 = há slot rodando sem o parâmetro · 2 = INCONCLUSIVO
set -uo pipefail

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo não encontrada"; exit 2; }

DC="${DC:-docker compose -f docker-compose.demo.yml}"
DB="${REGISTRY_DB:-plughub_registry}"
PGUSER_="${PGUSER_:-plughub}"

psqlq() { $DC exec -T postgres psql -U "$PGUSER_" -d "$DB" -t -A -F'|' -c "$1" < /dev/null 2>&1 | tr -d '\r'; }

echo "══ parâmetro de deploy obrigatório × slot que está rodando ══"

PING="$(psqlq 'SELECT 1;' | head -1)"
if [ "$PING" != "1" ]; then
  echo "   ⛔ INCONCLUSIVO — postgres do registry não respondeu ('$PING')"
  exit 2
fi

# ── Testemunhas, antes do veredicto ─────────────────────────────────────────
N_SLOTS="$(psqlq "SELECT count(*) FROM pool_skill_slots WHERE slot='current';" | head -1)"
N_DECL="$(psqlq "SELECT count(*) FROM skills
                  WHERE config_params IS NOT NULL
                    AND config_params::text NOT IN ('null','[]');" | head -1)"
case "${N_SLOTS:-x}" in ''|*[!0-9]*) echo "   ⛔ INCONCLUSIVO — contagem de slots não numérica ('$N_SLOTS')"; exit 2 ;; esac
case "${N_DECL:-x}"  in ''|*[!0-9]*) echo "   ⛔ INCONCLUSIVO — contagem de declarantes não numérica ('$N_DECL')"; exit 2 ;; esac

echo "      slots \`current\`: $N_SLOTS · skills que DECLARAM parâmetro: $N_DECL"

if [ "$N_SLOTS" -eq 0 ]; then
  echo "   ⛔ INCONCLUSIVO — nenhum slot \`current\`; não há deploy para auditar."
  exit 2
fi
if [ "$N_DECL" -eq 0 ]; then
  echo "   ⛔ INCONCLUSIVO — NENHUM skill declara \`config_params\`."
  echo "      Isto não é conformidade: é ausência de população. Ou ninguém declara"
  echo "      (e a regra não vigora), ou a leitura está quebrada — foi exatamente"
  echo "      assim que uma junção por \`skills.id\` (UUID) em vez de \`skills.skill_id\`"
  echo "      (slug) produziu um zero convincente em 2026-09-08."
  exit 2
fi

# ── O veredicto ─────────────────────────────────────────────────────────────
# ⚠️ A junção é por `skill_id` (slug) + `tenant_id`. `skills.id` é UUID e NÃO casa
# com `pool_skill_slots.skill_id` — junta sem erro e devolve zero linhas.
#
# `jsonb_array_elements` sobre o descritor; vazio = ausente, `null`, string só de
# espaço, `[]` ou `{}` — a mesma definição do portão do promote
# (`lib/required-config.ts`), e as duas têm de concordar. `0` e `false` NÃO são
# vazios (defeito de truthiness que este repositório cataloga).
VIOL="$(psqlq "
  SELECT s.pool_id || '|' || s.skill_id || '|' || (p.value->>'key')
    FROM pool_skill_slots s
    JOIN skills k
      ON k.skill_id = s.skill_id AND k.tenant_id = s.tenant_id
   CROSS JOIN LATERAL jsonb_array_elements(
      CASE jsonb_typeof(k.config_params::jsonb)
        WHEN 'array' THEN k.config_params::jsonb ELSE '[]'::jsonb END) AS p(value)
   WHERE s.slot = 'current'
     AND (p.value->>'required')::boolean IS TRUE
     -- A metade que amarra ao que RODA: o snapshot referencia \$.config.<chave>.
     -- Sem ela, um slot com snapshot anterior à parametrização (form_id literal,
     -- funcionando) era acusado de quebrado. Ver o cabeçalho.
     AND coalesce(s.yaml_snapshot::text, '') LIKE '%\$.config.' || (p.value->>'key') || '%'
     AND (
          NOT (coalesce(s.config_json::jsonb, '{}'::jsonb) ? (p.value->>'key'))
       OR jsonb_typeof(s.config_json::jsonb -> (p.value->>'key')) = 'null'
       OR (jsonb_typeof(s.config_json::jsonb -> (p.value->>'key')) = 'string'
           AND btrim(s.config_json::jsonb ->> (p.value->>'key')) = '')
       OR (jsonb_typeof(s.config_json::jsonb -> (p.value->>'key')) = 'array'
           AND jsonb_array_length(s.config_json::jsonb -> (p.value->>'key')) = 0)
       OR (jsonb_typeof(s.config_json::jsonb -> (p.value->>'key')) = 'object'
           AND (SELECT count(*) FROM jsonb_object_keys(
                  s.config_json::jsonb -> (p.value->>'key'))) = 0)
     )
   ORDER BY 1;")"

# Erro de SQL não pode virar "nenhuma violação": a saída do psql viraria uma
# mensagem, e `grep -c .` a contaria como linha. Reconhece e sai 2.
case "$VIOL" in
  *ERROR:*|*FATAL:*) echo "   ⛔ INCONCLUSIVO — a consulta falhou:"; printf '      %s\n' "$VIOL" | head -4; exit 2 ;;
esac

N_VIOL="$(printf '%s\n' "$VIOL" | grep -c . || true)"

# Declaração mais nova que o deploy: NÃO é defeito, é trabalho pendente. Contado e
# nomeado, nunca escondido — e nunca vermelho, porque o pool está rodando o
# snapshot antigo, que funciona. Quem tentar promover é recusado pelo portão.
PEND="$(psqlq "
  SELECT s.pool_id || ' · ' || s.skill_id || ' · ' || (p.value->>'key')
    FROM pool_skill_slots s
    JOIN skills k
      ON k.skill_id = s.skill_id AND k.tenant_id = s.tenant_id
   CROSS JOIN LATERAL jsonb_array_elements(
      CASE jsonb_typeof(k.config_params::jsonb)
        WHEN 'array' THEN k.config_params::jsonb ELSE '[]'::jsonb END) AS p(value)
   WHERE s.slot = 'current'
     AND (p.value->>'required')::boolean IS TRUE
     AND coalesce(s.yaml_snapshot::text, '') NOT LIKE '%\$.config.' || (p.value->>'key') || '%'
   ORDER BY 1;")"
case "$PEND" in *ERROR:*|*FATAL:*) PEND="" ;; esac
N_PEND="$(printf '%s\n' "$PEND" | grep -c . || true)"
if [ "${N_PEND:-0}" -gt 0 ]; then
  echo
  echo "── informação (não é defeito) ──────────────────────────────────────────────"
  echo "   $N_PEND declaração(ões) mais NOVA(s) que o deploy — o slot roda um snapshot"
  echo "   anterior à parametrização, que funciona. Vira defeito só se alguém promover"
  echo "   sem preencher, e aí o portão do promote recusa antes."
  printf '%s\n' "$PEND" | sed 's/^/      /'
fi

echo
echo "── veredicto ───────────────────────────────────────────────────────────────"
if [ "${N_VIOL:-0}" -eq 0 ]; then
  echo "   ✅ nenhum slot \`current\` roda skill com parâmetro obrigatório em branco."
  echo "      Conferido contra $N_DECL skill(s) que declaram — a testemunha que separa"
  echo "      isto de uma leitura que não achou nada."
  exit 0
fi

echo "   ❌ $N_VIOL parâmetro(s) obrigatório(s) em branco num slot que está RODANDO:"
printf '%s\n' "$VIOL" | while IFS='|' read -r pool skill chave; do
  [ -n "$pool" ] && echo "      $pool · $skill · falta \`$chave\`"
done
echo
echo "   O pool sobe, parece deployado e falha no PRIMEIRO contato: a referência"
echo "   \`\$.config.<chave>\` não resolve e o step cai no \`on_failure\`. Preencher"
echo "   em Flow › Deploy e promover — ou, se o parâmetro não devia ser obrigatório,"
echo "   corrigir o \`config_params\` do skill, que é onde a exigência foi declarada."
exit 1
