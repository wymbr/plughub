# `infra/scheduler/` — agendas declarativas

Cada `*.json` aqui é **uma Agenda** do `scheduler-api`, provisionada no boot pelo job
`agenda-seed` (`infra/seed/seed_agendas.py`) **pela API oficial** (`POST /v1/agendas`) e
**seed-if-absent**: existindo a agenda, o DB vence e o arquivo não a toca.

Existe porque, até a `VOZ-27`, agenda nenhuma nascia de arquivo — toda agenda vinha de
uma chamada REST feita à mão, então **instalação nova subia sem vigilância periódica
nenhuma**, e a ausência não aparecia em lugar algum. É o mesmo motivo (e o mesmo formato)
do `infra/dialog/*.json` semeado pelo `dialog-seed`.

## Formato

Os campos são os do `POST /v1/agendas` (`CreateAgendaBody`), mais um:

| campo | o que é |
|---|---|
| `seed_id` | **chave de idempotência**, gravada em `payload.seed_id`. Não é campo da API: o seed o move para dentro do `payload` antes de criar. |
| `name` · `target_pool_id` · `timezone` · `calendar_id` | como na API. O alvo é sempre um **POOL** (invariante S4), nunca um skill. |
| `schedule` · `validity` · `misfire_policy` | como na API (`once` / `recurring` com `times[]` no fuso da agenda). |
| `payload` | vira o `context` do trigger do webhook — é de lá que o skill lê `@ctx.*`. |

## Precedência, e como DESLIGAR uma agenda semeada

- **existe agenda com este `seed_id`** → não toca, e diz que não tocou (inclusive se estiver
  `paused` ou `cancelled`: pausar e cancelar são decisões do operador, e o seed as respeita);
- **não existe** → cria;
- `AGENDA_SEED_RECONCILE=true` → o arquivo vence, atualizando a agenda existente (`PATCH`).

⚠️ **Para desligar, PAUSE ou CANCELE — não apague.** Apagar remove a linha, e o seed do boot
seguinte a recria, porque "apagada de propósito" e "nunca semeada" ficam indistinguíveis.
É a mesma armadilha que o `dialog-seed` resolve lendo o `deleted_at` do form arquivado; aqui
o equivalente é o `status`, que o seed não sobrescreve.

## O que está declarado hoje

| arquivo | o que aciona | quando |
|---|---|---|
| `speech_check_default.json` | pool `speech_check_trigger` → verificação ativa do caminho de fala (`VOZ-23`) **sem perfil**, isto é, a config de fala do tenant | diária, 03:30 America/Sao_Paulo |

**Perfil de fala novo NÃO ganha agenda sozinho.** O grão é uma agenda por perfil: para
verificar um perfil periodicamente, declare outro arquivo aqui (com `payload.speech_profile_id`)
ou crie a agenda em *Configuração → Agendas*. A tela da verificação (aba WebRTC) mostra, por
perfil, se há agenda — a ausência é dita, nunca deduzida como cobertura.
