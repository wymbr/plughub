# Versão de produção v1 — critério de saída, fases e prioridade

> Estado: plano aceito pelo dono em 2026-09-28 · Fichas novas `PRD-*`, `VOZ-46..49`, `WHA-01`, `WCH-14`
> em `pending.md`, sob este documento · As fichas existentes seguem nos seus grupos; a prioridade delas
> para a v1 mora **aqui** (§ 5), não no texto de cada uma.
> Origem: levantamento de 2026-09-28 — classificação das ~190 fichas abertas e leitura da infraestrutura
> para escala. Antes da v1, o modelo comercial fica em espera (decisão do dono, 2026-09-25).

## 1. Alvo

**Primeiros clientes:** canais **voz** (SIP/PSTN e WebRTC), **WhatsApp** e **webchat**, em **instância
dedicada** por cliente (não SaaS multi-inquilino), com agentes de IA e humanos, sob LGPD.

**Capacidade:**

| Limite | Valor |
|---|---|
| Sessões simultâneas, todos os canais | **3.000** |
| Voz simultânea (subteto) | **1.000** — e o que a voz ocupa sai do total: com voz em 1.000, mensagens têm 2.000 |
| Meta de atendimento por IA na voz | **80%** → até ~800 chamadas simultâneas de IA |

**STT e TTS:** o primário roda no cliente ou em VMs dedicadas a ele; transbordo para provedor gerenciado
existe como **contingência**, ligado por configuração explícita e registrado em log a cada uso (dado de
voz sai da instância — decisão que o cliente tem de ver).

**Transcrição de chamada humana: offline, a partir da gravação** (decisão de 2026-09-28). O STT em tempo
real fica para as chamadas de IA, que precisam dele para conversar. Ganha-se custo (a carga online cai às
~800 chamadas de IA) e qualidade (modelo maior, áudio inteiro, diarização). Perde-se a transcrição AO VIVO
da chamada humana (copiloto, sentimento em tempo real, supervisor lendo); se um pool precisar dela, liga
o online só nele. Só se transcreve o que foi gravado, e a gravação segue a política do pool com aviso ao
cliente (VOZ-06): pool que não grava fica sem transcrição, e o relatório diz isso em vez de mostrar zero.

**Hardware:** dimensionado em [`producao-v1-dimensionamento.md`](producao-v1-dimensionamento.md). Produção
e teste de carga rodam em VMs próprias, com bancada unitária antes da compra completa.

## 2. O que o levantamento mostrou

As fichas abertas cuidam da **correção** do core e dos canais. O que separa o PlugHub de 3.000 sessões
é quase todo **sem ficha** até hoje — ele é uma stack demo num host só. Tetos medidos no código:

1. **Bridge limitado a ~100 sessões de IA simultâneas.** `POST /execute` ao executor de fluxo sem timeout,
   aberto durante a conversa inteira (inclusive na espera do `menu`), num `aiohttp.ClientSession()` com o
   limite padrão de 100 conexões (`orchestrator-bridge/main.py:1638`, `:10848`).
2. **Estado vivo num Redis sem persistência e sem réplica** (`redis-server --save ""`,
   `docker-compose.demo.yml:424`): `pipeline_state`, filas, ContextStore, sessões suspensas.
3. **Bot de voz em Python dentro do processo do channel-gateway**, STT em lote por frase, um servidor de
   fala numa GPU, sem fila nem controle de concorrência.
4. **Kafka com 1 broker, RF=1 e 3 partições; Postgres e ClickHouse em nó único;** nenhum backup,
   métrica, tracing, alerta, nem teste de carga ou número de capacidade medido.
5. **Serviços que supõem uma réplica** (reconcile e heartbeat do bridge; crash detector e drain do routing;
   conexões ao vivo e SSE do mcp-server; scanner do workflow-api) e **offset do bridge commitado antes do
   processamento**.
6. **WhatsApp** sem deduplicação por `wamid` (a Meta reenvia webhooks), sem tratamento de 429 e sem
   controle de vazão por número; **webchat** com duas conexões Redis por WebSocket (~6 mil a 3 mil
   conversas); **coturn** com 21 portas de relay; **LiveKit** e **egress** em nó único.
7. **Segredos fixos no compose** (`changeme_*`, JWT de demo), sem TLS nem borda versionada.

## 3. Fases

### Fase 0 — decidir e medir

- Topologia da instância dedicada (Kubernetes ou compose multi-host) e o pacote de produção — `PRD-01`.
  Decide junto a `CAP-10`/`CAP-15`.
- Harness de carga e a primeira rodada **sem conserto nenhum**, para medir os tetos reais — `PRD-02`.
- Escolha de STT e TTS para PT-BR na bancada unitária, com áudio de telefonia real — `VOZ-50`.

### Fase 1 — o core aguenta escala

`PRD-03` (teto do bridge — paliativo feito em 2026-09-28) · `DUR-01` (espera do `menu` por suspender/retomar — promovida de `adiado`; **fechada em 2026-09-28**) ·
`SFS-01` (executor sai de "harness de teste") · `PRD-04` (Redis) · `PRD-05` (Kafka, Postgres, ClickHouse,
backup) · `PRD-06` (réplicas e offset) · `PRD-08` (admissão por canal) · `ALW-18` · `PRM-04`.

### Fase 2 — canais confiáveis

- **Voz:** `VOZ-45` · `VOZ-20` · ~~`VOZ-03`~~ (legado Twilio aposentado em 2026-09-28) ·
  `VOZ-46` (bot em workers) · `VOZ-47` (STT/TTS dimensionado e transbordo) · `VOZ-48` (transcrição
  offline) · `VOZ-49` (LiveKit, egress, coturn) · `VOZ-34` (P1).
- **WhatsApp:** `PID-22` · `WHA-01` · `USG-03` (P1).
- **Webchat:** `WCH-03` (widget de produto; separar o widget só de chat, que não depende da `WCH-02`) ·
  `WCH-14` · `ALW-18`.

### Fase 3 — instância dedicada segura e operável

`PRD-01` (segredos, TLS, borda com a allowlist dos sete prefixos) · `CAP-10`/`CAP-15` · `AUT-20` ·
`AUT-58` · `CNS-25` · `AUD-03` (direitos do titular) · `PRD-07` (observabilidade, backup testado,
runbook de atualização).

### Fase 4 — prova

`PRD-09`: carga combinada no alvo (§ 1), teste de resistência de horas, testes de falha (réplica, failover
do Redis, broker) sem perder conversa — e o piloto.

## 4. Critério de saída da v1

1. Carga combinada sustentada: 1.000 chamadas (80% IA) + 2.000 conversas de mensagem, e 3.000 de mensagem
   sem voz, com metas de latência por canal definidas na Fase 0 e cumpridas.
2. Queda de qualquer réplica de serviço, failover do Redis e perda de um broker **sem perder conversa**.
3. Nenhum segredo de demo, TLS em toda borda publicada, só os sete prefixos publicáveis expostos.
4. Backup e restauração **executados** de Postgres, ClickHouse e Redis.
5. Métricas e alertas cobrindo os tetos do § 2.
6. Um cliente piloto operando.

## 5. Prioridade das fichas existentes

**P0 — bloqueia produção:** `VOZ-50`, `VOZ-45`, ~~`VOZ-03`~~ (fechada 2026-09-28), `VOZ-20`, `PID-22`, `WCH-03`, `ALW-18`, ~~`DUR-01`~~ (fechada 2026-09-28),
`SFS-01`, `PRM-04`, `CAP-10`, `CAP-15`, `AUT-20`, `AUT-58`, `AUD-03`.

**P1 — necessário nos primeiros meses:** `VOZ-13`, `VOZ-11`, `VOZ-34`, `WCH-02`, `WCH-04`, `USG-01`,
`USG-03`, `USG-04`, `AIG-02`, `AIG-01`, `KPI-01..05`, `AUD-01`, `AUD-02`, `CAP-07`, `CAP-08`, `CAP-14`,
`CNS-25`, `CNS-23`, `CTX-08`, `ALW-15`, `FMT-11`, `ALW-14`, `AUT-57`, `AUT-21`, `AUT-51`, `PUL-01`,
`ORF-03`, `DLG-15`, `DLG-33`, `SES-01`, `ROT-01`, `GAT-02`, `AUT-25`, `AUT-26`, `PID-11`, `SUR-03`,
`SUR-06`; `APR-01` só se aprovações entrarem na v1.

⚠️ `VOZ-13` (ciclo de fala em tempo real, < 1,5 s) vira **P0** se a IA conversacional por voz for exigida
já no piloto — com a meta de 80% de IA na voz, provavelmente será.

**P2 — congelado até a v1:** editor e autoria (`IDE-*`, `NLF-01`, `DTB-01`, `SCN-01`, `BLK-01`,
`EVM-01`, `RRH-*`, `DLG-35` e demais `DLG`/`FMT`/`NIV` de direção); análise (`ANL-*`, `PMN-01`,
`QMN-01`, `KPI-06..08`, `HIS-*`, `JRN-*`, `CCH-*`); voz avançada (`VOZ-14`, `VOZ-24`, `VOZ-28`,
`VOZ-29`, `VOZ-33` — P1 se o piloto tiver PABX —, `NIV-06`, `USG-02`); orquestração (`CTR-*`, `RET-10`,
`SLT-*`); identidade e comércio (`IDN-*`, `PID-05/14/18/20/21`); outbound (`OUT-*`); aprovação e survey
(`APR-02..07`, `SUR-01/02/04`); ContextStore (`ALW-05/09/16/19/20`, `CNS-13`, `CTX-10`); quality ingest
(`QIN-*`, `QSI-01`, `EVM-02`); higiene e docs (`DOC-*`, `PUI-01`, `AUT-09/28/29`, `SCH-02`).

**Fora de escopo com instância dedicada:** `CNS-14`, `CNS-16`, `AUT-22`, `AUT-32` (multi-inquilino) — a
reabrir se o modelo SaaS voltar à mesa; `PRC-01` a revisar no modelo dedicado. Caíram com a `VOZ-03`
(Twilio aposentado em 2026-09-28): `NIV-16`, `VOZ-30`.
