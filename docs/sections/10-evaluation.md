# 10. Módulo de Avaliação de Agentes

> Fonte: PlugHub spec v24.0

10. Módulo de Avaliação de Agentes

Extensão nativa do Agent Quality Score existente. O AQS mede performance
objetiva e automática em tempo real — resolution_rate, handle_time,
escalation_rate, sentiment. O Módulo de Avaliação opera em camada acima,
de forma assíncrona e qualitativa, sobre os mesmos dados que a
plataforma já produz: transcrições, context packages, outcomes, sinais
de escalação, histórico de handoffs. Nenhuma integração adicional.
Planejado para o Horizonte 2.

10.1 Evaluation Templates

Formulários de avaliação configurados pelo operador. Cada template
define critérios, pesos por critério, escala de notas e escopo de
aplicação — pool específico, tipo de agente, canal ou categoria de
atendimento. Múltiplos templates podem ser aplicados ao mesmo
atendimento em paralelo: um com foco em eficiência operacional, outro em
experiência do cliente, outro em conformidade regulatória.

Exemplo de template:

aderência_ao_script peso: 0.15

empatia_e_tom peso: 0.20

resolução_efetiva peso: 0.25

uso_correto_de_ferramentas peso: 0.20

conformidade_com_políticas peso: 0.20

Como todos os agentes operam sob o mesmo contrato de execução, o mesmo
template pode ser aplicado a agentes de IA de frameworks diferentes e a
agentes humanos — na mesma escala, com os mesmos critérios. Isso permite
comparar IA vs humano no mesmo tipo de atendimento e comparar pools
distintos com evidência qualitativa, não só volumétrica.

10.2 Evaluation Agent

Agente de IA registrado no pool com tipo arquitetural evaluator. Opera
de forma assíncrona após o encerramento de cada atendimento — sem
impacto no pipeline de atendimento ativo. Recebe: transcrição completa,
context_package, outcome, métricas do AQS e template de avaliação
aplicável. Produz: nota por critério, nota consolidada, justificativa
textual por critério e flags de atenção identificados.

A amostragem é configurável por pool: 100% dos atendimentos, percentual
aleatório, ou regras direcionadas — todos os atendimentos com sentiment
negativo, todos com escalação, todos de agentes novos no pool. A
amostragem é o controle principal de custo operacional do módulo.

10.3 Reviewer Agent

Agente de IA com tipo arquitetural reviewer. Opera sobre as avaliações
produzidas pelo Evaluation Agent — não sobre as transcrições originais,
custo significativamente menor por atendimento. Responsabilidade única:
avaliar a qualidade da avaliação. Identifica notas inconsistentes com o
histórico do avaliador, justificativas genéricas ou insuficientes, casos
limítrofes na fronteira entre faixas, e divergência significativa entre
múltiplos templates aplicados ao mesmo atendimento.

Classifica cada avaliação em:

  -------------------------------------------------------------------------
  Classificação   Significado            Próximo Passo
  --------------- ---------------------- ----------------------------------
  AUTO_APPROVED   Avaliação consistente  Nota incorporada ao perfil do
                  com histórico e        agente sem revisão humana
                  critérios              

  NEEDS_REVIEW    Sinalizada por         Encaminhada para Human Review
                  inconsistência ou caso Queue
                  limítrofe              

  DISPUTED        Divergência crítica    Human Review Queue com prioridade
                  entre templates ou com — requer revisão imediata
                  histórico              
  -------------------------------------------------------------------------

Aprovações e ajustes humanos retroalimentam o Reviewer Agent — o padrão
do que supervisores aprovam ou corrigem melhora a calibração do revisor
ao longo do tempo.

10.4 Human Review Queue

Esteira de aprovação para avaliações sinalizadas pelo Reviewer Agent.
Extensão da interface do supervisor — não um sistema separado. O
supervisor recebe: transcrição original, avaliação produzida pelo
Evaluation Agent, justificativa do Reviewer Agent para a sinalização e
histórico de avaliações anteriores do agente avaliado. Pode aprovar,
ajustar notas ou rejeitar a avaliação.

10.5 Fluxo Completo

Atendimento encerra → agent_done registrado no Kafka

↓ Evaluation Engine verifica templates aplicáveis e amostragem

↓ Evaluation Agent recebe: transcrição + context_package

+ outcome + métricas AQS + template

↓ Evaluation Agent produz: notas por critério + nota consolidada

+ justificativas + flags de atenção

↓ Reviewer Agent recebe a avaliação produzida

↓ Reviewer Agent classifica:

AUTO_APPROVED → nota incorporada ao perfil do agente

NEEDS_REVIEW / DISPUTED → Human Review Queue

↓ Supervisor revisa → aprova / ajusta / rejeita

↓ Resultado final incorporado ao perfil do agente

↓ Decisão humana retroalimenta calibração do Reviewer Agent

10.6 Integração com a Arquitetura Existente

Evaluation Agent e Reviewer Agent são agentes registrados no pool —
mesmo contrato de execução, mesmo ciclo de vida via
mcp-server-omnichannel. Dois novos tipos arquiteturais adicionados ao
Agent Registry: evaluator e reviewer.

O Evaluation Engine é um componente leve que consome eventos agent_done
do Kafka, verifica amostragem e templates aplicáveis, e enfileira o
pacote de avaliação. Avaliações armazenadas no ClickHouse existente —
sem novo banco de dados.

O mcp-server-omnichannel ganha um quarto grupo de tools — Grupo
Avaliação:

  ---------------------------------------------------------------------------------
  Tool                         Função
  ---------------------------- ----------------------------------------------------
  evaluation_template_create   Cria novo template de avaliação com critérios, pesos
                               e escopo de aplicação

  evaluation_template_update   Atualiza template existente. Versão anterior
                               preservada para continuidade histórica

  evaluation_results           Consulta notas e justificativas por agente, pool ou
                               período. Suporta comparação entre pools e entre
                               templates

  review_queue_status          Retorna pendências na Human Review Queue com
                               classificação do Reviewer Agent e prioridade
  ---------------------------------------------------------------------------------

