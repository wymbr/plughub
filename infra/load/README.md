# Harness de carga — PRD-02

Mede os **tetos reais** da plataforma nos três canais da produção v1 (voz, WhatsApp,
webchat) contra as metas de [`docs/product/producao-v1.md`](../../docs/product/producao-v1.md):
3.000 sessões simultâneas no total, voz ≤ 1.000 (80% IA), mensagens no restante. O hardware
de produção e dos geradores está em
[`producao-v1-dimensionamento.md`](../../docs/product/producao-v1-dimensionamento.md) — **não**
se mede capacidade na máquina de desenvolvimento; aqui ela só serve para validar o harness.

> **Regra da primeira rodada: nenhum conserto.** Ela roda contra a plataforma como está,
> e o resultado é a LISTA de onde quebra e em quantas sessões. Consertar no meio apaga a
> linha de base e deixa sem número o ganho de cada PRD-03..09.

> **Ausência não é zero.** Métrica sem amostra sai `null` com `n: 0`. Uma rodada em que
> ninguém recebeu resposta não pode reportar "resposta em 0 ms".

## Peças

| Arquivo | O que faz |
|---|---|
| `webchat_load.py` | Clientes WebSocket reais: autenticam com JWT HS256, respondem menu/lista/formulário/texto e, ao fim da sessão, abrem outra. Concorrência sustentada, não rajada (`--once` para rajada). |
| `whatsapp_load.py` | Webhooks de entrada assinados como a Meta (`X-Hub-Signature-256`), um telefone por sessão; espera a resposta no `fake_meta`. `--duplicate-rate` reenvia a mesma entrega (mede a WHA-01). |
| `fake_meta.py` | Simulador da Graph API: recebe os envios do gateway, entrega ao driver por long-poll, injeta `429` e latência. |
| `llm_mock.py` | Provedor Anthropic simulado (`POST /v1/messages`): `tool_use` com entrada derivada do `input_schema`, latência log-normal, `usage`. `--overrides` fixa a saída de um step. |
| `sample_redis.py` | Amostra `INFO` do Redis (conexões, clientes bloqueados, memória, ops/s) em CSV, sem dependência. |
| `sip/uac_speech.xml` + `sip/run_sipp.sh` | Chamada SIP com digest, PCMU, fala em loop, tecla RFC 4733 e BYE; mede tempo até o 200 e queda antes do fim. |
| `harness_common.py` | JWT, assinatura Meta, percentis e relatório JSON. |

WebRTC (browser → SFU) **ainda não tem driver**: o caminho de voz do cliente-app mede-se
primeiro pelo SIP, que exercita a mesma sala, o mesmo bot leg e o mesmo STT/TTS. O driver
WebRTC entra quando a rodada de SIP estiver de pé (cliente `livekit` Python publicando o
mesmo `.wav`, um por participante).

## Topologia

```
 gerador A (webchat_load) ─── WS ──────────┐
 gerador B (whatsapp_load) ─ webhook ──────┤
 gerador C..E (SIPp) ──────── SIP/RTP ─────┤──► plataforma sob teste
                                           │      channel-gateway ──► fake_meta (gerador B)
                                           │      ai-gateway ───────► llm_mock (gerador F)
 sample_redis ─────────── INFO ────────────┘
```

Geradores em VMs separadas da plataforma — o gerador não pode ser o gargalo que se mede.
Um processo Python sustenta ~1.500 WebSockets de webchat; SIPp com `rtp_stream` satura
perto de ~500 chamadas por máquina: reparta.

## Configuração do ambiente sob teste

| Serviço | Variável | Valor |
|---|---|---|
| channel-gateway | `PLUGHUB_WHATSAPP_GRAPH_API_URL` | `http://<fake_meta>:9100/v19.0` |
| channel-gateway | `PLUGHUB_WHATSAPP_APP_SECRET` | o `--app-secret` do driver |
| channel-gateway | `PLUGHUB_WHATSAPP_PHONE_NUMBER_ID` | o pool que atende (PID-22) |
| channel-gateway | `PLUGHUB_WHATSAPP_ACCESS_TOKEN` | qualquer valor |
| ai-gateway | `ANTHROPIC_BASE_URL` | `http://<llm_mock>:9200` (só nas rodadas com mock) |

O SDK da Anthropic lê `ANTHROPIC_BASE_URL` sozinho
(`ai-gateway/providers/anthropic_provider.py` não passa `base_url`). **Uma rodada menor com o
provedor real é obrigatória**: é ela que mede cota (RPM/TPM) e latência de verdade — o mock
mede a plataforma, não o LLM.

## Rodadas

```bash
pip install -r requirements.txt

# Webchat — 2.000 sessões sustentadas por 30 min, rampa de 5 min
python3 webchat_load.py --url ws://GATEWAY:8010/ws/chat/<slug> --secret "$WEBCHAT_SECRET" \
    --tenant <tenant> --concurrency 2000 --ramp-s 300 --duration 1800 --report webchat.json

# WhatsApp — com o simulador da Meta de pé
python3 fake_meta.py --port 9100 --delay-ms 80 --rate-429 0.01 &
python3 whatsapp_load.py --gateway http://GATEWAY:8010 --fake-meta http://FAKE:9100 \
    --app-secret "$APP_SECRET" --concurrency 1000 --ramp-s 300 --duration 1800 \
    --duplicate-rate 0.05 --report wa.json

# LLM simulado (ANTHROPIC_BASE_URL do ai-gateway apontando para cá)
python3 llm_mock.py --port 9200 --latency-ms 900

# Voz — 800 chamadas simultâneas repartidas em geradores
SIP_TARGET=SIP:5060 SIP_NUMBER=+5511... SIP_USER=... SIP_PASSWORD=... \
    CONCURRENCY=400 TALK_MS=90000 RAMP_CPS=3 bash sip/run_sipp.sh

# Redis, durante tudo
python3 sample_redis.py --host REDIS --port 6379 --every 5 --out redis.csv
```

Áudio do SIPp: `sip/audio/fala_ptbr_8k_ulaw.wav`, 8 kHz mono µ-law, com fala e pausas
(o bot leg precisa de silêncio para fechar o turno):

```bash
ffmpeg -i fala.wav -ar 8000 -ac 1 -acodec pcm_mulaw sip/audio/fala_ptbr_8k_ulaw.wav
```

O `pcap/dtmf_2833_1.pcap` acompanha a distribuição do SIPp (compilar com `-DUSE_PCAP=1`).

## O que se lê no fim

- **Por canal:** `peak_active_sessions`, `first_response` e `turn_response` (p50/p95/p99),
  `session_ended:<motivo>`, `error:*`, `no_first_response`. Voz: `Failed`, rtd `setup` e a
  repartição de duração do SIPp.
- **Da plataforma:** `sample_redis` (conexões, bloqueados), CPU/memória por serviço,
  lag de consumer do Kafka, fila do bridge.
- **O teto** de cada canal é a concorrência em que o p95 de resposta passa do alvo ou a
  taxa de erro passa de 1% — o que vier primeiro — e **o componente que cedeu**.

## Achados ao validar o harness (demo, 3 sessões — sem valor de capacidade)

1. **O agente fala primeiro.** No demo o menu chega ~40 ms depois da autenticação. A
   primeira versão do driver mandava "oi" por cima dele e cronometrava um quadro já
   enfileirado: resposta de **0,1 ms**, plausível o bastante para passar. Hoje a 1ª resposta
   conta desde a autenticação e o cliente só abre a conversa se nada chegar em
   `--greeting-wait-s`.
2. **"Sempre a primeira opção" anda em círculo** (`sac → info_plano → outra_coisa → sac`) e
   a sessão nunca fecha pelo fluxo. A partir de `--end-after-turns` o driver escolhe a opção
   de encerrar; medido depois disso: 33/33 sessões em `flow_complete`, 1ª resposta p50 34 ms.
3. `infra/test/_ws_chat.py:234` espera `conn.session_closed`; o servidor manda
   `conn.session_ended` — o helper de teste nunca vê o fim da sessão.
