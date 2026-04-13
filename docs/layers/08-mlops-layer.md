# Layer 8 — MLOps Layer

> Spec de referência: v24.0 seções 2.2 (frameworks), 7.3 (STT), Horizonte 2
> Responsabilidade: ciclo de vida de modelos — fine-tuning de STT por tenant, retraining de agentes, Model Registry
> Status no Horizonte 1: **fora do repositório principal** — repositório de infra separado

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

**Dimensionamento de GPU:** deve considerar streams WebRTC adicionalmente aos streams SIP quando o Horizonte 2 for ativado — o STT pipeline é compartilhado entre os dois canais de áudio.

**Lifecycle policies:** modelos antigos no Object Storage seguem lifecycle policies por versão — versões sem instâncias ativas são arquivadas ou removidas após período configurável.

**Retenção de áudio:** 30 dias (LGPD). Fine-tuning deve ser executado dentro desta janela ou usar datasets persistentes anotados separadamente.

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
- Seção 7.4 — WebRTC Gateway (pipeline STT compartilhado, Horizonte 2)
- Seção 13.4 — Data Mining (Horizonte 2 da Camada 3 analítica)
