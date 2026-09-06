# Sentiment Tracking — da medição à tela

> **Movido do `CLAUDE.md` em 2026-09-05 (DOC-01).** O texto abaixo é o corpo integral da seção
> *Sentiment Tracking* daquele arquivo, que passara de 115 linhas e é documentação de arco, não
> invariante — pela regra de *Saúde do `CLAUDE.md`* ele pertence a `docs/`. O `CLAUDE.md` guarda o
> resumo e o link. **Nada foi resumido nem reescrito na mudança**; correções datadas seguem com a
> data em que foram medidas.

Este guia é **transversal**: a cadeia atravessa `skill-flow-engine` (resolve a referência),
`ai-gateway` (mede e emite), ContextStore (fonte canônica) e `mcp-server-plughub` + `platform-ui`
(leem e desenham). O detalhe do lado PRODUTOR — prompt, provider, invariantes do analisador — vive
em [`docs/arcos/ai-gateway.md`](../arcos/ai-gateway.md) § *Medição de sentimento*; este arquivo é o
mapa dos dois lados e das dívidas que sobraram.

---

## O estado, medido

> ✅ **A plataforma MEDE sentimento, e isso está provado de ponta a ponta (2026-08-24).**
> Duas metades, dois gates: `probe_sentiment_producer.sh` (contrato → analisador → três emissores,
> com **testemunha negativa**: chamada sem `customer_utterance` não pode escrever nada) e
> `gate_sentiment_engine_half.sh` (contato REAL: referência resolvida pelo engine → skill-flow-service
> → gateway → ctx + `sentiment_live`). Medição de referência: score `-0.50`, pool `sac_ia`.
>
> ⚠️ **O bloqueio de credencial que dominava esta seção CAIU** — a chave do demo foi reposta e o
> `/v1/health` responde 200/`ok`. O diagnóstico de 08-22 (*"124 `status_401`, todo step `reason` de
> todo skill caindo no `on_failure`"*) está **encerrado**, mas a causa dele merece registro: o
> `docker-compose.demo.yml` não tinha `env_file`, então o `.env.demo` **nunca era lido** e a chave
> vinha exportada da shell de quem subiu a stack. Estado de shell não é entrada declarada.
>
> **Contrato de 2026-08-23 — a plataforma passou a MEDIR.** O diagnóstico anterior desta
> seção descrevia o defeito e apontava `/inference` como o caminho a resgatar. A medição refutou a
> premissa: `/inference` isola a fala, mas entrega a `extract_context_from_response`
> (`context.py:53-64`), que é **contagem de palavras-chave em português** — e a rota não tem chamador
> algum. Os dois caminhos pareciam medir e nenhum media (`/v1/reason` lia `sentiment_score` do
> `output_schema`, que nenhum skill declara ⇒ sempre `0.0`).
>
> Desenho vigente, em três peças: **(1)** `ReasonStepSchema.customer_utterance` — referência
> (`$.` / `@ctx.`, **nunca literal**) ao texto do cliente, resolvida pelo engine e enviada nomeada em
> `ReasonRequest`; nomear é declarar ENTRADA, não pedir que o modelo dê a própria nota. **(2)**
> `sentiment_analyzer.py` — chamada dedicada (haiku) fora do turno, alimentando os três emissores que
> já existiam. **(3)** `sentiment_score: float | None`, onde **`None` = não medido** e o pipeline é
> pulado; publicar `0.0` faria toda sessão parecer medida-e-neutra. `tenant_id` passou a viajar nos
> dois chamadores (`engine-runner.ts`, `skill-flow-service`), injetado onde o tenant é conhecido — sem
> ele as chaves nasciam sem prefixo. O analisador **recusa** tenant vazio.
>
> **Declarado (2026-08-24):** `agente_fila_v1.responder_cliente` traz
> `customer_utterance: "$.pipeline_state.ultima_mensagem"` — **o único** step `reason` sobre fala de
> cliente no repositório (`skill_atendimento_sac_v1`, apesar da descrição *"via LLM"*, é todo
> menu/choice/notify). Enquanto for o único, sentimento só existe para contato que passou pela FILA.
>
> **Três defeitos que esta trilha revelou, e que não são de sentimento** (detalhe no `CHANGELOG.md`
> de 2026-08-24) — todos da família *valor plausível*, cada um mascarado pelo anterior:
> · a medição **nunca rodara**: o provider era buscado em `inference_engine.providers`, atributo
>   inexistente (é `_providers`), e o `getattr(..., {})` fazia defeito de fiação sair pela porta de
>   "ambiente sem chave". Hoje: `app.state.llm_providers` + `main.sentiment_provider()`, que separa
>   os dois motivos;
> · **ordem dos emissores**: Kafka vinha antes das escritas locais e `producer.send` BLOQUEIA (não
>   levanta) com broker inalcançável — o score ficava ilegível por 40 s. Hoje: Redis primeiro, Kafka
>   por último sob `wait_for` de 5 s;
> · `session:{id}:meta` é **String (JSON)**, e o ai-gateway a lia com `HGET` em duas cópias ⇒
>   `WRONGTYPE` ⇒ toda medição de contato real agregada sob `unknown`. Hoje: helper único
>   `sentiment_emitter.resolve_session_pool_id`, com quatro ramos de saída nomeados.
>
> ✅ **MEDIR não é EXIBIR — a leitura foi consertada (2026-08-25), e o achado mudou o alvo.**
> A passagem apontava `tools/supervisor.ts:118`; medindo, o cálculo tinha **duas implementações
> independentes e idênticas**, e a que desenha a tela é a OUTRA — o endpoint HTTP
> `GET /api/supervisor_state/:sessionId` (`server.ts`), que a Console consome. Consertar só a tool
> teria deixado a barra dizendo "Neutral" com o commit no lugar. Hoje as duas chamam
> **`lib/session-sentiment.ts`**, fonte única.
>
> **Fonte canônica = ContextStore** (`{tenant}:ctx:{sid}` → `core.sentiment.current`), e isso é
> medição, não gosto: todo caminho que produz score passa por `update_partial_params` →
> `write_context_store_sentiment`, inclusive o auto-reporte do `output_schema`. O ctx é
> **superconjunto estrito** de `partial_params`; ler as duas fontes seria redundante *e* perderia dado.
> Lê-se sempre o hash **CRU**, nunca o `contextSnapshot` já filtrado por `applyContextMaskingDynamic`
> — o filtro é por namespace de operador, configurável POR POOL, e um pool que estreitasse a lista
> apagaria o sentimento em silêncio.
>
> **`current: null` = NÃO MEDIDO**, e nenhuma superfície renderiza sem valor. O `?? 0` convertia
> ausência num ponto legítimo da escala; pior, **desarmava a guarda que a UI já tinha** (`ActionBar`
> só renderiza com valor não-nulo) antes que ela pudesse agir. *Um default no produtor derruba a
> guarda do consumidor sem deixar rastro.* Idem `trend`, cujo default era `"stable"` — invenção da
> mesma família.
>
> **Quatro superfícies, três graus de proteção** — inventário que a passagem não tinha: `ActionBar` e
> `ContactList` guardavam por `!== null` (desarmadas); `ChatArea` inventara `!== 0`, que protegia por
> acidente **e escondia um `0.0` medido de verdade**; `EstadoTab` não tinha guarda nenhuma e
> anunciava "0% neutral" em toda sessão. *(O `packages/agent-assist-ui/` renderizava a mesma tela e caiu no mesmo conserto — app legado,
> **APOSENTADO em 2026-08-27**; a porta 5173 hoje serve só os ativos estáticos de `infra/demo/web/`.)*
>
> Gate: `infra/test/gate_console_sentiment_source.sh` (re-executável, sem contato real; testemunha
> negativa = ctx presente com outra tag e sentimento ausente ⇒ tem de vir `null`, nunca `0`).
>
> ⚠️ **Sem histórico**: o ctx guarda só o valor corrente. `trajectory` é `[]` e `trend` é `null` —
> `consolidated_turns` não serve de substituto (o `float(… or 0.0)` já achatou lá dentro, tornando um
> `0.0` medido indistinguível de turno sem medição). O array `session:{id}:sentiment` documentado
> abaixo **não tem produtor**; enquanto não tiver, gráfico e seta ficam ausentes em vez de fabricados.
>
> ⚠️ **Dívida nomeada:** o `pool_id` do meta é o pool de **ENTRADA**, não o que atende — sentimento
> medido pelo agente de fila agrega sob o pool onde o contato começou. É a fatia C de
> `session:{id}:meta` (`entry_pool_id` × `pool_id`), ver `docs/guias/session-meta-ownership.md`.
>
> Gates: `infra/test/probe_sentiment_producer.sh` (metade gateway) +
> `infra/test/gate_sentiment_engine_half.sh` (metade engine, reprodução manual com contato que
> ENFILEIRE). Detalhe em [`docs/arcos/ai-gateway.md`](../arcos/ai-gateway.md) § Medição de
> sentimento.
>
> **A recusa deixou de ser invisível (2026-08-23).** O `/v1/health` do ai-gateway decidia
> `anthropic: "ok"` pela PRESENÇA da string da chave — nada contatava o provedor —, então as 124
> recusas conviveram com verde no `docker ps`. Agora o estado é medido: desfecho gravado no funil
> único de erro + sonda de boot, `credentials` por conta, e **503 quando a chave está configurada e
> é recusada** (ausente ≠ recusada: só a segunda reprova). `unknown` nunca vira `ok` e `rate_limit`
> nunca vira `invalid`. Gate `infra/test/probe_llm_credential_health.sh`; detalhe em
> [`docs/arcos/ai-gateway.md`](../arcos/ai-gateway.md) § Health de credencial.

Score-only array in Redis during session. Labels calculated at read time using tenant-configurable ranges. Persisted to PostgreSQL (`sentiment_timeline JSONB`) on session close. Never published to canonical stream.

```
session:{id}:sentiment → [{ score: 0.40, timestamp: "..." }, ...]
TTL: same as session TTL
Ranges: [ 0.3, 1.0] → satisfied | [-0.3, 0.3] → neutral | [-0.6,-0.3] → frustrated | [-1.0,-0.6] → angry
```

> ⚠️ **`session:{id}:sentiment` NÃO TEM PRODUTOR** (medido 2026-08-25: nenhum componente escreve a
> chave). É promessa sem produtor — a mesma família de `participation_intervals`, cujo DDL *afirmava
> em prosa* a ordenação que ninguém impunha. O emitter grava três destinos e nenhum é este:
> `{tenant}:ctx:{sid}` (valor corrente, sobrescrito), `{tenant}:pool:{p}:sentiment_live` (agregado por
> pool) e o tópico `sentiment.updated`. Consequência viva: **não existe histórico por sessão**, logo
> trajetória e tendência são ausentes por decisão, não fabricadas. Ver `TODO.md`.

---

## Ver também

- [`docs/arcos/ai-gateway.md`](../arcos/ai-gateway.md) § *Medição de sentimento* — contrato,
  invariantes do analisador e o estado medido de ponta a ponta
- [`docs/guias/session-meta-ownership.md`](session-meta-ownership.md) — fatia C
  (`entry_pool_id` × `pool_id`), que é a dívida do pool sob o qual o score agrega
- Gates: `infra/test/probe_sentiment_producer.sh` (metade gateway) ·
  `infra/test/gate_sentiment_engine_half.sh` (metade engine) ·
  `infra/test/gate_console_sentiment_source.sh` (metade leitura/Console)
