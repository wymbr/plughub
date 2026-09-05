# `infra/dialog/` — DialogForms declarativos (seed-if-absent)

Cada arquivo é **um `DialogForm`** (o mesmo JSON que a `dialog-api` serve em
`GET /v1/dialog/forms/{form_id}?status=published`). O nome do arquivo é cosmético;
o que vale é o campo `form_id` dentro dele.

## Por que este diretório existe

Até 2026-08-07 nenhum DialogForm era semeado no boot: os formulários viviam **só** em
scripts ad-hoc (`infra/test/seed_dialog_*.sh`) rodados à mão. Ambiente que já tinha os
forms no Postgres continuava funcionando; ambiente com **banco novo** subia sem eles, e
os dois consumidores degradavam **em silêncio**:

- `agente_nps_v1` (`carregar_form` → `form_get` 404 → `on_failure: encerrar`) — o contato
  fecha sem NPS, sem erro visível ao cliente;
- `DialogFormRenderer` (`fetch(... ?status=published)` → 404 → `setForm(null)`) — o item de
  wrap-up é reivindicado mas o painel aparece **sem formulário**.

É o caso do princípio de engenharia *"um ambiente que só sobe porque já subiu antes não
está sendo verificado — está sendo lembrado"*. O `dialog-seed` fecha isso: instalação limpa
nasce com os forms.

## Precedência de provisionamento

**Seed-if-absent / DB-owned**, igual ao `RegistrySyncer`:

| Situação | O que o `dialog-seed` faz |
|---|---|
| Não há versão publicada do `form_id` | cria (`POST`) + publica (`POST …/publish`) |
| Já há versão publicada | **não toca** — o DB é a fonte de verdade (edição pela UI sobrevive a rebuild) |
| `DIALOG_SEED_RECONCILE=true` | o arquivo vence: `PUT` (novo draft) + publish |

Consequência que morde igual à dos skills: **editar um JSON daqui é no-op** num ambiente
que já tem o form publicado. Para o arquivo valer, ou se publica pela UI
(`/config/dialog-forms`), ou se roda o seeder com `DIALOG_SEED_RECONCILE=true`, ou se roda
o wrapper correspondente em `infra/test/seed_dialog_*.sh` (que sempre cria+publica uma
versão nova).

## Quem consome cada form

| Form | Consumidor |
|---|---|
| `dialog_nps_buttons` | `agente_nps_v1` (hook `on_contact_end` do `sac_ia`/`retencao_humano`), `skill_survey_outbound_v1` |
| `dialog_wrapup_v1` | `skill_wrapup_detached_v1` via `retencao_humano.on_human_end.context.dialog_form_id` |
| `dialog_wrapup_arc12_v1` | idem, variante com captura Arc 12 (FCR + serviços) — não referenciada pelo YAML por padrão |
| `dialog_wrapup_arvore_v1` | idem, variante com **taxonomia em árvore** (motivo em 3 níveis · serviços em 2 · `ask_when` com `prefix`) — **ainda não referenciada por pool nenhum**: é o artefato REAL em que os gates da árvore se ancoram, e o pré-requisito da F6 do `adr-dialog-tree-options`. Ligá-la a um pool muda o wrap-up que o agente vê, e por isso é decisão de operação, não de seed |
| `dialog_otp_possession` | `agente_portabilidade_intake_v1` (step-up de OTP via `dialog_runner`) |
| `dialog_formfill_demo` | `skill_formfill_demo_v1` (demo genérico do `DialogFormRenderer`) |
| `dialog_promocao_deploy` | `skill_gate_promocao_v1` (aprovação de promoção) |
| `dialog_survey_multi_v1` | `skill_survey_multi_v1` (dimension composta) |
| `dialog_nps_v1` | `dialog_runner` via delegate (veículo runner-especialista) |

## Como adicionar um form novo

1. Crie `infra/dialog/<form_id>.json`.
2. `docker compose -f docker-compose.demo.yml run --rm dialog-seed` (ou suba a stack).
3. Se ele for endereçado por um pool/skill, ligue o `dialog_form_id` onde for fato
   daquele pool (ex.: `PoolHookEntry.context`), nunca cravado no skill.
