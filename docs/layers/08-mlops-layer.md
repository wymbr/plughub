# Layer 8 — MLOps Layer

> Última atualização: 2026-05-25 · Estado: Arc 16
> Responsabilidade: ciclo de vida de modelos — fine-tuning de STT por tenant, retraining de agentes, Model Registry
> Status: camada **fora do repositório principal** — repositório de infra separado (Horizonte 1)
> Spec de referência: v24.0 seções 2.2 (frameworks), 7.3 (STT)

---

## Visão geral

A MLOps Layer gerencia o ciclo de vida dos modelos que a plataforma usa em produção. No Horizonte 1, o foco é o pipeline de fine-tuning de STT (Speech-to-Text) por tenant — vocabulário específico de domínio, termos técnicos, nomes de produtos. Retraining de agentes IA e modelos de relevância são planejados para horizontes posteriores.

Esta camada vive **fora do monorepo principal** — repositório de infra separado. Está documentada aqui por completude arquitetural.

---

## Componentes

| Componente | Tecnologia | Responsabilidade |
|---|---|---|
| **Fine-tuning pipeline** | HuggingFace Transformers + Ray Train | Fine-tuning de Whisper (STT) com dados de áudio por tenant |
| **STT primário** | NVIDIA Riva | STT streaming self-hosted — latência 100–200ms, suporte a pt-BR |
| **STT fallback** | Deepgram | Fallback automático quando Riva indisponível |
| **Model Registry** | S3/GCS + metadata store | Versões de modelos STT por tenant; políticas de lifecycle por versão |
| **GPU cluster** | Ray Train (multi-GPU) | Treinamento distribuído para fine-tuning |

---

## Interfaces

**Entrada (dados para fine-tuning):**
- Áudio de ligações do Object Storage (S3/GCS), retido por 30 dias (LGPD)
- Datasets anotados pelo tenant
- Métricas WER (Word Error Rate) por tenant — coletadas em produção pelo STT Router

**Saída:**
- Modelos STT fine-tuned implantados no NVIDIA Riva por tenant
- Métricas de qualidade STT (WER antes/depois do fine-tuning) para dashboard operacional

**Integração com a plataforma:**
- STT Router (Go) consulta o Model Registry para carregar o modelo correto por tenant
- Métricas WER publicadas para a Observability Layer
- Fallback Riva → Deepgram gerenciado pelo STT Router automaticamente

---

## Fluxo de dados

```
Áudio de ligações → Object Storage (S3/GCS)
↓ Fine-tuning pipeline coleta amostras por tenant
↓ HuggingFace Transformers prepara dataset
↓ Ray Train treina em cluster GPU
↓ Modelo avaliado (WER por tenant)
↓ Model Registry registra nova versão
↓ STT Router carrega modelo atualizado por tenant
↓ Produção usa modelo fine-tuned
↓ Métricas WER → Observability Layer
```

---

## Considerações operacionais

**Fine-tuning LoRA:** técnica de fine-tuning eficiente (Low-Rank Adaptation) que adapta o Whisper ao vocabulário do tenant sem retreinar o modelo base completo. Reduz tempo de treinamento e custo de GPU.

> ⚠️ **Correção de 2026-08-19 — medido.** *"canal `webrtc` implementado — Arc 15"* é **falso** como
> premissa de dimensionamento: **não há stream de áudio a dimensionar hoje**. O plano de mídia do
> WebRTC nunca foi provisionado (zero serviço LiveKit em compose algum, zero env `LIVEKIT_*`, SDK fora
> de `packages/channel-gateway/pyproject.toml:6-23`; sem credencial o provider entra em `_dev_mode` e
> devolve token/sala/egress placebo — `webrtc_provider.py:167`; ver `docs/arcos/arc15-webrtc.md:3-17`),
> e o canal `voice` não roda (`handle_inbound` chama cinco métodos inexistentes —
> `adapters/voice.py:236,247,433,558,565`, ausentes em `adapters/base.py:44-77`, mockados em
> `tests/test_voice_adapter.py:116-121` ⇒ `AttributeError` em runtime real). O texto abaixo é
> **projeto**, válido a partir do momento em que existir plano de mídia. Reconstrução:
> [`adr-voice-media-plane.md`](../adr/adr-voice-media-plane.md).

**Dimensionamento de GPU *(projeto)*:** deve considerar os streams WebRTC adicionalmente aos streams de voz/SIP — o STT pipeline é compartilhado entre os canais de áudio. Hoje **nenhum dos dois canais de áudio está de pé** (ver correção acima).

**Lifecycle policies:** modelos antigos no Object Storage seguem lifecycle policies por versão — versões sem instâncias ativas são arquivadas ou removidas após período configurável.

**Retenção de áudio:** ⚠️ *corrigido 2026-08-19* — deixou de ser um número fixo neste documento. É **item de configuração por classe de artefato** (namespace `storage` na config-api), decidido em [`../adr/adr-voice-media-plane.md`](../adr/adr-voice-media-plane.md) V5, porque o repositório tinha dois números conflitantes (30 dias aqui e em `07-data-layer.md`, 5 anos em `../arcos/channel-gateway-multi-channel.md:1371-1550`) e **nenhum dos dois estava implementado** — o que roda é `attachment_expiry_days: int = 30` em `channel-gateway/config.py:119`, um único valor em env para todas as classes. Consequência para MLOps: fine-tuning deve ser executado dentro da janela **configurada para a classe `call_recording`**, ou usar datasets persistentes anotados separadamente.

**Horizonte 1 — escopo atual:**
- Fine-tuning de STT por tenant
- Métricas WER por tenant
- Fallback Riva → Deepgram automático

**Horizonte 2 — planejado:**
- Retraining de agentes IA com base em feedback de avaliações
- Modelos de relevância para `supervisor_capabilities`
- Clustering automático de intents
- Anomalia em tempo real

---

## Referência spec

- Seção 2.2 — Frameworks e SDKs (Ray Train, HuggingFace, NVIDIA Riva)
- Seção 7.3 — STT Router e fine-tuning
- Seção 7.4 — WebRTC Gateway (pipeline STT compartilhado — **projeto**: só a sinalização do canal WebRTC roda, o plano de mídia não foi provisionado; ver correção em § Considerações operacionais)
- Seção 13.4 — Data Mining (Horizonte 2 da Camada 3 analítica)
