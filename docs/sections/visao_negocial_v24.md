# PlugHub
### *where agents work as a team*

**Visão Negocial**

Agentes de IA + Agentes Humanos com MCP · Versão 24.0 · Março 2026

| **Versão** | **Data** | **Canais** | **Status** |
|---|---|---|---|
| 24.0 — Greenfield | 20/03/2026 | WhatsApp · SMS · Chat · Email · Voz | Revisão — Bring-Your-Agent, Assistência ao Humano, sem faseamento |

---

## 1. Um Momento de Virada Tecnológica

Durante décadas, a terceirização do atendimento ao cliente foi uma decisão econômica racional: infraestrutura cara, sistemas complexos, gestão de equipe em escala — o BPO resolvia tudo isso transferindo custo fixo para custo variável. Com agentes de IA, essa equação muda estruturalmente. Pela primeira vez, sistemas automatizados conseguem raciocinar sobre contexto, usar ferramentas externas, lidar com ambiguidade e conduzir conversas complexas com autonomia real — não é uma evolução incremental dos chatbots baseados em regras, é uma mudança de arquitetura. O custo de entrada para operar atendimento próprio cai não por redução de salários, mas porque parte do trabalho que exigia volume humano passa a ser executado por agentes que escalam sem custo marginal proporcional. BPOs que quiserem continuar relevantes precisam evoluir — de fornecedores de mão de obra para operadores de plataformas de agentes que entregam resultado, não apenas presença.

Essa transição está acontecendo de forma descentralizada. Times diferentes dentro das mesmas empresas estão construindo agentes em frameworks distintos — LangGraph, CrewAI, Azure AI Agent Service, SDKs proprietários — para domínios diferentes, sem padrão comum de integração ou avaliação. O Model Context Protocol, lançado pela Anthropic em 2024 e rapidamente adotado pela indústria, emergiu como resposta: um protocolo aberto que permite a agentes se conectarem a sistemas externos e entre si, independente de quem os criou — da mesma forma que o HTTP se tornou o padrão da web. O resultado prático é que a maioria das empresas que avaliam uma plataforma de atendimento com IA já tem agentes. Não estão começando do zero — estão tentando conectar o que construíram sem perder o investimento feito. As plataformas omnichannel existentes não resolvem esse problema — e o PlugHub foi construído exatamente para preencher essa lacuna: enquanto as soluções tradicionais foram projetadas antes dessa mudança e tratam agentes de IA como mais um canal — integrados por fora, sem acesso nativo ao contexto do processo, sem visibilidade sobre o que fazem e sem capacidade de ser avaliados com consistência.

Há ainda uma dimensão frequentemente negligenciada nessa transição: a qualidade. As ferramentas analíticas das plataformas atuais medem eficiência operacional — volume de atendimentos, tempo médio de resolução, taxa de abandono. Com agentes de IA, a pergunta relevante muda: não é quanto o agente atendeu, mas quão bem ele resolveu — e se a resolução foi adequada ao contexto do cliente, aderente às políticas da empresa e comparável a outros agentes operando no mesmo ambiente. Essa avaliação exige instrumentação nativa, construída desde a origem. Add-ons analíticos sobre plataformas que não foram projetadas para isso não resolvem o problema — apenas criam mais silos de dados.

---

## 2. O Que é Esta Plataforma

O PlugHub é um AI/Human Agent Hub — uma camada de controle centralizada que conecta, avalia, governa e orquestra agentes de IA e humanos sobre um único núcleo operacional. Diferentemente de ferramentas que automatizam tarefas isoladas, o PlugHub opera de forma transversal: qualquer agente, de qualquer origem, passa a funcionar com os mesmos padrões de integração, qualidade e visibilidade.

O resultado prático é que uma empresa não precisa escolher entre os agentes que já construiu e uma nova plataforma. Traz o que tem, conecta o que falta, e opera tudo sob controle unificado — sem reescrita, sem lock-in, sem perda do investimento feito.

*Esta seção apresenta uma visão geral das cinco capacidades do PlugHub. Cada uma é detalhada nas seções 2.1 a 2.9.*

**As capacidades que o PlugHub entrega:**

**Discovery & Management.** Todos os agentes — internos, externos ou de terceiros, em qualquer framework — são registrados num repositório central com gestão de ciclo de vida. A compatibilidade é certificada antes do deploy e a portabilidade é garantida pelo protocolo: o agente pode ser desconectado e reconectado a qualquer sistema MCP sem modificação.

**Evaluation & Simulation.** Cada atendimento é avaliado automaticamente com templates configuráveis por pool — os mesmos critérios aplicados a agentes de IA de qualquer framework e a agentes humanos. Uma camada de revisão por IA opera sobre as avaliações, identifica inconsistências e retroalimenta a calibração ao longo do tempo. Qualidade não é um add-on — é parte da operação desde o primeiro dia.

**Governance & Security.** Ambientes de trabalho isolados garantem que dados de departamentos e clientes distintos nunca se cruzam. Mecanismos de proteção automática isolam falhas de sistemas externos sem interromper a operação. Autenticação e conformidade regulatória (LGPD/GDPR) são delegadas ao sistema de identidade do operador — a plataforma entrega os sinais, o operador mantém o controle.

**Workflow Automation.** O PlugHub suporta dois modelos de orquestração, adequados à complexidade do processo. Fluxos mais simples são declarados nativamente como grafos de orquestração multi-agente, sem dependência de ferramenta externa. Fluxos mais complexos, já modelados em sistemas de BPM de mercado como Camunda, Pega ou IBM BPM, integram o PlugHub como executor: o processo externo define contexto, sequência e condições de ramificação, e recebe o outcome de volta — sem precisar ser reconstruído dentro da plataforma. Em ambos os modelos, regras de escalação e prioridade são configuráveis por tenant, e o módulo outbound fecha o ciclo proativo com contato ativo, pendência oportunista e entrega contextualizada.

**Context & Memory.** Estado de sessão compartilhado em tempo real entre todos os componentes, sem coordenação explícita. Bases de conhecimento setorial — regulações, terminologia, processos — são refinadas continuamente a partir de todas as interações em produção. Um ativo que cresce com o uso e não se perde quando agentes ou modelos são substituídos.

**O diferencial:**

O PlugHub funciona como um clube de esporte profissional, tem coach para seu time de agentes, que monta estratégias de atendimento e escala os agentes nas posições de melhor rendimento, tem Performance Analysis para estudar os dados estatísticos e avaliar a qualidade, efetividade e desempenho de seus agentes IA e humanos de forma padronizada nos diversos cenários práticos de atuação e gerando informações para reciclagem e curadoria. Você traz o elenco que já tem, conecta o que falta, e opera tudo sob um único sistema de controle. Os dados pertencem a você. O lock-in não existe porque o contrato de integração é aberto. O time-to-operation se mede em semanas porque a infraestrutura já está pronta.

---

## 2.1 Canais Suportados

| **Canal** | **Latência Tolerada** | **Características** |
|---|---|---|
| Voz | < 1.500ms | Barge-in, turn-taking, respostas curtas (≤ 2 frases), TTS |
| WhatsApp / SMS | < 5s | Templates aprovados, mídia, limite de caracteres SMS |
| Chat Web/App | < 3s | Formatação leve, até 3 opções, links |
| Email | < 2h | Respostas longas, formatação HTML, sem urgência de latência |
| WebRTC — Voz/Texto/Vídeo | < 3.000ms (estabelecimento) | Canal unificado com capacidades adaptativas. Agentes de IA atendem voz e texto via WebRTC sem alteração de pipeline. Vídeo bidirecional exclusivo para agentes humanos. Degradação graciosa automática: vídeo → voz → texto na mesma sessão. |

---

## 2.2 Estrutura Modular: Inbound e Outbound

A plataforma é organizada em dois módulos operacionais independentes — Inbound e Outbound — sobre um núcleo compartilhado. Essa decisão tem implicações diretas em deploy, risco operacional e roadmap.

| **Dimensão** | **Inbound** | **Outbound** |
|---|---|---|
| Iniciativa | Reativo — cliente inicia | Proativo — sistema inicia |
| Padrão de carga | Fluxo contínuo e previsível | Rajadas por campanha |
| Impacto de falha | Afeta cliente que pediu ajuda — urgente | Afeta cliente que não solicitou nada — menos urgente |
| Risco regulatório | Baixo — cliente iniciou o contato | Alto — LGPD, opt-out, base legal obrigatórios |
| Blast radius de bug | Contido ao canal afetado | Pode disparar milhares de contatos indevidos |

Ambos os módulos compartilham infraestrutura comum: Pool de Agentes, MCP Servers, Kafka, Redis, ClickHouse, Eval Pipeline e Customer Context. O módulo outbound possui circuit breaker automático que o desabilita sem afetar o inbound em situações de risco LGPD, taxa de opt-out elevada ou saturação do pool de agentes.

---

## 2.3 Outbound — Contact Engine e Entrega Oportunista

O módulo outbound vai além do disparo de campanhas. É composto por dois subsistemas que fecham o ciclo completo do contato proativo.

O Contact Engine gerencia o contato ativo — tenta alcançar o cliente nos canais suportados com dois modelos de acionamento: orquestrado por BPM, onde o processo de negócio define contexto, sequência de tentativas e recebe o outcome de volta; e campanhas autônomas configuradas diretamente na plataforma para casos que não justificam um processo orquestrado. Quando o contato ativo não é estabelecido, o ciclo não encerra — uma pendência é criada automaticamente para entrega oportunista.

O Pending Delivery Store é um banco de pendências por cliente com prazo de validade. Qualquer sistema pode inserir pendências — BPM, Contact Engine após falha de contato, CRM ou ERP diretamente. O Notification Agent, um agente de IA nativo da plataforma, detecta essas pendências e as entrega nos momentos certos: no início de cada conversa inbound, durante a fila de espera por agente humano, ou em slots configuráveis pelo operador no fluxo de atendimento. Quando o cliente aceita uma oferta, o Notification Agent aciona o agente especializado configurado para aquela tratativa — sem replicar lógica de negócio, sem inconsistência.

O diferencial em relação às plataformas atuais é o fechamento do ciclo: o outbound tenta o contato ativo → se não consegue, aguarda o momento certo → entrega com contexto → aciona o agente especializado → confirma a entrega no fluxo original. Nenhuma etapa é perdida, nenhum sistema precisa ser consultado manualmente para saber o que aconteceu.

---

## 2.4 Módulo de Avaliação de Agentes

A promessa de avaliação qualitativa uniforme entre agentes de diferentes frameworks — e entre IA e humanos — exige mais do que métricas automáticas de performance. O Módulo de Avaliação entrega isso de forma nativa, sem ferramentas externas de quality assurance.

O operador configura templates de avaliação com critérios e pesos — aderência ao script, empatia, resolução efetiva, conformidade com políticas — por pool. Um Agente Avaliador de IA lê cada atendimento encerrado e preenche o formulário de forma assíncrona, produzindo notas por critério e justificativas textuais. O mesmo template pode ser aplicado a agentes de IA de qualquer framework e a agentes humanos — mesma escala, mesmos critérios. Isso permite comparar pools distintos com evidência qualitativa, não só volumétrica, e tomar decisões de automação com dados concretos.

Um Agente Revisor opera sobre as avaliações produzidas — não sobre as transcrições originais — e identifica inconsistências, casos limítrofes e divergências. Classifica cada avaliação em aprovada automaticamente ou encaminhada para revisão humana. O supervisor recebe apenas o que o revisor sinalizou, com contexto completo para decidir. Aprovações e ajustes humanos retroalimentam a calibração do revisor ao longo do tempo — a esteira fica mais precisa com o uso.

O resultado é um ciclo completo de qualidade: atendimento → avaliação automática → revisão por IA → aprovação humana proporcional ao risco — tudo dentro da plataforma, sobre os dados que ela já produz.

---

## 2.5 Gestão de Conhecimento

O conhecimento de negócio — políticas, produtos, procedimentos, regulações — é o ativo central de qualquer operação de atendimento. A plataforma gerencia esse ativo de forma nativa, organizado por domínios independentes e com dois modos de alimentação complementares.

O operador define domínios de conhecimento — Produto, Cobrança, Regulatório, Procedimentos Internos, por exemplo. Cada domínio é uma base vetorial independente, acessível exclusivamente pelos tipos de agente autorizados. Um agente de cobrança não consulta a base de produto — e vice-versa. O campo `valid_until` em cada entrada garante expiração automática de conhecimento com prazo — promoções, regulações temporárias, campanhas — sem intervenção manual.

A alimentação da base funciona em dois modos. Via interface, o operador cria, edita e versiona entradas diretamente — adequado para atualizações cotidianas por analistas de conteúdo sem necessidade de engenharia. Via portal de scripts, o operador instrui um agente de IA a processar um documento — PDF, página interna, exportação de sistema — que extrai, estrutura e gera o script de ingestão para revisão e execução. Esse segundo modo resolve o problema de onboarding inicial e de sincronização com bases de conhecimento existentes sem exigir estruturação manual de cada entrada.

### 2.5a A Base de Conhecimento que se Auto-Diagnostica

Toda consulta que o agente faz à base de conhecimento é registrada e cruzada com o desfecho da conversa. Isso gera um ciclo de melhoria contínua sem dependência de revisão manual periódica: a plataforma detecta automaticamente intents sem cobertura, artigos que não resolvem, artigos não consultados e conhecimento fragmentado. O output é uma fila de trabalho acionável para o time de curadoria — não apenas relatórios.

### 2.5b Bases de Conhecimento Verticais

O módulo de gestão de conhecimento resolve o problema de como o operador organiza e mantém seu conhecimento de negócio. As bases de conhecimento verticais resolvem um problema anterior: o custo e o tempo de construir esse conhecimento do zero para domínios onde grande parte do conteúdo relevante é setorial — regulações, terminologia, produtos típicos, processos comuns — e não específico de nenhum operador individual.

A plataforma oferece bases pré-populadas para as principais verticais de atendimento. Cada base é um ponto de partida estruturado — não um produto acabado. O operador que inicia uma operação de telco não começa com domínios vazios que precisa preencher entrada por entrada. Começa com uma base que já conhece o vocabulário do setor, as regulações vigentes, os processos típicos de portabilidade, cancelamento e suporte técnico. Complementa com o que é específico do seu negócio — produtos próprios, políticas internas, campanhas — e assume a responsabilidade pelo conteúdo que coloca em produção.

**Duas camadas, responsabilidade clara**

Cada base vertical opera em duas camadas complementares e independentes.

A camada base contém conhecimento setorial — regulações, glossário técnico, processos típicos do setor, produtos comuns. É compartilhada entre todos os operadores da vertical e mantida como conteúdo de referência. O operador lê, não escreve. A plataforma não garante a precisão ou atualidade desse conteúdo — ele é um acelerador de onboarding, não uma fonte normativa. O operador é responsável por validar o que usa.

A camada específica contém conhecimento do operador — produtos próprios, políticas internas, procedimentos específicos, campanhas ativas. É completamente privada — nenhum outro operador tem acesso. O operador escreve, mantém e é inteiramente responsável por esse conteúdo.

O agente consulta as duas camadas de forma transparente. O Knowledge Base Analytics distingue a procedência de cada consulta, permitindo ao operador avaliar quanto o conteúdo base está resolvendo e onde o conteúdo específico é necessário.

**Verticais disponíveis**

| **Vertical** | **Conteúdo base típico** |
|---|---|
| Telecomunicações | Regulações ANATEL, processos de portabilidade, glossário técnico de rede, direitos do consumidor no setor |
| Saúde e Planos | Regulações ANS, terminologia clínica de atendimento, processos de autorização e elegibilidade, direitos do beneficiário |
| Serviços Financeiros | Regulações BACEN e CVM, terminologia de produtos financeiros, processos de cobrança e acordo, direitos do consumidor financeiro |
| Varejo e E-commerce | Código de Defesa do Consumidor, processos de troca e devolução, logística reversa, terminologia de marketplace |
| Utilities | Regulações ANEEL e ANA, processos de religação e medição, direitos do consumidor de energia e saneamento |

O conteúdo base não substitui o conhecimento específico do operador — ele cobre o que é comum ao setor. Um operador de plano de saúde que oferece um produto com carências específicas precisa declarar essas carências na camada específica. O conteúdo base sabe o que é uma carência e o que a ANS regula sobre ela — o operador declara o que é específico do seu plano.

**O ativo que cresce com o uso**

O Knowledge Base Analytics da plataforma — descrito na seção 2.5a — opera sobre todas as consultas de todos os operadores da mesma vertical. Os sinais agregados — intents sem cobertura, artigos com baixa eficácia, gaps detectados — alimentam a evolução da camada base ao longo do tempo.

Um operador que entra no segundo ano de operação da plataforma herda uma base mais refinada do que a que existia no primeiro ano — corrigida pelos gaps identificados por todos os operadores que vieram antes. O ativo cresce de forma cumulativa e irreversível, de forma análoga ao dado analítico descrito na seção 2.10. O operador que entra cedo contribui para a construção desse ativo. O operador que entra tarde herda mais. Ambos se beneficiam de um conhecimento setorial que nenhum operador individual teria condições de construir sozinho na mesma velocidade.

**O que isso significa para o time-to-value**

Para verticais com alto volume de conhecimento regulatório e setorial — saúde, finserv, telco — a diferença entre partir de uma base vazia e partir de uma base vertical estruturada é medida em semanas de trabalho editorial de onboarding. A base vertical não elimina esse trabalho — reduz sua escala para o que genuinamente precisa ser específico do operador.

O argumento é paralelo ao bring-your-agent da seção 2.7: assim como o operador não precisa construir agentes do zero quando já os tem, não precisa construir conhecimento setorial do zero quando ele é compartilhável. A plataforma acelera o que pode ser acelerado e deixa o operador focado no que é genuinamente diferenciador do seu negócio.

---

## 2.6 Autenticação e Identidade do Cliente

A plataforma não gerencia autenticação — delega integralmente ao sistema de identidade do operador via MCP. Essa decisão é intencional: autenticação tem implicações regulatórias e de segurança que pertencem ao operador, não à plataforma de atendimento.

O `mcp-server-identity` é implementado pelo operador e expõe três capacidades: reconhecimento — resolve o identificador do canal para um `customer_id` canônico, garantindo que o mesmo cliente seja reconhecido independente do canal de entrada; verificação de nível — informa se o cliente está apenas identificado ou autenticado, determinando quais ações sensíveis estão disponíveis no fluxo; e autenticação em fluxo — quando o agente precisa elevar o nível de autenticação para prosseguir com uma ação sensível, aciona o fluxo de verificação do operador que executa o fator adequado e devolve o resultado.

Em canais de voz, o áudio da chamada pode ser enviado como parâmetro para verificação biométrica passiva — sem interromper a conversa. A decisão final é sempre do sistema do operador: a plataforma entrega os sinais disponíveis e consome o resultado, sem conhecer os critérios internos de verificação.

---

## 2.7 Agentes — Bring-Your-Agent

A plataforma não entrega agentes prontos — entrega as condições para qualquer agente funcionar. Essa distinção é o que diferencia o modelo de todos os concorrentes, e tem duas consequências práticas que definem o time-to-market real do operador.

**Reaproveitamento — o catálogo já existe**

A maioria das empresas que avaliam esta plataforma já tem agentes construídos ou em construção — para CRM, para BPM, para automações internas, para provas de conceito que nunca chegaram a produção, e inclusive agentes hospedados em ambientes proprietários como AWS Bedrock Agents, Google Agent Builder ou Microsoft Copilot Studio. O operador não precisa reconstruir esses agentes para a plataforma. Agentes com código controlável são conectados via SDK de Integração. Agentes em ambientes proprietários podem ser regenerados como agentes nativos portáveis a partir dos artefatos fonte que esses ambientes expõem — instrução, schema de tools, fluxos declarativos — ou integrados via Wrapper Agent enquanto a versão nativa é construída. O tempo-a-operação não é o tempo de construir um agente do zero — é o tempo de conectar ou converter o que já existe.

**Portabilidade — o investimento pertence ao operador**

Agentes conectados via contrato MCP aberto continuam funcionando fora da plataforma. O contrato de execução não cria dependência do runtime da plataforma — um agente que implementa o SDK pode ser desconectado e reconectado a qualquer outro sistema que fale MCP sem modificação. O que foi construído ou adaptado pode ser reutilizado em outros contextos, outras plataformas, outros sistemas da empresa.

> *"Se eu construir meus agentes aqui, o que acontece se eu quiser sair?"*

A resposta é estrutural: o agente não é construído para a plataforma — é conectado a ela. A conexão pode ser desfeita sem perda do investimento.

**Verticais e horizontais — organização do catálogo**

Agentes se organizam naturalmente em duas categorias. Agentes verticais são especializados num domínio de negócio de uma indústria específica — um agente de diagnóstico técnico para telco, um agente de elegibilidade para saúde, um agente de cobrança para finserv. Seu valor está no conhecimento do domínio e não faz sentido fora dele.

Agentes horizontais resolvem problemas que aparecem em múltiplos domínios sem precisar conhecer o domínio — extração de documentos, autenticação avançada, análise de sentiment, resumo de contexto entre agentes. Eles servem qualquer vertical que precise da capacidade.

A separação não é apenas organizacional. Agentes verticais delegam subtarefas para agentes horizontais via protocolo A2A — sem precisar conhecer a implementação. Um agente de retenção detecta um documento anexado pelo cliente e delega silenciosamente para o agente de extração. O agente horizontal processa e devolve o resultado estruturado. O agente vertical usa a informação para continuar a conversa — sem o cliente saber que outro agente foi acionado. Quando dois verticais diferentes precisam da mesma capacidade horizontal, eles compartilham o mesmo agente — sem reescrita, sem fork, sem drift de comportamento entre implementações.

**SDK de Integração — o mecanismo de conexão**

O SDK de Integração opera em ambas as direções — entrada e saída. Na direção de entrada, traduz o contrato de execução da plataforma para a linguagem do agente que está sendo conectado. Na direção de saída, garante que agentes construídos nativamente não acumulam dependências implícitas da plataforma — podendo ser portados para qualquer outro sistema MCP sem modificação. Cobre nove responsabilidades: ciclo de vida e protocolo, adaptação de schema de contexto via interface única e bidirecional, declaração de capabilities para discovery e delegação A2A, certificação de compatibilidade antes do deploy, propagação automática de observabilidade, interface de portabilidade nativa, regeneração assistida de agentes proprietários, verificação de portabilidade, e extração de skills de agentes existentes. O agente não sabe que está falando com a plataforma — e a plataforma não fica presa ao agente.

**Skills — capacidades empacotadas e reutilizáveis**

Além de trazer agentes, o operador pode empacotar capacidades especializadas como skills — unidades que combinam instrução especializada, tools necessárias, bases de conhecimento e critérios de avaliação para um domínio específico. Uma skill de portabilidade de telco, por exemplo, empacota tudo que qualquer agente precisa para conduzir um processo de portabilidade — independente de quem construiu o agente ou em qual framework. Vários agentes diferentes referenciam a mesma skill, e o comportamento é consistente porque a fonte é a mesma.

Skills de orquestração vão além: empacotam o fluxo de coordenação entre múltiplos agentes como um grafo declarativo de steps. Um agente orquestrador interpreta o flow da skill e coordena os agentes especializados em sequência — delegando subtarefas via A2A, derivando para pools humanos quando necessário, tratando falhas com retry e fallback antes de escalar, e retomando do ponto onde parou em caso de interrupção. O fluxo de negócio é declarado na skill — não hardcoded no agente.

---

## 2.8 Assistência ao Agente Humano

A plataforma não trata agentes humanos como receptores passivos de conversas escaladas. O agente humano tem acesso em tempo real ao estado observável de cada conversa e às capacidades disponíveis para aquele momento — apresentadas pelo Agent Assist como dois painéis dinâmicos.

**Painel de estado** — exibe a trajetória de sentiment do cliente ao longo da conversa, o intent atual com grau de confiança, os flags ativos — sinal de churn, tópico sensível, limite de autoridade atingido — e o status de SLA. O dado é lido diretamente do estado da sessão, atualizado pelo AI Gateway a cada interação. O agente humano vê o que a plataforma sabe sobre a conversa naquele instante.

**Painel de capacidades** — exibe as tools MCP, os agentes IA disponíveis e as escalações possíveis, filtrados por relevância ao assunto em andamento. O agente não vê o catálogo completo — vê o que faz sentido para o momento. Uma conversa de portabilidade mostra as tools de portabilidade e os agentes especializados naquele domínio. Uma conversa com sinal de churn mostra as ferramentas de retenção e os agentes com capability de retenção disponíveis.

**Conferência IA + humano**

Agentes IA podem entrar na conversa como participantes ativos — não substituindo o agente humano, mas participando junto. O agente humano permanece presente durante toda a conferência, vê tudo e pode intervir a qualquer momento. O cliente conversa com ambos no mesmo canal.

O modelo de conferência funciona uniformemente para todos os canais. Em voz, o agente IA fala com perfil de voz distinto do agente humano — o cliente ouve dois interlocutores, cada um identificado. Em texto, as mensagens chegam com identidade visual distinta. A infraestrutura de STT/TTS já normaliza a interação para o mesmo envelope de eventos independente da mídia — do ponto de vista do agente IA, o canal é transparente.

O impacto operacional é direto: o agente humano deixa de ser o gargalo de capacidade. Ele orquestra múltiplos atendimentos simultaneamente — conduz a conversa principal enquanto agentes IA cobrem trechos específicos em paralelo. Em vez de atender uma conversa por vez, ele supervisiona e intervém onde sua capacidade humana genuinamente agrega.

### 2.8a Orquestração Multi-Agente

Processos de negócio complexos raramente são resolvidos por um único agente. Um onboarding de cliente financeiro passa por verificação de identidade, análise de crédito, oferta de produto e formalização — cada etapa com um agente especializado. Um diagnóstico técnico de telco pode exigir consulta a sistemas diferentes antes de propor a solução. A plataforma suporta esse padrão nativamente via orquestração multi-agente.

O operador declara o fluxo de coordenação como uma skill de orquestração — um grafo declarativo de steps que descreve quais agentes são chamados em qual ordem, com quais condições de ramificação, e o que fazer quando um agente falha. Um agente orquestrador interpreta esse flow e coordena os demais — sem que o fluxo de negócio esteja hardcoded em nenhum agente individual.

**Oito tipos de step cobrem qualquer fluxo de negócio real:**

| **Step** | **O que faz** |
|---|---|
| `task` | Delega subtarefa para agente com a skill declarada via protocolo A2A |
| `choice` | Ramificação condicional baseada no resultado do step anterior |
| `catch` | Trata falha de step anterior — retry automático, fallback para capacidade alternativa, e escalação após esgotar estratégias. Equivalente a um try-catch declarativo. |
| `escalate` | Deriva para pool humano ou especializado via Motor de Regras, com contexto completo do pipeline |
| `complete` | Encerra o pipeline com outcome definido |
| `invoke` | Chama uma tool de MCP Server diretamente e persiste o resultado no pipeline — sem precisar de um agente intermediário para operações simples como consultar um sistema ou emitir um documento |
| `reason` | Invoca o AI Gateway com um prompt declarado e retorna uma decisão estruturada em JSON — permite ao orquestrador tomar decisões que não são expressíveis como condições booleanas, como classificar uma intenção ou avaliar elegibilidade |
| `notify` | Envia mensagem ao cliente via Notification Agent com personalização dinâmica — útil para comunicar progresso de etapas longas sem precisar de um agente especializado para mensagens simples |

O estado do pipeline é persistido a cada transição de step. Se o orquestrador é interrompido — por falha, timeout ou reinicialização por deploy — uma nova instância retoma exatamente do ponto onde parou, incluindo contadores de retry e resultados dos steps anteriores. O cliente não percebe a interrupção.

Como o flow é declarado na skill e não no agente, atualizar o fluxo de um processo de negócio — adicionar uma etapa regulatória, mudar a condição de escalada, inserir um agente de fallback — não exige redeployar o agente orquestrador. A skill é atualizada e todos os orquestradores que a referenciam recebem o novo fluxo automaticamente.

O mesmo vocabulário de steps que permite orquestrar múltiplos agentes também permite criar agentes nativos completos sem nenhuma linha de código. Um agente de onboarding financeiro, por exemplo, pode consultar o CRM via `invoke`, classificar o perfil do cliente via `reason`, comunicar o status ao cliente via `notify`, e acionar agentes especializados de análise e formalização via `task` — tudo declarado no flow da skill, sem que o operador precise escrever ou manter código de agente.

Isso muda o perfil de quem pode criar agentes na plataforma. Processos com fluxo bem definido — onboarding, portabilidade, cobrança estruturada, elegibilidade — podem ser modelados por analistas de negócio com suporte das ferramentas de composição da plataforma. A fronteira entre configurar um processo e desenvolver um agente se desloca: o agente é a skill declarada, e a skill pertence ao operador.

---

## 2.9 Visibilidade Executiva e Workspaces

A unidade de isolamento dentro de um tenant é o workspace. O operador cria workspaces por departamento, linha de negócio ou qualquer critério operacional — cada workspace tem seus próprios pools de agentes, bases de conhecimento, templates de avaliação e dashboards. Dados de um workspace nunca são visíveis em outro, mesmo dentro do mesmo tenant.

Esse modelo resolve dois problemas simultaneamente: o sigilo entre departamentos de uma mesma empresa — cobrança não vê dados de produto — e o isolamento entre clientes de um BPO que opera múltiplos clientes finais, onde cada cliente é um workspace ou tenant separado conforme o grau de isolamento necessário.

Os dashboards são configuráveis pelo operador por workspace. A plataforma entrega widgets prontos — métricas de fila em tempo real, performance de agentes por pool, comparativo IA vs. humano, funil de atendimento, SLA por pool, evolução de qualidade — e o operador compõe as visões adequadas a cada perfil. Supervisor operacional acompanha filas e alertas de SLA em tempo real. Gestor de qualidade analisa avaliações e tendências históricas. Executivo acessa visão consolidada de múltiplos workspaces em modo somente leitura — KPIs agregados sem acesso a dados granulares de conversas individuais.

---

## 2.10 Analytics e Inteligência Operacional

A plataforma produz dados analíticos em três camadas. A primeira materializa todos os eventos operacionais em tabelas otimizadas para leitura — nenhum dado se perde quando a sessão expira. A segunda entrega modelos pré-calculados que respondem diretamente às perguntas mais comuns: taxa de resolução por agente e tipo de atendimento, distribuição de sentiment ao longo do funil, ciclo completo de Pending Delivery, tendências de circuit breaker por sistema externo antes de virar incidente. A terceira aplica data mining sobre o volume acumulado: clustering de intents, padrões de jornada que precedem churn ou upgrade, e detecção de anomalia em tempo real.

O dado mais diferenciado que a plataforma produz é a jornada do cliente cross-canal ao longo do tempo. Porque toda a operação passa pelo mesmo núcleo, a plataforma vê sequências de intents entre sessões e canais diferentes que nenhuma ferramenta de analytics externa consegue sem integração pesada. Esse dado existe como subproduto da arquitetura — não exige nenhum investimento adicional para capturar.

O dado analítico acumulado é um ativo que cresce com o tempo de forma irreversível. Trocar de modelo de IA, atualizar um agente, trazer um agente novo de outro contexto — nada disso apaga o histórico. Operadores que entram cedo acumulam mais histórico, modelos mais calibrados e base de conhecimento mais refinada do que entrantes tardios — independentemente do modelo de linguagem ou dos agentes que cada um usa.

---

## 3. Posicionamento de Mercado

A plataforma compete diretamente com Genesys, NICE e Amazon Connect no modelo SaaS. O diferencial é uma combinação que nenhum concorrente oferece simultaneamente.

### 3.1 A Lacuna que Esta Arquitetura Preenche

Concorrentes como Genesys e NICE entregam agentes prontos para domínios pré-definidos, integrados numa plataforma fechada — o operador usa o que o vendor oferece e paga por isso. O PlugHub opera de forma inversa: entrega a infraestrutura com pontos de conexão abertos onde qualquer agente pode ser instalado, incluindo os que o operador já tem. A diferença não é de funcionalidade — é de modelo: o vendor controla o elenco; no PlugHub, o operador é o coach do seu próprio time.

| **Plataforma** | **BPM Aberto** | **MCP Exposto p/ Consumo** | **Multi-Tenant Operável pelo Cliente** | **Base de Conhecimento Multi-Domínio** | **Bring-Your-Agent** |
|---|---|---|---|---|---|
| Genesys Cloud CX | NÃO — BPM interno | NÃO — consome MCP | NÃO — operado pela Genesys | Não incluído | NÃO — framework fechado |
| Amazon Connect | NÃO — Step Functions AWS | NÃO | NÃO — engenharia customizada | NÃO | NÃO — ecossistema AWS |
| NICE CXone Mpower | NÃO — Salesforce Flow | NÃO | NÃO — operado pela NICE | NÃO | NÃO — agentes gerados internamente |
| Twilio Flex | NÃO — sem nativo | SIM — abertura total, build it | NÃO — build it | NÃO — build it | Parcial — sem contrato de execução nem SDK de certificação |
| **PlugHub** | **SIM — agnóstico de engine** | **SIM — tools nativas MCP** | **SIM — híbrido namespace+cluster** | **SIM — por tipo de agente com valid_until** | **SIM — SDK de integração com certificação, portabilidade e regeneração de agentes proprietários** |

A coluna Bring-Your-Agent é o diferencial que as tabelas anteriores não capturavam. Todos os concorrentes têm algum grau de suporte a agentes externos — mas nenhum tem um contrato de execução aberto, um SDK de integração com certificação de compatibilidade, portabilidade garantida pelo protocolo, e regeneração de agentes proprietários como agentes nativos portáveis. O operador que traz um agente para a plataforma sabe exatamente o que está conectando e o que pode desconectar. O operador que tem agentes presos em AWS Bedrock ou Microsoft Copilot Studio tem um caminho de migração para nativos portáveis — sem perder o investimento feito.

### 3.2 Vantagem do Operador SaaS vs. Revenda de Plataforma

| **Dimensão Comercial** | **Genesys / NICE (vendor SaaS)** | **PlugHub (SaaS próprio)** |
|---|---|---|
| Precificação | Vendor define o preço. Cliente paga por seat. | Operador define o preço — margem sobre custo de infra |
| Dado do cliente | Fica no vendor | Fica no operador — diferencial em mercados regulados |
| Modelo de IA | Vendor controla | Operador controla — `model_profile` configurável por tenant |
| Agentes | Vendor entrega o catálogo — o operador usa e paga por eles | Operador traz os seus — sem reescrita, sem lock-in de catálogo. Agentes proprietários regenerados como nativos portáveis. |
| Crescimento de receita | Linear: mais clientes = mais licenças pagas ao vendor | Custo marginal decrescente — mais tenants = mais margem |
| Portabilidade | Alto lock-in — migrar leva 6–18 meses | Agentes portáveis pelo contrato MCP — lock-in pela qualidade, não por dependência técnica |

---

## 4. Análise Comercial e TCO

Análise de unit economics do modelo SaaS multi-tenant, baseada em preços públicos de mercado 2026. O modelo gera margem crescente com escala — cada novo tenant dilui o custo fixo de infraestrutura sem adicionar custo proporcional.

### 4.1 Unit Economics do Operador SaaS

O argumento central do modelo não é TCO de infraestrutura — é a margem que o operador captura ao controlar a precificação para seus clientes finais. A comparação relevante é entre revender capacidade de um vendor (margem zero) e operar plataforma própria (margem crescente com escala).

| **Métrica** | **Revenda Genesys / NICE** | **PlugHub (SaaS próprio)** |
|---|---|---|
| Custo mensal por tenant | US$ 70k–85k (licença ao vendor) | US$ 13k–20k (infra diluída)* |
| Preço cobrado ao cliente | Definido pelo vendor — sem margem real | Definido pelo operador — livre |
| Exemplo: cobrar US$ 35k/tenant | Margem: ZERO (paga mais ao vendor) | Margem bruta: 55–75% |
| Dado do cliente final | Fica no vendor | Fica no operador |
| Agentes | Pertencem ao vendor | Pertencem ao operador — portáveis |
| Crescimento: +1 tenant | Nova licença ao vendor | Custo marginal decrescente |

*\* Custo de infra inclui cloud, APIs e serviços gerenciados. Não inclui equipe de operação do ambiente SaaS — estimada em US$ 8k–15k/mês independente do número de tenants, diluindo com escala. Com 10 tenants, o impacto é de +US$ 0,8k–1,5k/tenant/mês sobre o custo de infra.*

Com 10 tenants ativos a US$ 35k/tenant/mês: receita de US$ 350k/mês contra custo total (infra + operação) de US$ 138k–215k. Margem bruta de 39–61%. Com 50 tenants: receita de US$ 1,75M/mês contra custo de US$ 465k–675k. Margem bruta de 61–73% — o custo operacional cresce sub-linearmente enquanto a receita cresce linearmente.

### 4.2 Projeção de Receita e Margem por Escala de Operador

| **Métrica** | **Genesys (por tenant)** | **PlugHub (custo/tenant, 10 tenants)** |
|---|---|---|
| Custo mensal (infra) | $70k–85k (licença Genesys) | $13k–20k (infra diluída) |
| Custo mensal (operação) | N/A — gerenciado pelo vendor | $0,8k–1,5k (diluindo com escala)* |
| Custo total por tenant | $70k–85k | $13,8k–21,5k |
| Precificação ao cliente | Definida pela Genesys | Definida pelo operador |
| Margem operacional | Zero — cliente paga ao vendor | Diferença entre preço cobrado e custo total |
| Crescimento de tenant | Cada novo tenant = nova licença | Custo marginal decrescente — operação é fixo |

*\* Operação do ambiente SaaS estimada em US$ 8k–15k/mês total (2–3 engenheiros de plataforma), diluindo com número de tenants.*

O custo marginal por tenant é diretamente proporcional ao volume de chamadas LLM — não ao número de tenants nem ao número de agentes conectados, já que agentes externos não adicionam custo de licença.

---

## 5. Riscos Estratégicos

### 5.1 O Risco Central

O ciclo de evolução de LLMs e frameworks está em 3–6 meses. Um roadmap de 18 meses atravessa 4–6 gerações de modelos. O risco não é técnico — é chegar ao mercado com premissas de diferenciação que mudaram.

### 5.2 Riscos por Dimensão

| **Risco** | **Probabilidade (18 meses)** | **Impacto** | **Mitigação** |
|---|---|---|---|
| MCP como diferencial estreitando — Genesys/NICE podem adotar | Alta | Médio — o diferencial estreita mas não desaparece | Lançar antes da janela de 12 meses. Track record operacional supera diferencial técnico. Bring-your-agent é mais difícil de copiar que suporte a MCP. |
| Modelos mais capazes reduzindo necessidade de pool especializado | Média | Baixo — pool simplifica mas versionamento e deploy canário continuam válidos | Anatomia por camadas é agnóstica de modelo. Agentes verticais e horizontais continuam válidos independente da capacidade do modelo base. |
| Novo entrante com arquitetura equivalente | Média | Alto se tiver go-to-market agressivo antes do Mês 12 | Clientes em produção no Mês 6 criam referências que entrante não tem. Catálogo de agentes acumulado pelo operador é ativo que entrante não replica. |
| AI Gateway como ponto único — gargalo potencial sob carga extrema | Baixa | Alto se ocorrer — toda chamada LLM passa por ele | Stateless e horizontalmente escalável. Circuit breaker por provider. Cache semântico reduz chamadas reais. Load balancer distribui entre instâncias. |
| ClickHouse como dependência analítica — complexidade operacional adicional | Média | Médio — analytics degradado, não inbound | Analytics é camada separada — indisponibilidade não afeta atendimento. PostgreSQL particionado é alternativa viável para volume inicial. |

### 5.3 O Que Não Muda — Fundamentos de Longa Duração

| **Elemento** | **Por Que Resiste ao Tempo** |
|---|---|
| Modelo de negócio multi-tenant com custo marginal decrescente | Não depende de nenhuma versão de LLM — estrutura econômica válida independente do modelo |
| Soberania de dado e controle de modelo | Regulação de IA acelera globalmente — o argumento fica mais forte em 18 meses, não mais fraco |
| Contrato MCP com BPM independente de versão de modelo | A interface de tools não muda porque o LLM ficou mais inteligente — o protocolo de integração é estável |
| Bring-your-agent como modelo de adoção | Quanto mais agentes o mercado constrói em frameworks abertos, mais forte fica o argumento de conexão sem reescrita. Skills empacotam capacidades especializadas que qualquer agente pode referenciar — o catálogo de capacidades da plataforma cresce por composição, não por duplicação. O mercado trabalha a favor da plataforma. |
| Skills e orquestração multi-agente via flow declarativo | O conceito de skill como unidade de capacidade reutilizável e o flow declarativo como mecanismo de orquestração são independentes de qualquer modelo de linguagem ou framework específico. Fluxos de negócio complexos exigem coordenação entre especializações — independente de quantas gerações de LLM passarem. |
| Portabilidade de agentes pelo contrato MCP aberto | Regulação de dados e soberania tecnológica tornam portabilidade um requisito crescente, não opcional. Agentes em ambientes proprietários têm caminho de migração para nativos portáveis via regeneração assistida — o lock-in nos vendors de IA é reversível. O argumento fica mais forte com o tempo. |
| Separação entre infraestrutura e o que o operador instala | A separação é estrutural e válida independente do que for instalado — não depende de nenhum protocolo ou modelo específico. |
| Dado analítico acumulado como ativo crescente | Histórico de jornada cross-canal, padrões de escalação e eficácia de base de conhecimento crescem com o tempo. Trocar LLM ou trazer novos agentes não apaga esse ativo. Operadores que entram cedo acumulam vantagem estrutural sobre entrantes tardios. |
