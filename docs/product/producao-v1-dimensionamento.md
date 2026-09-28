# Versão de produção v1 — dimensionamento de hardware

> Estado: primeira estimativa, 2026-09-28 · Parte de [`producao-v1.md`](producao-v1.md) · Calibrada pela
> `PRD-02` (harness de carga) antes da compra completa — ver § 5.
> Premissa do dono: produção e teste de carga rodam em **VMs dimensionadas para o alvo**; a máquina de
> desenvolvimento não é base de nada.

## 1. Alvo e premissas

| Premissa | Valor | De onde |
|---|---|---|
| Sessões simultâneas no pico | 3.000 (voz ≤ 1.000; mensagens = 3.000 − voz ativa) | dono |
| Voz com IA | 80% → ~800 chamadas; 200 humanas | dono |
| Cliente falando, numa chamada de IA | ~50% do tempo → ~400 streams de STT ativos | estimativa |
| Bot falando | ~40% do tempo → ~320 s de áudio sintetizado por segundo | estimativa |
| Chamadas por sala | 2 a 3 participantes (cliente, bot e/ou humano, às vezes supervisor) | modelo de sessão |
| Gravação | por trilha (TrackEgress), nunca RoomComposite; quem grava é decidido pelo pool | VOZ-06 |
| Transcrição humana | offline, a partir da gravação | decisão de 2026-09-28 |
| LLM | provedor externo por API — **não** entra no hardware, mas entra na cota (§ 4) | arquitetura atual |

Legenda: **[pub]** número publicado · **[est]** conta sobre números publicados · **[medir]** sem número
publicado, precisa da `PRD-02`.

## 2. Produção — VMs por camada

### 2.1 Mídia de voz

| Componente | VMs | Por VM | Base do número |
|---|---|---|---|
| LiveKit SFU | 2 (N+1) | 16 vCPU · 16 GB · NIC 10 Gbps · IP público | [pub] 1 nó de 16 núcleos encaminha ~30 mil trilhas de áudio a 80% de CPU; 1.000 salas de 2–3 pessoas são ~1/10 disso. O segundo nó é para falha — cada sala cabe num nó só |
| LiveKit SIP | 3 (N+1) | 16 vCPU · 16 GB · IP público · RTP 10000–20000 | [medir] sem número publicado. Estimativa de 2–3% de núcleo por chamada para G.711 ↔ Opus com reamostragem ⇒ 20–30 núcleos |
| Egress de gravação | 3 | 16 vCPU · 32 GB | [pub] instância mínima de 4 CPU/4 GB; TrackEgress cabe "centenas" por instância. Para gravar só as 200 humanas, 1 VM basta |
| coturn (TURN com TLS na 443) | 2 (N+1) | 4 vCPU · 8 GB · 1 Gbps · IP público | [pub/terceiro] milhares de fluxos por núcleo; só parte do WebRTC usa relay |
| Workers do bot de voz (`VOZ-46`) | 12 (10 + 2 de folga) | 16 vCPU · 32 GB | [pub] servidor de agente LiveKit: 4 núcleos/8 GB para 10–25 bots, escalar a 50% de carga ⇒ 800 bots ≈ 130–320 vCPU, com STT/TTS fora da CPU. Faixa real: 8 a 20 VMs, [medir] o custo do nosso bot |

### 2.2 STT e TTS (GPU, hospedados no cliente ou em VM dedicada)

A escolha do modelo de STT muda o número de GPUs em ~3×; por isso ela é medida antes da compra (§ 5).

| Função | Opção A — Whisper (já em uso) | Opção B — modelo de streaming | Base |
|---|---|---|---|
| **STT online** (~400 streams) | **12 VMs com 1× L4** (8 vCPU · 32 GB) | **4 VMs com 1× L40S** (16 vCPU · 64 GB) | [pub] Whisper large-v3-turbo na L4: ~41× o tempo real em lote. Parakeet/Riva streaming: ~160 streams por GPU grande (modelo inglês). ⚠️ PT-BR não validado para Parakeet (o v3 é português europeu) |
| **TTS** (~320 s de áudio/s) | **3 VMs com 1× L40S** (16 vCPU · 64 GB) | idem | [pub] Kokoro em servidor com lote no H100: ~670 s de áudio/s; Kokoro-FastAPI: ~127 s/s. Em CPU não escala. Riva FastPitch na L4: 32 streams a 500× — ⚠️ PT-BR a verificar |
| **Transcrição offline** (humanas) | **2 VMs com 1× L4** | idem | [est] 200 chamadas × 10 h/dia = 2.000 horas/dia; a ~41× por L4 ⇒ ~50 horas-GPU/dia. Alternativa: reusar as GPUs do online fora do pico |
| **Total de GPUs** | **~17** (14 L4 + 3 L40S) | **~9** (4 + 3 L40S + 2 L4) | |

**Transbordo de contingência:** provedor gerenciado de STT/TTS, sem hardware, com cota contratada para
~20% do pico (falha de uma GPU ou pico acima do previsto), ligado por configuração e logado a cada uso.

### 2.3 Aplicação (serviços sem estado)

Um cluster (Kubernetes ou compose multi-host, decisão da `PRD-01`) com **6 nós de 16 vCPU · 64 GB**
(~96 vCPU), dividido por réplica:

| Serviço | Réplicas × tamanho |
|---|---|
| channel-gateway (webchat WS, WhatsApp, webhooks do SFU) | 3 × 8 vCPU / 16 GB |
| orchestrator-bridge | 3 × 8 / 16 |
| executor de skill-flow | 4 × 8 / 16 — depois da `DUR-01`; antes dela, cada espera de `menu` segura conexão e memória |
| routing-engine · mcp-server-plughub · ai-gateway | 3 × 4 / 8 cada |
| demais APIs (registry, config, auth, dialog, evaluation, analytics, scheduler, calendar, mailing, pricing, usage, rules, replayer) | 2 × 2–4 / 4–8 cada |
| platform-ui | 2 × 1 / 1 |

[est] — sem teste de carga, é o número com mais margem de erro depois do bot de voz. A `PRD-02` o fixa.

### 2.4 Dados

| Componente | VMs | Por VM |
|---|---|---|
| Redis (primário + 2 réplicas com failover, AOF) | 3 | 8 vCPU · 32 GB · NVMe |
| Kafka (RF=3, partições dimensionadas) | 3 | 8 vCPU · 32 GB · 1 TB NVMe |
| PostgreSQL (primário + réplica) | 2 | 16 vCPU · 64 GB · 1 TB NVMe |
| ClickHouse (com réplica) | 2 | 16 vCPU · 64 GB · 2–4 TB NVMe (depende da retenção) |
| Armazenamento de objetos (gravações, anexos) | — | por retenção: [est] Opus a ~14 MB/h por trilha; 10 mil chamadas-hora/dia com 2 trilhas ≈ 280 GB/dia ⇒ ~25 TB em 90 dias gravando tudo, ~5 TB gravando só as humanas |

### 2.5 Borda e operação

| Componente | VMs | Por VM |
|---|---|---|
| Borda (TLS, allowlist dos sete prefixos, balanceamento) | 2 | 4 vCPU · 8 GB |
| Observabilidade (métricas, logs, alertas — `PRD-07`) | 1 | 16 vCPU · 64 GB · 2 TB |

### 2.6 Rede

- **Pública:** ≥ 1 Gbps simétrico. [est] Tronco SIP G.711 ~90 kbps por sentido por chamada (≈ 90 Mbps a
  1.000 chamadas); Opus ~40–50 kbps por trilha por sentido no WebRTC.
- **Interna:** 10 Gbps entre SFU, SIP, workers do bot e GPUs (o áudio em PCM viaja entre eles).
- **IPs públicos:** SFU, SIP e TURN, com as faixas de porta abertas; o resto só pela borda.

### 2.7 Total aproximado (opção A de STT)

~50 VMs de CPU (~620 vCPU) + ~17 GPUs. Com a opção B, ~9 GPUs. As faixas mais incertas são os workers do
bot (8–20 VMs), o SIP e a camada de aplicação — as três só saem de medição.

## 3. Ambiente de teste de carga

O ambiente sob teste é a própria topologia de produção (§ 2). Os geradores rodam em VMs **separadas**,
para não disputar CPU com o que está sendo medido:

| Gerador | VMs | Por VM | Faz |
|---|---|---|---|
| SIPp com RTP (fala PT-BR gravada) | 3 | 8 vCPU · 16 GB · Linux | até 1.000 chamadas SIP. [medir] chamadas por VM — o SIPp é um laço de eventos só; começar com 3 |
| WebRTC sintético (`lk load-test` ou clientes próprios) | 2 | 16 vCPU · 32 GB | a parcela WebRTC da voz, com áudio real |
| k6 para webchat e WhatsApp | 2 | 8 vCPU · 16 GB | 3.000 WebSockets; webhook da Meta simulado |
| Simulador da Meta (recebe os envios, devolve 429 sob demanda) | 1 | 4 vCPU · 8 GB | testa vazão e backoff (`WHA-01`) sem tocar a Meta |
| LLM simulado (latência e tokens configuráveis) | 1 | 8 vCPU · 16 GB | evita custo e cota nas rodadas grandes; uma rodada menor com o provedor real valida a cota (§ 4) |

## 4. Cota do provedor de LLM (não é hardware, mas trava a capacidade)

[est] Voz: 800 bots com um turno a cada ~8 s ≈ 100 chamadas/s. Mensagens: ~2.000 sessões de IA com um
turno a cada ~30 s ≈ 70/s. Juntas, **~10 mil requisições por minuto**; a ~1.500 tokens de entrada por
turno, **~15 milhões de tokens de entrada por minuto**. Tem de caber na soma das contas do catálogo
`llm_accounts` com folga para o transbordo entre elas, e a latência por conta e modelo tem de ser medida
(`AIG-02`).

## 5. Ordem de compra: custo unitário antes de escalar

Comprar tudo de uma vez e descobrir no teste que o número estava errado sai caro. Proposta:

1. **Bancada unitária:** 1 VM de cada tipo crítico — 1 SFU, 1 SIP, 1 worker do bot, 1 L4, 1 L40S, e os
   geradores. Medir o custo por chamada de cada um, e a **qualidade em PT-BR** dos modelos de STT e TTS
   candidatos (é o que decide entre a opção A e a B) — `VOZ-50`.
2. **Recalcular** o § 2 com os números medidos.
3. **Comprar a topologia completa** e rodar a Fase 4 do `producao-v1.md` (carga combinada, resistência,
   falhas).
