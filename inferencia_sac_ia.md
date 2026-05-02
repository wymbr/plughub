# Arquitetura de Inferência para SAC IA
## LLM vs. Modelos Especializados na Extração de Contexto

**PlugHub Platform · Versão 1.0 · Abril 2026**

---

## 1. Contexto

No fluxo do SAC IA (`agente_sac_ia_v1`), antes de escalar para um agente humano, o agente especialista `agente_contexto_ia_v1` executa dois grupos de operações distintos:

- **Raciocínio de intenção**: detectar o que o cliente quer, avaliar completude do contexto e decidir a estratégia de coleta (`completo` / `buscar_crm` / `coletar` / `confirmar`).
- **Extração de campos estruturados**: mapear CPF, nome, conta, motivo, sentimento e demais campos do `ContactContext` a partir de mensagens em linguagem natural.

A questão técnica central é: essas operações exigem necessariamente um LLM de grande porte, ou modelos menores e especializados — como Gemma ou SSLMs fine-tunados — seriam suficientes?

---

## 2. Análise por Tipo de Operação

### 2.1 Extração de campos com padrão fixo

**CPF, conta e identificadores numéricos** são candidatos naturais a regex. Um padrão como `\d{3}\.\d{3}\.\d{3}-\d{2}` extrai CPF com 100% de acurácia e latência de microssegundos — sem nenhum modelo de linguagem envolvido.

**Nome próprio** é tarefa clássica de Reconhecimento de Entidades Nomeadas (NER). Modelos como `spaCy pt_core_news_lg` ou um BERT-NER fine-tunado no domínio de atendimento brasileiro atingem F1 > 95% nesse campo, com latência de 3–8 ms em CPU.

### 2.2 Classificação de intenção e motivo

O vocabulário de intenções de um SAC de telecomunicações é relativamente fechado: cancelamento, reclamação técnica, dúvida de fatura, portabilidade, upgrade de plano. Modelos classificadores fine-tunados nesse domínio (DistilBERT ~66M parâmetros, ou Gemma 2B) cobrem 85–90% dos casos com confiança alta, entregando scores de probabilidade nativamente — o que é mais preciso e interpretável do que tentar extrair um score de confiança de texto gerado por LLM.

### 2.3 Onde o LLM é insubstituível

Três operações do fluxo atual exigem capacidade generativa que modelos menores não entregam adequadamente:

- **`gerar_pergunta`**: compor uma única pergunta consolidada em português natural que cubra múltiplos gaps de informação ao mesmo tempo — tarefa generativa aberta.
- **`resumo_conversa`**: sumarização livre do histórico da sessão para compor o campo `resumo_conversa` do `ContactContext`.
- **Casos ambíguos de alta complexidade**: mensagens com múltiplas intenções implícitas, referências a contratos ou situações fora da distribuição de treino.

> **Exemplo de caso complexo:** *"Quero cancelar, mas antes preciso entender a multa. Ah, e o técnico que veio ontem não resolveu."* — mistura intenção de cancelamento, consulta contratual e reclamação técnica em uma única mensagem. Um classificador treinado em intenções atômicas vai divergir; o LLM absorve o contexto composto naturalmente.

---

## 3. Tabela Consolidada: Step × Modelo Recomendado

| Operação / Step | Modelo indicado | Alternativa leve | Observação |
|---|---|---|---|
| Extração de CPF | Regex puro | — | 100% acurácia, zero latência |
| Extração de nome | NER clássico (spaCy pt) | BERT-NER fino | F1 > 95% no domínio |
| Extração de conta / ID | Regex + NER | DistilBERT | Padrão previsível |
| Classificação de intenção | Classificador fine-tuned | Gemma 2B / DistilBERT | 80–90% cobertura |
| Sentimento atual | Classificador (3 classes) | BERT-sentimento-pt | Saída de probabilidade nativa |
| Detecção de ambiguidade | LLM (fallback) | Limiar de confiança baixo | Escala para LLM se conf. < 0,7 |
| `gerar_pergunta` | **LLM obrigatório** | — | Tarefa generativa livre |
| `resumo_conversa` | **LLM obrigatório** | — | Sumarização aberta |
| `avaliar_contexto` (estratégia) | Classificador de estado | Gemma 2B | Apenas 4 classes: completo / crm / coletar / confirmar |

---

## 4. Arquitetura Híbrida Recomendada

O padrão que maximiza qualidade, custo e latência é um **fast path com fallback para LLM**, aplicado especificamente aos steps de classificação e extração:

```
mensagem do cliente
     ↓
classificador leve  (< 10 ms, local — Gemma 2B / DistilBERT)
     ↓  confiança ≥ 0,85?
    sim  →  usa resultado direto
    não  →  escala para LLM  (Gemma local ou Claude via AI Gateway)
```

| Camada | Latência alvo | Cobertura esperada | Descrição |
|---|---|---|---|
| Fast path | < 10 ms | 75–85% | Modelo leve local — extração + classificação de alta confiança |
| LLM fallback | 200–800 ms | 15–25% | LLM (Gemma local ou Claude via AI Gateway) — casos ambíguos + geração |

Em atendimentos de SAC com vocabulário de domínio controlado, a expectativa é que 75–85% das mensagens sejam resolvidas no fast path. As chamadas ao LLM ficam restritas aos casos genuinamente ambíguos e às etapas de geração de texto — reduzindo custo de inferência e latência sem comprometer qualidade nos casos simples, que são a maioria.

---

## 5. Gemma e SSLMs no Contexto deste Piloto

### 5.1 Gemma 2B / 7B

Gemma é um modelo aberto do Google, disponível para execução local sem dependência de API externa. Com system prompt bem estruturado e exemplos few-shot em português, o Gemma 2B cobre os steps de extração e classificação satisfatoriamente em ambiente de piloto. Para produção em escala, fine-tuning no corpus histórico de atendimentos do cliente é o que fecha a lacuna de 10–15% nos casos de borda.

> **Vantagem operacional:** Gemma rodando on-premise elimina latência de rede, custo por token e dependência de disponibilidade de API externa — especialmente relevante para o fast path onde o volume de chamadas é alto.

### 5.2 SSLMs — Small Specialized Language Models

SSLMs são modelos menores treinados ou fine-tunados em um domínio específico. No contexto de SAC de telecomunicações, um modelo de ~110M parâmetros fine-tunado no corpus de atendimentos históricos da operadora tende a superar modelos genéricos maiores nas tarefas dentro da distribuição de treino, com latência e custo de inferência muito menores.

- Vocabulário fechado e previsível: cancelamento, portabilidade, reclamação técnica, upgrade
- Saída estruturada: classificação + score de confiança nativos
- Latência < 10 ms em CPU, viabilizando o fast path
- Custo de fine-tuning amortizado rapidamente em volumes de atendimento típicos de telco

---

## 6. Aplicação ao Fluxo `agente_contexto_ia_v1`

O step `avaliar_contexto` — ponto de entrada do agente especialista — toma a decisão de estratégia entre quatro saídas possíveis: `completo`, `buscar_crm`, `coletar`, `confirmar`. Essa é fundamentalmente uma **classificação de estado do pipeline com quatro classes**, não um raciocínio livre.

> **Candidato natural ao fast path:** `avaliar_contexto` pode ser implementado como um classificador leve que avalia presença, ausência e nível de confiança dos campos do `ContactContext`. O LLM entra apenas como fallback quando o classificador retorna baixa confiança ou quando o step requer geração de texto (`gerar_pergunta`, `resumo_conversa`).

### 6.1 Steps candidatos ao fast path

- `avaliar_contexto` — classificação de estratégia (4 classes)
- Extração de CPF / conta — regex
- Extração de nome — NER
- Classificação de intenção e motivo — classificador fine-tuned
- Scoring de sentimento — classificador de 3 classes

### 6.2 Steps que requerem LLM

- `gerar_pergunta` — geração de texto natural em português
- `resumo_conversa` — sumarização aberta
- `avaliar_contexto` (fallback) — casos de baixa confiança ou intenção composta
- `confirmar_incertos` — formulação de confirmação natural ao cliente

---

## 7. Conclusão

A resposta à questão original é: **depende do step**. Extração de campos estruturados e classificação de intenção são tarefas onde modelos menores e especializados — Gemma, DistilBERT, spaCy NER — entregam qualidade equivalente ou superior ao LLM genérico, com latência e custo significativamente menores.

O LLM permanece necessário e insubstituível para as etapas generativas (formulação de perguntas, sumarização) e como safety net para os casos que fogem da distribuição do modelo classificador.

A arquitetura híbrida — fast path com modelo leve, fallback para LLM — é o padrão que melhor equilibra qualidade, custo e latência para o volume e perfil de atendimento típico de um SAC de telecomunicações em produção.
