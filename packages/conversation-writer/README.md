# conversation-writer — PACOTE FÓSSIL (quarentena, 2026-08-03)

> **Não está deployado em lugar nenhum, e seu destino de escrita não existe.**
> Mantido no repositório em quarentena documentada — não apagado — pelo mesmo
> critério usado com a tabela `pools` fóssil: o erro fica visível e reversível.

## Por que fóssil

Medido em 2026-08-03 (`infra/test/probe_hygiene.sh`, bloco 2):

| Evidência | Resultado |
|---|---|
| Serviço em `docker-compose.demo.yml` | **não existe** |
| `Dockerfile` no pacote | **não existe** |
| Tabelas de destino (`transcripts`, `transcript_messages`) | **não existem** em `plughub_demo` |
| Leitor dessas tabelas no repo | **nenhum** — o único hit é este próprio pacote |
| Menção em `CLAUDE.md` § Repository Structure | **ausente** |
| Referência viva | só `ecosystem.config.js` (topologia PM2, anterior ao Docker) |

Ele consome tópicos que **estão vivos** (`conversations.inbound/outbound/events`,
`evaluation.events`) — o que o torna mais perigoso que o `clickhouse-consumer`:
se alguém o subir, ele começa a ler tráfego real e a criar tabelas paralelas via
o `DDL`/`migrate()` embutido em `postgres_writer.py`. Não haveria erro; haveria
uma segunda persistência de transcrição, divergente e sem leitor.

Os defaults confirmam a idade: `config_api_url: "http://localhost:3500"` —
3500 é a **analytics-api** na topologia atual; o config-api é 3600.

## O que o substituiu

A persistência durável de stream é o **`StreamPersister`** (session-replayer),
que grava `session_stream_events` no PostgreSQL a partir do `session_closed`.
A transcrição servida à UI vem da analytics-api
(`GET /analytics/v1/transcript/sessions/{id}`), sobre ClickHouse.

## Antes de reativar ou apagar

Reativar exige decidir o conflito com o `StreamPersister` — duas persistências
da mesma conversa, com esquemas diferentes, é o defeito, não a feature. Se a
decisão for apagar, apagar **junto** a entrada do `ecosystem.config.js`.

A suíte (`src/plughub_conversation_writer/tests/`) foi retirada do
`infra/test/report_suite_skips.sh`, onde saía como INCONCLUSIVO e fazia o
relatório inteiro terminar com código 2.
