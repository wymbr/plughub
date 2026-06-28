# Calendários — Consolidação de UI, Clone-from-Existing e Disparo por Calendário

> Spec fechada (2026-06-28). Fecha os dois itens levantados pelo usuário:
> **(2)** duplicação de UI de calendários em `configurations`; **(1)** disparo de avaliação por calendário
> (combo de calendário da campanha vem vazio). Inclui o atalho de criação **clone-from-existing**.

## 1. Diagnóstico (o que realmente existe hoje)

A calendar-api (porta 3700) tem três entidades:

| Entidade | Escopo (chaves) | Papel |
|---|---|---|
| `holiday_set` | `organization_id` (NOT NULL) + `tenant_id` (nullable) | lista reutilizável de feriados (date+name, `MM-DD` recorrente) |
| `calendar` | `organization_id` (NOT NULL) + `tenant_id` (nullable) | `weekly_schedule[]` inline + `holiday_set_ids[]` (refs) + exceções |
| `association` | `tenant_id` (NOT NULL) — **sem** organization_id | pool/entidade → calendário (priority, operator) |

**Resolução de feriados é referência viva, não cópia.** O engine resolve `is_open(associations, holidays, when)`
buscando os holiday sets por id **na hora da avaliação**. Editar um holiday set propaga automaticamente a todos
os calendários que o referenciam via `holiday_set_ids`.

**`weekly_schedule` é inline (cópia) em cada calendário** — não há entidade de "template de horário".

### As duas UIs NÃO são template×instância — são CRUD redundante sobre as mesmas tabelas

| | `/config/calendars` (`CalendarsPage`) | `Platform → Calendars` (`CalendarManager`) |
|---|---|---|
| entidades | calendars + holiday sets (as mesmas) | calendars + holiday sets (as mesmas) |
| editor de calendário | weekly_schedule **+ seleção de `holiday_set_ids`** + detecção de conflito + link p/ holiday sets | weekly_schedule, mas **fixa `holiday_set_ids: []`** (não vincula feriados) |
| `organization_id` | `VITE_CALENDAR_ORG_ID ?? 'org-default'` | **`tenantId`** ('tenant_demo') ← bug de escopo |

**Raiz do item 2:** a aba Platform usa o `tenant_id` como `organization_id`. Como o list filtra por
`organization_id`, as duas telas leem/gravam em organizações distintas → parecem "duplicadas e dessincronizadas".
Não é um split de templates; é a mesma feature implementada duas vezes, e a versão do Platform é a mais fraca
(editor incompleto + org errado).

**Raiz do item 1:** `useCalendarOptions` na `CampaignsPage` chama `GET /v1/calendars?tenant_id=…`
**sem `organization_id`** → 422 → `.catch` engole → combo vazio.

## 2. Decisões fechadas

### D1 — escopo = `organization_id` (canônico) **E** `tenant_id` (ambos explícitos)
Hierarquia real do modelo (confirmada): **`installation_id` → `organization_id` → `tenant_id`**
(`InstallationContextSchema = {installation_id, organization_id}`; defaults calendar-api
`install-local`/`org-default`). `tenant_id` é a chave de escopo **universal** (toda tabela da plataforma);
`organization_id` é o nível **acima** do tenant, presente só onde há compartilhamento cross-tenant
(calendar-api, workflow-api). A dimensão de **site/cluster é `installation_id`**, não o tenant.

Logo, **não se troca tenant por org** — passam-se **os dois explícitos**. O `organization_id` vem da fonte
canônica de config (`VITE_CALENDAR_ORG_ID ?? 'org-default'`, espelhando o default do InstallationContext);
`tenant_id` = tenant atual. O list (`tenant_id=$2 OR tenant_id IS NULL`) devolve org-wide **+** tenant-specific.
**Bug a evitar (era a raiz do item 2): nunca usar `tenant_id` no slot de `organization_id`** (mistura de níveis).
Quando existir um mapa tenant↔org de primeira classe, troca-se só a fonte do `organization_id`.

### D2 — UI única: `CalendarsPage` (`/config/calendars`) é a fonte de verdade
A `/config/calendars` **já tem 3 abas** (Calendários, Feriados, Associações) — é a UI completa. Logo, **não há
nada a "mover"**: a ação é **remover por completo a seção de calendários do `configurations/platform`**
(ambas as sub-abas `Calendars` + `Holiday List` do `CalendarManager`), deixando `/config/calendars` como casa
única. O nav do Platform perde o item Calendars (mantém os demais settings de installation/org, ex. timezone).
Nenhuma migração de dados: a fonte boa já está em `org-default`; o que a aba Platform gravou sob
`organization_id = tenant_demo` (bug) é órfão/descartável — confirmar se há algo a preservar antes de remover.

### D3 — Camada de reuso: feriados = referência viva (já existe); horário = clone (snapshot)
Não criamos entidade `calendar_template`. O "facilitar criação" é o **clone-from-existing**:
- no modal "Novo calendário", seletor **"começar a partir de [calendário ▾]"**;
- copia `weekly_schedule` (snapshot — editar o origem depois **não** propaga);
- copia `holiday_set_ids` (continuam **refs vivas** aos holiday sets compartilhados).

Justificativa: horário é específico por pool/uso; um "template vivo" de horário deslocaria silenciosamente
janelas de avaliação/SLA de vários calendários (footgun). Feriado é o oposto — muda junto pra todos, e por isso
já é referência. A opção A respeita essa separação sem backend novo. (Entidade `calendar_template` fica como
enhancement futuro só se surgir necessidade de biblioteca de horários versionada/compartilhada.)

### D4 — Disparo por calendário (item 1)
Backend **já está pronto** (`sampling.compute_expires_at`/`campaign_dispatch_open` →
calendar-api `/v1/calendar/business-deadline`; scanner T15 respeita a janela). O fix é só popular o combo:
`useCalendarOptions` passa `organization_id` (org canônico D1) + `tenant_id`.

## 3. Plano de implementação

1. **D1/Item 2 — escopo + consolidação (platform-ui):**
   - `CampaignsPage.useCalendarOptions`: `GET /v1/calendars?organization_id=${ORG}&tenant_id=${tenant}`
     (`ORG = import.meta.env.VITE_CALENDAR_ORG_ID ?? 'org-default'`); não engolir erro silenciosamente (log).
   - `ConfigPlataformaPage`/`CalendarManager`: **remover a seção de calendários inteira** (sub-abas
     `Calendars` + `Holiday List`) + tirar o item Calendars do nav do Platform (já coberto por
     `/config/calendars`, que tem Calendários+Feriados+Associações). Remover `CalendarManager` e
     `calendar-hooks` do config-plataforma se ficarem órfãos.
2. **D3 — clone-from-existing (`CalendarsPage` modal de novo calendário):**
   - estado `cloneFromId`; ao escolher, pré-preenche `weekly_schedule` + `holiday_set_ids` do calendário-fonte;
   - payload de create inalterado (snapshot já embutido). i18n do seletor.
3. **D4/Item 1 — validar disparo:** combo popula → seleciona → `evaluation_calendar_id` persiste →
   dispatcher abre na janela do calendário.
4. **Verificação + docs:** smoke (combo popula; criar via clone; campanha dispara na janela); CHANGELOG/TODO.

## 4. Invariantes a respeitar
- Inglês no código; PT só em i18n value strings.
- `organization_id` canônico vem de `VITE_CALENDAR_ORG_ID` — nunca usar `tenant_id` como org.
- Holiday set permanece referência viva (`holiday_set_ids`); clone copia só o `weekly_schedule`.
- Sem entidade nova; sem migração; `associations` seguem por `tenant_id`.

## 5. Fora de escopo
- Entidade `calendar_template` (enhancement futuro).
- Mapa org↔tenant de primeira classe (quando existir, troca a fonte de D1).
- Limpeza física dos registros órfãos sob `organization_id = tenant_demo` (confirmar com usuário).
