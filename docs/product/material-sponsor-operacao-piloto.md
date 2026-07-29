# Operação-piloto PlugHub — proposta ao sponsor

> **Público:** diretor de operação de empresa usuária **ou** executivo de BPO.
> **Propósito:** conduzir a primeira conversa e sustentar a decisão interna dele.
> **O que este material NÃO faz:** não menciona rodada de investimento, valuation nem estratégia de mercado. Isso
> é assunto do material do investidor, que vem depois e usa este engajamento como evidência.
> Data: 2026-07-29 · Base: [`operacao-piloto-e-rodada-2.md`](operacao-piloto-e-rodada-2.md)

**Como usar este documento.** A espinha é comum. Dois blocos são variantes por perfil e estão marcados —
**[EMPRESA]** e **[BPO]**. Leve só o bloco do seu interlocutor; os dois na mesma sala confundem.

---

## 1. A proposta, em um parágrafo

Queremos operar com você uma **fatia pequena e completa** da sua operação sobre uma plataforma nova — poucos
agentes, um processo de negócio real, todos os canais de texto que você já usa — durante um período acordado, com
métricas de sucesso definidas antes de começar e cláusula de saída explícita. Você recebe condições de fundador e
prioridade de roadmap; nós recebemos a primeira operação real e o direito de usar o resultado como referência.

Pequena para o risco caber. Completa para a prova valer.

---

## 2. O que você já convive e ninguém resolveu

Quatro coisas que o setor normalizou e trata como "é assim mesmo". Não são funcionalidades faltando — são
consequências de como as plataformas atuais foram construídas.

**Quando o cliente precisa passar um dado sensível, o que acontece hoje?** A resposta costuma ser transferir para
a URA — e perder parte dos clientes no caminho —, pausar a gravação e abrir um buraco na auditoria, ou
simplesmente deixar o agente ouvir o número, colocando a operação inteira no escopo PCI. Aqui o agente **delega** a
captura, acompanha o progresso em tempo real e **não vê o dado**; pode retomar o controle a qualquer momento e, no
fim, recebe só o resultado. O cliente não é transferido e não percebe nada.

**O seu AHT inclui o wrap-up?** Se inclui, você não sabe quanto tempo o cliente é de fato atendido, e o agente
fica bloqueado depois que o cliente já foi embora. Aqui o contato fecha no instante em que o cliente sai — o AHT
passa a ser verdade — e a disposição vira item de trabalho na fila do próprio agente, preenchida quando ele puder.

**Que percentual das interações a monitoria cobre?** Normalmente 2 a 5%, com avaliação subjetiva e contestação
resolvida em reunião, sem trilha. Aqui a cobertura é ampla, parte dos critérios é calculada de forma determinística
e entra na nota, e a contestação é estruturada por dimensão, com histórico imutável.

**Quando um cliente contata quatro vezes pelo mesmo problema, isso vira quantos registros?** Quatro. E ninguém
consegue responder quanto custou resolver aquele problema do começo ao fim — só quanto custou cada contato. Aqui
o processo é a unidade: atravessa contatos, canais e dias, com SLA por etapa e o roteador ciente de que aquele
cliente já esteve aqui.

**E como você faz hoje para dar continuidade a um atendimento?** Prometeu retorno em três dias, precisa do
segundo passo de uma negociação, precisa confirmar um documento — e a única forma de expressar isso na sua
plataforma é **colocar a pessoa numa lista e disparar uma campanha em lote**. Não porque o lote seja a forma
natural da necessidade, mas porque é a única primitiva que existe. Sua operação acaba remodelada em ciclos de
disparo e resposta que ninguém pediu. Aqui a continuidade é **um passo do próprio processo**: ele espera o tempo
necessário, respeita horário útil e retoma o contato pelo canal certo, sem lista, sem lote e sem virar outra
operação.

**Quantas vezes o mesmo cliente foi abordado por você esta semana, somando todos os canais e todas as
campanhas?** Provavelmente ninguém sabe responder — e não é falta de zelo. Seu discador de voz tem uma lista, o
disparo de SMS costuma ser outro sistema, o WhatsApp é um terceiro, e cada campanha pertence a uma área
diferente. Cada um sabe se ligou para aquele registro; nenhum sabe quanta pressão aquela **pessoa** recebeu da
sua empresa. Bloqueio total todos têm; **fadiga não tem praticamente ninguém**, porque exigiria um registro
único de contato no nível do cliente, e os canais são sistemas separados.

Aqui o registro é um só, e a regra é definida por você em camadas — teto por período, quarentena após tentativa
sem sucesso, limite por canal. Vale para **qualquer campanha em qualquer canal**, e cada decisão fica gravada com
o motivo. Além de proteger a relação com o cliente, é a evidência que você quer ter em mãos quando a frequência
de abordagem for questionada — por um órgão de defesa do consumidor, por um cliente seu, ou pela sua própria
ouvidoria.

---

### 2a. [EMPRESA] Por que isso importa para você

Seu custo por contato já foi otimizado até onde dá. O desperdício que sobrou não está **dentro** da interação —
está **entre** as etapas: handoffs, retrabalho, espera, dado que se perde de um sistema para outro. É exatamente
o que nenhuma ferramenta atual consegue medir, porque todas medem interação.

O ganho que propomos não é "atender mais rápido". É **enxergar e reduzir o processo inteiro** — e, no caminho,
recuperar o AHT como métrica confiável e ampliar a cobertura de qualidade sem contratar mais monitores.

### 2b. [BPO] Por que isso importa para você

Sua margem é comprimida de dois lados: o cliente pressiona preço a cada renovação e exige auditoria de qualidade
que custa gente. Três coisas mudam com esta plataforma:

**Qualidade vira argumento comercial, não custo.** Cobertura ampla com evidência e contestação estruturada é algo
que você **apresenta na concorrência** — e que quase nenhum concorrente seu consegue oferecer. Deixa de disputar
só por preço.

**A automação entra por etapa, sob seu controle.** O humano começa no comando do processo, a IA assume pedaços, e
a cada pedaço que a avaliação prova confiável o humano recua — medido, auditado e reversível. Você decide a
velocidade.

**A objeção honesta, que você vai levantar:** *"se eu automatizo, eu canibalizo meus próprios postos faturados."*
É verdade em contrato por posto ou por hora. Dois contrapontos, e o segundo é o que pesa: os contratos estão
migrando para resultado e transação, e nesse modelo a automação vira margem direta; e, mais importante, **quem não
automatizar vai perder a concorrência para quem automatizou** — a pressão não vem de nós, vem do seu cliente. A
pergunta deixa de ser *se* e passa a ser *com qual parceiro e em que velocidade*.

E há um ganho que só existe no seu caso: o que você aprender aqui é replicável na sua base de clientes.

---

## 3. O que é a operação-piloto

**Pequena em superfície, completa em profundidade.** Não é uma prova de conceito de laboratório: é uma operação
real, com agentes reais, clientes reais e volume real — só que delimitada.

### 3.1 O que entra

- **Atendimento com humanos e IA na mesma conversa** — não é bot na frente com transferência quando falha. São
  participantes da mesma sessão, com o operador podendo convocar especialistas de IA durante o atendimento.
- **Um processo de negócio real**, escolhido junto com você, que atravesse mais de um contato.
- **Canais de texto** — tipicamente webchat e WhatsApp.
- **Plataforma de qualidade completa** — avaliação, contestação, calibração.
- **Console do operador**, com fila, histórico do cliente e caixa de trabalho.
- **Painéis** com detalhamento de processo → contato → atendimento.
- **Mascaramento de dado sensível e trilha de auditoria** desde o primeiro dia.

### 3.2 O que fica de fora, por escolha

**Voz.** O canal está **implementado e integrado** — tronco PSTN via Twilio, com transcrição e síntese de voz de
provedores externos e substituíveis. Fica fora do piloto **de propósito**: é o caminho mais caro por minuto e o
que menos rodou em operação até aqui, então começar por texto deixa a prova mais rápida, mais barata e com menos
risco para você. Se sua operação exigir voz no piloto, é conversa de escopo e prazo — não impedimento técnico.

**Campanha ativa (outbound).** Também está implementada, ponta a ponta: audiência, campanha, controle de fadiga
de contato, importação de base e o disparo por qualquer canal, inclusive voz. **Uma campanha outbound roda.** O
que ainda não existe é a camada de **otimização de discagem** — o pacing preditivo que estima taxa de atendimento
para manter o agente ocupado. Em volume moderado isso não faz falta; em alto volume, é onde as plataformas
maduras entregam mais eficiência que nós hoje. É diferença de otimização, não de capacidade.

Se campanha ativa for parte do que você quer provar, ela cabe no piloto — e vale conversarmos, porque é
justamente um dos pontos que precisamos exercitar em operação real.

**Gestão de força de trabalho (WFM).** Não substituímos sua ferramenta; integramos com ela.

**Sua operação inteira.** O piloto é uma fatia. O resto continua exatamente como está.

---

## 4. As métricas — e o combinado sobre elas

A primeira atividade do engajamento é **medir a linha de base da sua operação atual**. Sem baseline, qualquer
resultado depois é indefensável — inclusive para você defender internamente.

| O que medimos | Por que importa |
|---|---|
| **AHT verdadeiro** (contato fechado na saída do cliente) | Comparado ao AHT atual, que inclui o wrap-up |
| **Cobertura de monitoria** | Comparada aos 2–5% da amostragem manual |
| **Tempo de resolução do processo**, ponta a ponta | Métrica que hoje não existe — e é justamente o ponto |
| **Contatos por processo resolvido** | O tamanho real do retrabalho, hoje invisível |
| **Etapas com automação** e a evolução | O avanço da automação, sob controle e medido |
| **Exposição a dado sensível** | Redução de escopo de conformidade |

**Critério de sucesso acordado antes de começar.** Definimos juntos quais dessas métricas precisam melhorar e
quanto, para o piloto ser considerado bem-sucedido. Sem gol móvel — nem para cima, nem para baixo.

---

## 5. O que você compromete

Sendo direto, porque proposta vaga não fecha:

- **Uma operação delimitada**, com agentes reais e volume real. Volume baixo demais não produz dado de qualidade
  suficiente para provar nada — dimensionamos isso junto.
- **Acesso a sistemas e dados**, em ambiente controlado, pelos protocolos de integração.
- **Um patrocinador interno com autoridade** para destravar TI, segurança e jurídico. É o item que mais atrasa
  este tipo de projeto.
- **Tempo do seu time** em desenho de processo e treinamento.
- **Compromisso comercial.** O piloto é pago, mesmo com condição de fundador. Piloto gratuito não tem dono
  interno e morre na primeira prioridade concorrente — é do seu interesse tanto quanto do nosso.

---

## 6. O que você recebe

**Condição de fundador** no preço, válida na expansão. **Prioridade de roadmap** — sua necessidade entra na frente
da fila. **Acesso direto a quem constrói**, sem camada de suporte de nível 1. E influência real sobre um produto
que você vai operar por anos.

Nosso modelo comercial é **licença por capacidade simultânea** — agentes logados ao mesmo tempo, humanos e IA na
mesma unidade. É o modelo de licença concorrente que você já conhece, estendido para incluir IA. Você sabe no
primeiro mês o que vai pagar no décimo terceiro. Não há cobrança por ação, por token, por resolução ou por módulo.

> **[BPO]** Para você, isso tem um efeito adicional: quando a IA assume parte do trabalho, ela ocupa uma licença
> em vez de um posto — e a economia aparece na sua margem, não na fatura do fornecedor.

---

## 7. O que você precisa saber antes de decidir

Preferimos que isso venha de nós agora do que apareça na diligência depois.

**A plataforma não está em produção em outro cliente.** Você seria o primeiro. É por isso que as condições são de
fundador e o escopo é pequeno.

**Não temos certificações emitidas ainda** (SOC 2, ISO 27001). A arquitetura já produz a evidência técnica que
elas exigem — trilha de auditoria por chamada, mascaramento por perfil, registro imutável de acesso — e a
certificação está em andamento. Se o seu processo de compras exige o certificado hoje, precisamos falar sobre
isso antes de qualquer outra coisa.

**Somos uma equipe pequena.** Cinco pessoas, todas com mais de vinte anos em soluções e serviços de contact
center. Dois engenheiros construíram a plataforma que você vai ver. Isso é eficiência de capital e é também uma
limitação de banda — que o escopo pequeno respeita, e que o crescimento da equipe endereça.

**Não somos telefonia.** Somos a camada de orquestração e qualidade sobre provedores externos substituíveis.
Isso significa menos risco de infraestrutura e nenhum aprisionamento de operadora — e também que não competimos
em maturidade de telefonia pura com quem faz isso há vinte anos.

---

## 8. O risco, e como ele é contido

| Sua preocupação | Como é contida |
|---|---|
| "E se atrapalhar meu atendimento?" | Fatia delimitada; o resto da operação segue intacta. Rollback é desligar o roteamento daquela fila |
| "E se não funcionar?" | Cláusula de saída explícita em contrato, com prazo e condições definidos antes de começar |
| "E se vocês quebrarem?" | Sua lógica de atendimento é artefato declarativo portável, seus dados são exportáveis, e a integração é por protocolo aberto. Podemos discutir custódia de código |
| "E meu time não adotar?" | O operador ganha capacidade, não perde controle: ele convoca especialistas, delega e retoma. E o piloto começa com quem quer participar |
| "E os dados sensíveis?" | Mascaramento por perfil desde o primeiro dia, com trilha imutável de quem viu o quê. É mais controle do que você tem hoje, não menos |

---

## 9. Como começa

| Etapa | O que acontece | Duração aproximada |
|---|---|---|
| **1. Escopo** | Escolha do processo e da fila, dimensionamento de volume, definição das métricas e do critério de sucesso | Semanas |
| **2. Baseline** | Medição da operação atual nas métricas acordadas | Semanas |
| **3. Desenho e integração** | Modelagem do processo, conexão aos seus sistemas, configuração de qualidade e mascaramento | A definir no escopo |
| **4. Operação assistida** | Vai ao ar com acompanhamento próximo; ajustes em ciclo curto | A definir no escopo |
| **5. Avaliação** | Resultado contra baseline e decisão conjunta de expandir, ajustar ou encerrar | — |

A etapa 1 não custa nada e não exige compromisso: é uma sessão de trabalho com seu time de operação. Sai dela um
escopo com números — ou a conclusão honesta de que não é o momento.

---

## 10. Quem somos

Cinco pessoas, todas com **mais de vinte anos em soluções e serviços de contact center**. Não é um time de
tecnologia que descobriu o contact center: é gente que operou, implantou e sofreu com essas plataformas, e que
construiu esta a partir das dores que conhece de primeira mão.

Essa é a resposta honesta a uma pergunta legítima — *como vocês sabem que construíram a coisa certa sem ter
clientes?* Conhecimento de domínio substituiu parte do que normalmente vem do mercado. O piloto existe justamente
para validar o que falta.

---

## Anexo — para o time técnico do sponsor

Estes documentos respondem à diligência técnica e podem ser compartilhados quando a conversa avançar:

- **Descritivo técnico-funcional** — arquitetura, 29 serviços, invariantes e uma seção completa de limitações
  declaradas: [`plughub-descritivo-tecnico-funcional.md`](plughub-descritivo-tecnico-funcional.md)
- **Figuras** — topologia de serviços, anatomia da sessão, ciclo de vida do contato, fluxo de eventos e o modelo
  de processo multi-contato
- **Racional de arquitetura** — por que as decisões estruturais foram tomadas:
  [`por-que-plughub-existe.md`](por-que-plughub-existe.md)

> A §24 do descritivo lista as limitações conhecidas sem maquiagem. Recomendamos que o time técnico dele comece
> por ali — é o que separa uma conversa de engenharia de uma conversa de vendas.
