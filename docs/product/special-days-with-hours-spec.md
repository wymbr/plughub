# Special Days com horário (holiday set → dias especiais com override de horário)

> Spec fechada (2026-06-28). Follow-up #2 do lote de calendários. Generaliza o conjunto de feriados
> (hoje só "fechado o dia todo") para **dias especiais** que podem **fechar OU definir horário custom**
> (com intervalos), tornando o set reutilizável um template de dias especiais — não só de feriados.

## 1. Achado-chave: backend, schema e engine JÁ suportam

Não é mudança de modelo. A capacidade existe ponta-a-ponta, falta só a UI expor:

- **Schema** (`@plughub/schemas/calendar.ts`): `HolidaySchema` já tem
  `override_slots: z.array(TimeSlotSchema).nullable().default(null)` — `null` = fechado o dia todo;
  array = horário custom.
- **Engine** (`calendar-api/engine.py` `_resolve_date`): já lê `holiday["override_slots"]` —
  `None` → `("holiday", [])` (fechado); array → `("holiday", override_slots)` (aberto nesses horários).
- **Persistência** (`calendar-api/db.py`): `holidays` é JSONB; grava o que vier (inclui `override_slots`).
- **Router** (`HolidaySetCreate/Update.holidays: list[dict]`): pass-through, sem perda.

Ou seja: criar um holiday com `override_slots` via API **já funciona hoje** (engine respeita). O gap é só
o editor de UI, que coleta apenas `{date, name}`.

## 2. Decisão de nomenclatura

Renomear a superfície de "Holidays/Feriados" para **"Special Days / Dias Especiais"** (i18n value strings;
identificadores de código permanecem `holiday*`/`holiday_set*` — **não** renomear schema/rotas/engine, que
são contrato estável). O conceito de "feriado fechado" vira um caso particular de dia especial
(`override_slots: null`). Mantém retrocompatibilidade total.

> Identificadores em inglês intactos: `HolidaySet`, `holiday_set_ids`, `/v1/holiday-sets`, `override_slots`.
> Só muda o **texto exibido** (en/pt-BR) e rótulos de aba.

## 3. Escopo da implementação (frontend-only)

`packages/platform-ui/src/modules/calendars/CalendarsPage.tsx`:

1. **`HolidayEntry` (interface local)**: adicionar `override_slots?: TimeInterval[] | null` (espelha o schema).
2. **`HolidaysEditor`**: replicar o padrão do `ExceptionsEditor` (que já faz isso):
   - no add-row, toggle **"Fechado o dia todo"** vs **"Horário custom"** + editor de intervalos (`open`/`close`,
     `+ intervalo`, remover, máx. 4) — idêntico ao ExceptionsEditor;
   - no `pendingEntry()`/`add()`/`flushPending()`, montar `override_slots: closed ? null : [...slots]`
     (o contrato de flush já existe — só estende o objeto);
   - **linha existente**: mostrar o horário quando houver (`override_slots.map('HH:MM–HH:MM')`) ou
     "fechado o dia todo"; manter o toggle `↺ todo ano` / `one-off` já implementado;
   - **editar override de uma linha existente**: permitir abrir a linha para alternar fechado↔horário
     (opcional fase 2; mínimo viável = definir no add e re-adicionar).
3. **i18n** (`calendars.json` en + pt-BR): renomear labels visíveis para "Special Days/Dias Especiais";
   reusar as chaves de horário do ExceptionsEditor (`exceptions.closedAllDay`, `customHours`, etc.) ou criar
   equivalentes `holidaySet.*`.
4. **Aba** (`CalendarsPage` `tabs.holidaySets`) e cabeçalhos: "Dias Especiais".

Nada no backend/engine/schema. Nenhuma migração (linhas antigas têm `override_slots` ausente → tratado como
`null` = fechado, comportamento idêntico ao atual).

## 4. Invariantes
- Inglês no código (`override_slots`, `holiday*`); PT só em i18n values.
- `override_slots: null` ≡ feriado fechado (retrocompat).
- Holiday set permanece **referência viva** (engine resolve por id); editar o set propaga aos calendários.
- Reusar o componente/UX de intervalos do `ExceptionsEditor` (sem duplicar lógica de slot divergente).

## 5. Fora de escopo
- Renomear identificadores de código/rotas/schema (contrato estável).
- "Exceções" do calendário continuam existindo (override **por-calendário, data única**); Special Days é a
  camada **reutilizável**. Os dois coexistem por design (um é template compartilhável, outro é pontual).
- Edição inline avançada de override em linha existente (pode ser fase 2; mínimo = no add-row).

## 6. Teste
- Criar Special Day com horário 08:00–12:00 num set → vincular a um calendário → `get_open_status` às 10h =
  `holiday`/aberto nos slots, fechado fora; às 15h = fechado. (engine já coberto por `test_engine.py`;
  adicionar caso com `override_slots` no holiday se ainda não houver.)
- UI: digitar dia especial com horário e **Save direto sem `+`** → persiste (flush).
