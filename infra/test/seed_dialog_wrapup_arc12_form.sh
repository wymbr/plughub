#!/usr/bin/env bash
#
# seed_dialog_wrapup_arc12_form.sh — fatia 3: form de wrap-up COM captura Arc 12.
#
# Irmão do `seed_dialog_wrapup_form.sh` (que segue válido e sem captura). Este
# acrescenta as duas formas que a §D2 define, e existe para haver **dado real** em
# `agent_business_events` — que até 2026-08-03 tinha UMA linha, de seed, e nenhum
# produtor no sistema inteiro.
#
#   fcr      → capture.kind = "scored"   → categoria `{pool}.wrapup.fcr`, value 0|1
#                                          ⇒ `avg_value` do summary É a taxa de FCR
#   servico  → capture.kind = "nominal"  → categoria `{pool}.wrapup.servico.{opção}`,
#                                          value 1 ⇒ `count` por serviço. Multi-select
#                                          vira N eventos.
#
# A folha nominal sai de `options[].value` — lista controlada, versionada e
# UI-editável (§D3). Sem isso `troca_titularidade` × `troca_de_titularidade` viram
# duas séries que jamais reconciliam, e série histórica errada é irreversível.
#
# `resumo` e `proximos_passos` seguem SEM capture de propósito: prosa não cabe em
# `agent_business_events` (lá `value` é numérico e o nominal vive na categoria) e
# continua indo para as colunas do segmento. É a §D6 — os dois sinks coexistem —
# ficando decidida pela AUSÊNCIA de capture, não por convenção a lembrar.
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo \
#     ./infra/test/seed_dialog_wrapup_arc12_form.sh
set -euo pipefail

DIALOG_API="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_wrapup_arc12_v1"

echo "→ Criando DialogForm '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT})"

read -r -d '' BODY <<'JSON' || true
{
  "form_id": "dialog_wrapup_arc12_v1",
  "name": "Wrap-up do atendimento (com métricas)",
  "description": "Disposição + FCR (pontuável) + serviços executados (nominal, multi). A classificação vira outcome do segmento; FCR e serviço viram agent_business_events; resumo e próximos passos ficam em prosa no segmento.",
  "default_locale": "pt-BR",
  "locales": ["pt-BR"],
  "tags": ["wrapup", "arc12"],
  "nodes": [
    {
      "id": "intro",
      "kind": "statement",
      "text": { "pt-BR": "Revise a conversa ao lado e registre a disposição do seu atendimento." }
    },
    {
      "id": "classificacao",
      "kind": "question",
      "prompt": { "pt-BR": "Como este atendimento terminou?" },
      "interaction": "button",
      "output_key": "classificacao",
      "timeout_s": -1,
      "options": [
        { "id": "resolvido", "label": { "pt-BR": "Resolvido" }, "value": "resolvido" },
        { "id": "pendente",  "label": { "pt-BR": "Pendente" },  "value": "pendente" },
        { "id": "escalado",  "label": { "pt-BR": "Escalado" },  "value": "escalado" },
        { "id": "cancelado", "label": { "pt-BR": "Cancelado pelo cliente" }, "value": "cancelado" }
      ]
    },
    {
      "id": "fcr",
      "kind": "question",
      "prompt": { "pt-BR": "Resolvido no primeiro contato?" },
      "interaction": "button",
      "output_key": "fcr",
      "timeout_s": -1,
      "capture": { "metric": "fcr", "kind": "scored" },
      "options": [
        { "id": "sim", "label": { "pt-BR": "Sim" }, "value": "sim", "capture": { "value": 1 } },
        { "id": "nao", "label": { "pt-BR": "Não" }, "value": "nao", "capture": { "value": 0 } }
      ]
    },
    {
      "id": "servico",
      "kind": "question",
      "prompt": { "pt-BR": "Quais serviços você executou?" },
      "interaction": "checklist",
      "output_key": "servico",
      "timeout_s": -1,
      "capture": { "metric": "servico", "kind": "nominal" },
      "options": [
        { "id": "segunda_via",         "label": { "pt-BR": "Segunda via de fatura" }, "value": "segunda_via" },
        { "id": "troca_titularidade",  "label": { "pt-BR": "Troca de titularidade" }, "value": "troca_titularidade" },
        { "id": "alteracao_plano",     "label": { "pt-BR": "Alteração de plano" },    "value": "alteracao_plano" },
        { "id": "cancelamento",        "label": { "pt-BR": "Cancelamento" },          "value": "cancelamento" }
      ]
    },
    {
      "id": "resumo",
      "kind": "question",
      "prompt": { "pt-BR": "Resumo do atendimento" },
      "interaction": "text",
      "output_key": "resumo",
      "timeout_s": -1
    },
    {
      "id": "proximos_passos",
      "kind": "question",
      "prompt": { "pt-BR": "Próximos passos (opcional)" },
      "interaction": "text",
      "output_key": "proximos_passos",
      "timeout_s": -1
    }
  ]
}
JSON

curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms" \
  -H 'Content-Type: application/json' \
  -H "X-Tenant-ID: ${TENANT}" \
  -d "${BODY}" >/dev/null || {
    echo "  (form já existe — seguindo para o publish)"; }
echo "✓ DialogForm criado/atualizado: ${FORM_ID}"

echo "→ Publicando"
curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms/${FORM_ID}/publish" \
  -H "X-Tenant-ID: ${TENANT}" >/dev/null
echo "✓ DialogForm publicado: ${FORM_ID}"
