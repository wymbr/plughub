# Revisão da página "Fronteira" do folder técnico — sob o binding A2A servidor

- **Status:** proposta para discussão (nada alterado no folder)
- **Data:** 2026-08-13
- **Alvo:** `docs/product/folder-tecnico-plughub.html`, seção 12 (`<!-- 12 · FRONTEIRA ABERTA -->`, linhas 940–994, página impressa 13)
- **Base:** [`docs/adr/adr-a2a-server-binding.md`](../adr/adr-a2a-server-binding.md) ·
  [`agentes-externos-reclassificacao.md`](agentes-externos-reclassificacao.md) ·
  [`docs/adr/adr-mcp-interception-single-border.md`](../adr/adr-mcp-interception-single-border.md)

---

## 1. O que a página diz hoje

Frase-mestra: *"a fronteira é aberta nas duas direções: **incorporar** agentes construídos fora e **derivar** atendimento, dado e lógica para fora"*. Quatro cartões: incorporar terceiros · derivar por webhook · conviver com a plataforma instalada · não aprisionar. O cartão de incorporação é o mais forte da página:

> "Um agente escrito em outro framework participa como **participante de primeira classe** da mesma sessão: entra pelo mesmo roteador, ocupa vaga do mesmo pool, produz segmento e é avaliado pelo mesmo formulário. […] as ferramentas chegam por sidecar de proxy local […] *Adotar um agente externo não abre buraco de conformidade.*"

## 2. Avaliação da ideia — vale mudar, e a razão principal não é o A2A

**A ideia está certa, e é mais urgente do que parece.** A reclassificação (exportar em vez de importar) é a justificativa de *produto*; mas a razão para mexer nesta página é anterior a ela: **o cartão de incorporação vende como realidade um caminho que o repositório mede como não-vigente**. Pela tabela do `CLAUDE.md` § MCP Interception, o sidecar "só existe se o operador subir o processo" e o interceptador in-process do agente nativo "nunca é instanciado". A única borda em vigor é a de *tool* (`invoke` no mcp-server) — que não é importar agente.

É o modo de falha que a Postura de Engenharia manda caçar, agora numa peça comercial: **uma afirmação plausível que ninguém confere** — até o prospect conferir. E o cartão termina com uma garantia de conformidade (*"não abre buraco"*), que é a pior frase possível para se apoiar num caminho opcional por construção.

**A favor da troca de eixo:**

- O argumento novo é **melhor de vender**, não só mais honesto: *"seu agente continua onde está"* remove um item de esforço do lado do cliente, em vez de adicionar um SDK e um processo sidecar ao deploy dele.
- Converte a governança de *promessa sobre código alheio* em **propriedade do que fica atrás do endpoint** — fila, capacidade, SLA, avaliação, auditoria. É defensável porque é medido aqui dentro.
- Há convergência independente: o quality-ingest já escolheu *contrato na borda* (ingerir transcrição) em vez de *runtime compartilhado* para resolver "avaliar agente externo".
- O descritor de pool (fase A0) se paga sozinho — é o mesmo artefato que o contrato delegate-por-pool e o "Adicionar Especialista" do Console já pedem.

**Contra — os riscos reais da peça:**

1. **Trocar uma promessa não-cumprida por outra.** O binding A2A está **proposto**; a fase A0 não começou. Escrever "exportamos agentes por protocolo aberto" em tempo presente repete o defeito com outro sujeito. Mitigação disponível na própria peça: o selo `roadmap` já existe (usado em biometria e WFM).
2. **Sequenciamento explícito.** A reclassificação §8 diz para *não anunciar a aposentadoria antes da fase A1*, sob pena de ficar uma janela sem nenhuma das duas histórias. **Um folder é exatamente um anúncio.** Esta é a decisão que precisa ser tomada antes do texto (ver §5).
3. **Perda comercial nomeada.** Some o discurso *"traga o seu agente para dentro"*. Se houver oportunidade em aberto que se apoie nisso, a peça deixa de responder. Vale confirmar antes de imprimir.
4. **Consistência interna.** A página 13 não é o único ponto que afirma o modelo importado — ver §4. Mudar só ela cria contradição dentro da mesma peça.

**Conclusão:** mudar, sim. Mas o ganho de honestidade vem principalmente de **remover** a promessa de incorporação, não de **acrescentar** a de A2A. As duas coisas podem ser separadas no tempo.

## 3. Novo enquadramento proposto

Uma frase substitui a atual:

> **A fronteira não se dissolve — ela se padroniza.**

Quatro portas, cada uma com um selo honesto:

| # | Porta | Estado |
|---|---|---|
| 1 | Sistemas e agentes de terceiro **acionam** os agentes da plataforma | webhook **em operação** · protocolo aberto entre agentes **roadmap** |
| 2 | Os agentes da plataforma **acionam** os sistemas do cliente, sempre mediados | **em operação** |
| 3 | Conviver com a plataforma instalada (histórico entra e sai) | **em operação** |
| 4 | Não aprisionar (portabilidade, modelos, dado) | **em operação** |

O que deixa de existir: o cartão "incorporar agentes de terceiros". O que entra no lugar dele, como argumento e não como omissão: **não pedimos que o seu agente venha para dentro; pedimos que ele fale um protocolo.**

### 3.1 O eixo de reaproveitamento — o argumento econômico

A porta 1 descreve um **mecanismo**. Falta o motivo pelo qual ele vale dinheiro, e é aqui que o binding deixa de ser assunto de arquitetura: **um agente construído nesta plataforma deixa de estar preso ao caso de uso que o justificou.** Quem financiou um agente de portabilidade para o WhatsApp financiou, sem saber, um serviço que o ERP, o portal do cliente, o app interno e o orquestrador que o time do cliente escreveu podem chamar — sem reescrever nada, sem um segundo contrato de integração, sem um projeto novo.

Isso não é retórica de folder: cai sobre invariantes que já existem.

- **A unidade endereçável já é o pool** — expor não exige inventar um endereço, exige declarar um contrato.
- **O descritor é o mesmo artefato** que o contrato delegate-por-pool e o "Adicionar Especialista" do Console já pedem. Declarar uma vez serve três consumidores.
- **O cartão é projeção do registro**, versionado pelo deploy — não existe catálogo paralelo para envelhecer.

**A frase que fecha o argumento é a que separa isto de uma API comum:** *reaproveitar sem regressão de governança*. Um agente exposto por framework é uma chamada de API — cada novo consumidor recomeça do zero em fila, capacidade, medição e auditoria. Um agente publicado aqui leva junto a fila, a capacidade medida por recurso, o SLA, a avaliação de qualidade com a versão de deploy carimbada e a trilha de auditoria. **O segundo consumidor herda o que o primeiro pagou.**

**Cuidado — "preservação de investimento" tem dois donos, e confundi-los é caro.** Em conversa de compra, a expressão costuma significar *"o que eu já tenho não é jogado fora"* — que aqui é a **porta 3** (o histórico da operação instalada entra e é avaliado antes de migrar tráfego). O eixo acima é o outro: *"o que eu construir aqui rende fora daqui"*. Os dois são verdadeiros e se reforçam, mas precisam aparecer separados; juntos numa frase só, o leitor ouve apenas o primeiro e o segundo se perde.

### 3.2 Vocabulário: tirar "webhook", nomear o protocolo

Correto — com um cuidado que inverte o sentido da edição se for feito de forma errada.

"Webhook" é substantivo de encanamento. Na peça, o que importa é o contrato, e o leitor não precisa saber por qual rota HTTP a chamada desce. O próprio ADR só diz "caminho webhook" porque é vocabulário **interno**, descrevendo o que o binding chama por dentro.

**O cuidado:** a menção ao webhook é a **única coisa em operação hoje** naquele cartão. Removê-la deixa o cartão inteiro em roadmap — e a premissa desta revisão era parar de imprimir o que o código não sustenta. Então: **tirar a palavra, não o fato.** "Acionado de fora por chamada HTTP, com o mesmo modelo de sessão" continua verdadeiro e continua presente; a face A2A padrão dessa mesma entrada é o passo seguinte, com selo próprio.

**Nomear "A2A" tem custo — e vale pagar.** Genérico ("protocolo aberto entre agentes") não convida à pergunta seguinte; nomear convida: *qual versão, quais métodos, tem streaming, tem push, tem gRPC?* Hoje a resposta é "nenhum, é roadmap". Mas quem reconhece a sigla é exatamente quem vai conferir, e para esse leitor **vago soa pior que datado**. Nomear com a fase declarada é a opção honesta e a mais forte. *(A colisão de nome do ADR §7 — "A2A" também designa delegação interna nos docs — não afeta a peça: o termo não aparece no folder hoje.)*

### 3.3 Delegar a uma pessoa por A2A — é fase 2, e é o argumento mais forte que existe aqui

A intuição está certa, e não é só minha opinião: o ADR §8 já registra que pool humano via A2A é *"provavelmente o produto mais diferenciado que existe aqui"* — e o coloca em **fase 2** por razões medidas, não por preguiça.

**Por que é forte.** Todo projeto agêntico bate no mesmo muro: o agente não sabe o que fazer e **não tem para quem passar**. Um framework expõe endpoint; ele não escala pessoas, não faz fila, não mede SLA, não avalia qualidade e não fatura atendimento. A proposta aqui é ser o **destino do handoff** — uma empresa expõe a própria força de trabalho humana como capacidade endereçável por agentes de terceiro, com espera medida, SLA, avaliação e auditoria.

**E o encaixe é arquitetônico, não retórico.** O pool já é a unidade endereçável; o contrato de ciclo de vida já é o mesmo para pessoa e IA; o `input-required` do A2A é um estado *interrompido*, não terminal — assento exato para o turno humano. A frase que fecha:

> **O chamador vê um agente. Se atrás dele há uma IA, uma pessoa, ou as duas em conferência, é decisão de roteamento — não do contrato.**

Isso é o princípio de *Opaque Execution* do A2A encontrando a tese que o folder já defende na página 5 (*"o papel de orquestrador é cambiável"*). O binding torna esse dial visível para quem chama de fora.

**O que separa a ideia da entrega — quatro obstáculos medidos:**

| # | Obstáculo | Consequência |
|---|---|---|
| 1 | **A2A é blocking por padrão** (spec §3.2.2: sem `returnImmediately`, o servidor *MUST* esperar até estado terminal ou interrompido) | Numa fila humana de minutos, o único caminho conforme é manter a conexão aberta. **A5 (streaming) deixa de ser fase opcional e vira pré-requisito** |
| 2 | **A graça de espera não substitui a fila** (D11) | 5 s contra fila humana de 4 min abandona em ~100% dos casos. Sem modo de presença declarado, um pool humano nunca serviria um caller A2A |
| 3 | **Um caller não é uma pessoa esperando** (§9) | Não desiste por tédio: reenvia em loop, de graça. `503` + `Retry-After` vira requisito, e a cota do cliente A2A precisa ser **menor** que a capacidade do pool |
| 4 | **Licença humana é cobrada por login, não por sessão** | O gate de admissão só vale para IA. Um parceiro consumindo fila humana consome capacidade cara sem portão próprio — a cota `a2a_tasks` deixa de ser refinamento |

Some ainda um `close_reason` novo para `tasks/cancel`, que não existe no domínio — e que **não pode** ser `customer_abandon`, ou a taxa de abandono do pool humano sobe por desistência programática e continua plausível.

**Recomendação para a peça:** entra, com selo, como **direção declarada** — e vale a linha mesmo sendo roadmap, porque é o único item da página que nenhum concorrente de framework consegue copiar. O que não pode é ser desenhado como capacidade. Separação honesta dentro do mesmo cartão: *hoje o alvo da chamada é um agente de IA da plataforma; o passo seguinte é a mesma chamada cair numa fila de pessoas.*

### 3.4 "Sem saber que a plataforma existe" — o que isso ganha, e os três asteriscos

A formulação é a do próprio ADR §1. Ela é forte, e tem consequências que a peça precisa antecipar antes que o leitor as levante.

**Asterisco 1 — quem não precisa saber é o chamador; o comprador precisa.** Se ninguém sabe que a plataforma existe, ela é, por construção, **substituível no ponto de uso**: trocar o endpoint no arquivo de configuração aponta o orquestrador para outro agente A2A amanhã. Isso não é defeito, é a mesma moeda do "sem lock-in" — mas exige dizer onde a diferença aparece. **Transparência no ponto de uso, evidência no ponto de compra:** o agente externo não precisa saber; quem assina o contrato precisa, e é para ele que existem Analytics, Qualidade, Bancada e Auditoria. Sem essa frase, o leitor arguto faz a objeção sozinho — *"então vocês são um endpoint intercambiável"*.

**Asterisco 2 — "sem integração proprietária" ≠ "sem integração".** O padrão elimina o trabalho de **transporte e descoberta**; não elimina o **acordo semântico**. O chamador ainda precisa saber o que pedir, e é isso que o descritor do pool declara. Formulação honesta: *o trabalho de integração deixa de ser descobrir como falar e passa a ser decidir o que pedir.*

**Asterisco 3 — a credencial é a exceção necessária.** O chamador não conhece a plataforma, mas carrega um principal emitido pelo tenant, com escopo de pools, cota e tenant derivado dela (D6/D7/D9). Não é contradição — é o que vale para qualquer serviço —, mas "não precisa saber nada" convida o arquiteto de segurança a apontar que alguém teve de provisionar um principal. Melhor: *não precisa conhecer a plataforma; precisa de uma credencial, como para qualquer serviço.*

### 3.5 O argumento comercial que ainda não está na página: entrar sem tocar em nada

A consequência mais vendável de "não sabe que a plataforma existe" não é técnica — é o **custo de experimentar**. Se o chamador fala padrão, adotar a plataforma **não exige reescrever o orquestrador, migrar um canal, nem substituir o contact center instalado**: acrescenta-se um endereço A2A à configuração de um agente que já roda. O piloto deixa de ser projeto e vira parâmetro.

Isso ataca exatamente a objeção que mata projeto de IA agêntica em empresa grande — *o esforço de integração não cabe no trimestre*. E encadeia com os outros dois eixos da página: **barato entrar** (padrão), **barato expandir** (reaproveitamento: o segundo consumidor herda o que o primeiro pagou), **barato sair** (portabilidade).

**Simetria a assumir de propósito:** barato entrar é barato sair. Não é contradição, é a mesma tese que a página já vende em "o custo de sair é uma decisão de projeto, não uma consequência" — e negá-la seria incoerência interna. Assumir reforça.

**Onde entra na peça:** é uma frase, não um cartão — provavelmente na abertura da página ou fechando a faixa. A página está apertada em A4 (§6.3, §7.6); acrescentar sem cortar não é opção. Candidato natural a ceder espaço: a última oração da faixa ("Protocolo aberto é preço de entrada…"), que diz por outro caminho quase a mesma coisa.

### 3.6 "Fase 2" — duas numerações colidindo, e uma correção na figura

A confusão é real e a origem é o ADR usar **dois vocabulários** de fase:

- **Arco A0–A6** (descritor → cartão read-only → principal externo → artefato → JSON-RPC → streaming → validação): é o **servidor A2A inteiro**. A0 não começou. Logo **o servidor é roadmap por completo**.
- **"Fase 2"** do §8: uma *onda de escopo posterior ao arco* — pools humanos e de contato. Ou seja, **roadmap mais um**.

Então a resposta é sim, com precisão: **o servidor é roadmap; a fila de pessoas é o passo seguinte a ele.**

**Correção aplicada na figura, e ela era mais séria que a nomenclatura.** A caixa teal aparecia sem marcador, com apenas a linha de humanos sinalizada — o que lê como *"o servidor existe; humanos vêm depois"*. É exatamente a classe de erro que esta revisão veio corrigir. Agora o **título da caixa carrega `ROADMAP`** e a linha de humanos fica tracejada e esmaecida, com `EM SEGUIDA`. Dois graus, uma escala só, sem números internos que a peça não explica.

### 3.7 Importar × exportar: o resultado macro converge, mas três coisas divergem

A observação é a mais forte contra a reclassificação até aqui, e merece resposta direta em vez de defesa da decisão anterior.

**Onde está certa.** Olhado do cliente final, o estado terminal é parecido: uma IA de terceiro e uma pessoa trabalham o mesmo caso. Se o recorte for *"o que o cliente experimenta"*, os dois modelos chegam perto.

**Onde não é a mesma coisa — e nenhum dos três é detalhe:**

| | Importar | Exportar |
|---|---|---|
| **Quem comanda** | a plataforma orquestra; o agente de terceiro é participante subordinado ao roteamento | o agente de terceiro orquestra; a plataforma é chamada por ele |
| **Quem garante o quê** | a plataforma responde por capacidade, encerramento, pausa e auditoria **sobre código que não controla** | cada lado responde pelo próprio; o SLA cobre o que a plataforma executa |
| **O que é medível** | evidência de execução completa do agente importado — **substrato estritamente mais rico** | só o segmento da plataforma; o raciocínio do agente externo é opaco (transcrição via ingest cobre o texto, não o traço) |

E há uma **diferença funcional concreta** que a formulação "IA+Humano rodando junto" encobre: no modelo importado, o agente de terceiro é **participante de uma conferência** — aparece como cartão no Console com passo e estado, recebe `@mention`, compartilha contexto, aceita intervenção de supervisor (Arc 11). No modelo exportado ele é **chamador**, não participante: fala por turnos através da borda e não é visível na sala. *Co-presença numa conferência ≠ requisição e resposta através de uma fronteira.* São topologias diferentes, e a superfície de orquestração do Console só existe na primeira.

**Assimetria que decide a ordem.** Os dois se sobrepõem em parte, mas **exportar cobre um caso que importar não cobre**: tornar os agentes da plataforma alcançáveis a partir da arquitetura que o cliente já tem. Importar o agente dele para cá não deixa o ERP dele chamar um agente daqui. Na direção oposta, importar cobre a evidência de execução, que exportar não dá.

**Conclusão prática — e ela usa o argumento a favor, não contra:** *se o resultado macro converge, escolhe-se pelo custo e pela garantia, não pela capacidade.* Exportar custa uma linha de configuração para o cliente e só promete o que a plataforma controla. Importar custa SDK, certificação, processo sidecar e uma promessa sobre código alheio. Convergência de resultado é precisamente o que autoriza escolher o caminho barato e garantível **primeiro**.

Isso não fecha a porta: a reclassificação já registra o runtime importado como **rebaixado, não deletado**, com reabertura mediante demanda comercial nomeada. O que você descreveu é o formato dessa demanda — *"meu orquestrador comanda e eu quero co-presença, não requisição"*. Se ela aparecer com contrato atrás, o item volta com dado.

### 3.8 "A conferência existe para atender ao A2A" — não, e a inversão vende mais

Tentador, e **não sustenta** por três motivos independentes. Vale registrar porque a formulação vai voltar.

**1 — A causalidade não fecha.** O modelo de conferência é anterior e independente: *"todo contato é uma sala"* é núcleo do modelo unificado de sessão, e sustenta especialista convocado, supervisor, avaliador, hooks de finalização e o Console do Arc 11 — tudo isso decidido e construído antes do ADR de A2A, que é de 2026-08-13 e diz explicitamente que **nada disso muda**. Um avaliador técnico que leia os dois documentos vê a data.

**2 — O A2A não pede isso.** No protocolo, uma `Task` tem **um** cliente e **um** agente remoto; o agrupamento de tarefas relacionadas é o `contextId`. Multiagente em A2A acontece por **composição** — um agente que é, ele próprio, cliente de outros —, não por co-presença numa sala compartilhada. E o chamador externo **não é participante**: ele fala por turnos através da borda. É a mesma distinção do §3.7: *co-presença numa conferência ≠ requisição e resposta através de uma fronteira.* Atribuir ao protocolo uma necessidade que ele não tem é dar ao leitor técnico uma pergunta fácil de fazer e impossível de responder.

**3 — Justifica o que existe pelo que não existe.** A conferência está **em operação**; o servidor A2A é **roadmap por inteiro**. Ancorar o ativo mais sólido da plataforma no item mais frágil da página enfraquece os dois. Regra geral que vale além deste caso: *não justificar capacidade construída por capacidade prometida.*

**A inversão, que é verdadeira e mais forte:**

> A conferência não existe por causa do A2A. **Ela é o que dá substância ao A2A.**

Uma tarefa entra pelo protocolo; atrás dela pode haver uma sala inteira — orquestrador de IA, especialista humano convocado no meio do caminho, supervisor acompanhando, avaliador pontuando depois. O chamador continua vendo **um agente e um contrato**. Isso é exatamente o princípio de *Opaque Execution* do protocolo, e é o que separa a plataforma de um endpoint de framework: **atrás do endpoint deles há um agente; atrás deste pode haver um time — e o contrato não muda.**

O motivo real da conferência continua sendo o do cliente, e o folder já o afirma nas páginas 4 e 5: a necessidade não cabe num agente só, e por isso o orquestrador convoca um especialista para dentro da sessão em vez de transferir. Esse argumento não precisa de muleta.

**Onde caberia na peça:** uma linha, na faixa de governança da figura — algo como *"uma tarefa pode ser atendida por vários participantes; o cartão continua sendo um"*. A faixa já está no limite de largura (§7.6), então entra **substituindo**, não somando. Candidato a ceder: `mascaramento sem opção`, que está dito em outro lugar e não é o argumento principal ali.

### 3.9 A conferência como justificativa da implementação — sim, e ela já é a tese da página 4

Aqui a resposta é outra: **é justificativa legítima, e é a melhor que a peça tem** — porque se apoia em algo construído e numa comparação que o comprador verifica pela própria experiência. Três precisões.

**1 — Já está argumentada, e no lugar certo.** A página 4 traz o cartão vermelho *"A interface de integração é o agente especialista, não a transferência"*, declarado como *"a diferença mais concreta em relação ao modelo herdado"*, e a abertura já diz que a sessão é *"uma sala com participantes simultâneos e visibilidade controlada, não uma fila de passagem"*. Repetir o argumento na Fronteira **dilui**; o que a Fronteira deve fazer é **cobrar o dividendo** dele (§3.8).

**2 — "Os tradicionais não funcionam assim" precisa de precisão, ou vira alvo fácil.** Conferência e consulta existem em telefonia há décadas; supervisor com escuta e intrusão também. Um avaliador vindo de um incumbente responde *"temos conferência desde sempre"* e o argumento cai. O que é diferente não é a existência de multiparte, são quatro coisas juntas:

- é o **modelo base de todo contato**, não caminho de exceção;
- vale em **qualquer canal**, não só na voz;
- tem **visibilidade por participante** (mensagem para todos, só para agentes, ou para participantes nomeados);
- produz **um segmento medível e avaliável por participante** — e admite participante de IA em simetria com o humano.

**3 — A forma mais forte não é "é diferente", é "a alternativa cobra pedágio".** Se um contato só comporta um ocupante, toda competência adicional vira transferência — e transferência **perde contexto, reinicia a espera e parte a medição em dois**. Isso o comprador confere nos próprios números: taxa de transferência, recontato, TMA inflado. Argumento verificável bate argumento arquitetural.

**E o fecho, que responde exatamente à sua pergunta.** A justificativa para ter implementado assim não é o A2A nem a diferenciação em si — é que **"IA e humano trabalhando juntos" é impossível sobre um modelo de sessão de ocupante único**. Numa plataforma que roteia para um só, a IA só pode ser *etapa antes* (bot de triagem) ou *painel ao lado* (copilot) — nunca **participante da mesma sala**. É por isso que acoplar IA a um incumbente não produz a mesma coisa, e é a razão pela qual isto teve de ser construído do substrato para cima.

Repare que essa é a mesma função macro que você identificou em §3.7 como resultado comum aos dois modelos de integração: a conferência é **a condição de possibilidade** dela. Fecha o arco.

**Efeito colateral bem-vindo:** o argumento de reaproveitamento é sobre investimento **futuro**, o que o torna coerente com o selo `roadmap`. Prometer que *"o que você construir aqui será chamável por protocolo aberto"* é uma afirmação de direção — categoria correta. Era a promessa de incorporação, em tempo presente, que estava na categoria errada.

**E responde à perda nomeada.** O cliente que queria *"hospede o meu agente"* recebe um não. Com este eixo ele recebe, no lugar, a metade que resolve a dor real (não ter dois mundos desconectados): o orquestrador dele continua dele e chama os nossos agentes; e o que ele construir aqui é chamável de lá.

## 4. Dependências — o que muda junto (senão a peça se contradiz)

| Onde | Linha | O que afirma hoje | Ação |
|---|---|---|---|
| p.12 · Camada 6 | 918–922 | Tabela de interceptação com dois mecanismos: interceptador in-process (nativo) e sidecar (externo) | **Passivo próprio, maior que o da p.13.** Nenhum dos dois está em vigor. Reescrever falando da **borda** e da política declarada na ferramenta, sem enumerar mecanismos. Depende do ADR de borda única (proposto, fase M0 = medir) |
| p.15 · SVG rodapé | 1209–1211 | "Fronteira aberta — agente externo entra pelo sidecar de proxy com a mesma governança" | Trocar por "Fronteira padronizada — protocolo aberto para chamar os agentes da plataforma · histórico de outra plataforma reidratado pelo leitor plugável" |
| p.16 · Módulos por camada | 1228 | "SDK com interceptador e sidecar de proxy" | Manter o SDK, mas separar **portabilidade** (fica, sustenta o "sem lock-in") de **runtime importado** (rebaixado) — mesma separação pendente em `docs/pacotes/sdk.md` |
| p.8 · Sete camadas | 557–658 | (não menciona) | **Não mexer — decisão ativa, ver §4.1** |

### 4.1 Não existe camada 8 — e a figura da p.8 deve continuar provando isso

O ADR é explícito: A2A é **binding de borda**, na mesma classe de WhatsApp e webchat; routing-engine, modelo de admissão, `ChannelSchema`, skill-flow-engine, bridge e modelo de sessão **não mudam**. Uma "Camada 8 · Protocolo" na figura das sete camadas seria a peça contradizendo o próprio ADR — e repetiria o erro que o ADR §D4 evita citando `WorkflowInstance` e `Journey`: *contêiner largo para fato que cabe num estreito*.

O valor visual está no oposto. A figura da p.8 tem dois eixos — camadas à esquerda, **ordem do contato** à direita (① canal → ② identidade → ③ roteador → ④ agente → ⑤ fluxo → ⑥ registro). O chamador externo entra em ① e percorre **exatamente a mesma trilha**. É isso que torna a promessa "o que está atrás do endpoint" verificável na própria figura, sem uma linha de texto: *a fila, o roteador, a medição e o registro não são opcionais para quem chega por protocolo.*

Se, na fase A1, valer marcar isso, a edição mínima e honesta é uma segunda origem na faixa "TRILHA DE UM CONTATO" (`cliente` **e** `sistema externo` chegando à mesma caixa ①), não uma camada nova. A faixa está apertada em largura — provável que exija encolher as caixas. **Recomendação: não fazer agora.**

Também **não redesenhar o SVG da p.15** além do texto da faixa de rodapé (já previsto em §4). A figura é ajustada à mão e o ganho de redesenhar não paga o risco.

### 4.2 Onde ela fica: em frente ao gateway de canais, no caminho webhook

Correto, e é o que o ADR descreve: o binding não publica `/v1` — ele expõe `/a2a` e **chama o caminho webhook por dentro**. Duas precisões, porque "acima" tem dois sentidos e só um vale:

**"Acima" no sentido de caminho de chamada: sim.** A borda A2A recebe, traduz e delega ao endereço webhook que já existe. É um degrau **para fora**, não para cima.

**"Acima" no sentido de camada de abstração da p.8: não.** Naquela figura a leitura é top-down por abstração — subir significaria ficar entre *Fluxos* (2) e *Canais* (3), e o binding não é mais abstrato que um fluxo. É a **face externa da camada 3**, mesma classe de WhatsApp e webchat. Continua valendo: não há camada 8, e também não há camada 2,5.

**E não é um canal.** `channel` é filtro duro de roteamento; A2A é fato de *credencial*. Desenhá-la dentro da caixa "canais", ao lado de voz/chat/WA, afirmaria o contrário de D1. A representação honesta é uma caixa **irmã**, alimentando a mesma entrada, com rótulo de representação externa — não de canal novo.

> ⚠️ **A figura tem de mostrar a autenticação entre as duas, senão desenha um defeito.** O endereço webhook por pool é **anônimo por construção** e interno. Se o traço sugerir "A2A é o webhook publicado", o desenho vira a materialização exata do que D6/D7 existem para impedir — disparador anônimo de pools que promovem deploy e contatam clientes, com `tenant_id` vindo do corpo. Entre a borda A2A e o caminho webhook há **principal externo, escopo de pools e tenant derivado da credencial**. É por isso que o portão, na figura da §6.3, não é enfeite.

**Restrição mecânica da p.15:** a primeira coluna do SVG está cheia — `canais` (y 26–78) e `agenda` (y 92–138) já empilhadas, e a faixa vermelha começa em y 156. Não há linha livre para a caixa irmã sem reflow da coluna. Ou seja: o lugar conceitualmente certo é esse, mas **a versão desenhada cabe na figura nova da p.13** (§6.3), onde o portão já está explícito. Na p.15, fica no texto da faixa.

## 5. Decisão que precede o texto

**(a) Publicar já, com selo `roadmap` no protocolo aberto.** A página fica honesta imediatamente e o peso recai nas três portas em operação. Risco: vender direção declarada numa peça técnica.

**(b) Segurar a página até a fase A1** (AgentCard read-only), como manda a reclassificação §8. Risco: a promessa falsa de incorporação continua impressa nesse meio-tempo.

**(c) Duas etapas — recomendada.** Agora: remover o cartão de incorporação e reescrever a página com as três portas em operação, sem citar A2A. Na A1: acrescentar a porta 1 com o protocolo nomeado. Assim nenhuma versão da peça afirma o que o código não sustenta, e a janela do §8 não existe porque nada foi anunciado como aposentado — apenas deixou de ser vendido.

O conteúdo abaixo está escrito para **(a)**; a variante **(c)** é o mesmo texto sem o segundo parágrafo do cartão 1 e sem o selo `roadmap`.

---

## 6. Conteúdo proposto — página "Fronteira"

### 6.1 Texto (para leitura e crítica)

**Kicker:** Fronteira
**Título:** A fronteira se padroniza — ela não se dissolve
*(alternativo: "Integrar sem hospedar: contrato na borda, governança do lado de cá")*

**Abertura.** Uma plataforma que se propõe a orquestrar agentes não pode presumir que todos os agentes sejam dela. A resposta comum é hospedar o agente do outro — trazer código de terceiro para dentro e prometer, sobre ele, as mesmas garantias de capacidade, disponibilidade, encerramento e auditoria. Nós fazemos o inverso. **O seu agente continua onde está: no seu framework, sob o seu deploy, no seu ciclo de release.** O que atravessa a fronteira é contrato — protocolo declarado, permissão verificada, chamada registrada. E atravessa nos dois sentidos: o que a operação já tem entra sem ser descartado, o que for construído aqui sai sem ser reescrito.

**Cartão 1 — Seus sistemas acionam nossos agentes** · *webhook em operação · protocolo aberto roadmap*
Um processo da plataforma é acionado de fora com o mesmo modelo de sessão de qualquer contato: cria sessão, roteia, suspende aguardando um sinal externo e retoma por token — inclusive dias depois. O passo seguinte é publicar esse mesmo endereço como **agente em protocolo aberto**: cada pool anuncia um cartão de capacidades — o que faz, o que aceita, o que devolve e em que versão —, recebe mensagem, abre uma tarefa e entrega o resultado. O chamador não precisa saber que existe uma plataforma do outro lado. **O agente escrito para atender no WhatsApp passa a ser chamável pelo ERP, pelo portal e pelo orquestrador que o seu time escreveu — sem reescrita e sem um segundo projeto de integração.** *A versão publicada é a versão do deploy: promover uma versão muda o contrato anunciado, sem catálogo paralelo para manter.*

**Cartão 2 — Nossos agentes acionam os seus sistemas** · *em operação*
Nenhum agente — humano ou de IA — alcança sistema de negócio diretamente. Toda integração passa por servidores de ferramenta autorizados, e a política de auditoria é declarada **na ferramenta**, não na chamada: o chamador não pode optar por sair. O efeito colateral é de arquitetura, não de esforço: a mesma capacidade fica disponível para o agente de IA, para o fluxo automatizado e para o Console, com uma única trilha.

**Cartão 3 — O que a operação já tem não é descartado** · *em operação*
O histórico de atendimento de outro contact center entra no pipeline de qualidade por um leitor plugável: um fluxo de eventos canônicos, com mascaramento aplicado na entrada, que reidrata sessão, segmento e transcrição como se tivessem nascido aqui. Serve para **medir a operação instalada antes de migrar qualquer tráfego** — o investimento já feito vira linha de base, não perda. O caminho inverso também existe: o histórico interno pode ser reexportado e reavaliado com um formulário novo, sem contaminar a medição de produção — todo dado carrega a procedência.

**Cartão 4 — Não aprisionar** · *em operação*
A lógica de atendimento é declarativa e portável, não código proprietário: extraível, versionada e verificável por linha de comando quanto a contrato de execução e isolamento de dependências. Os modelos são trocáveis por configuração, operadoras e provedores de canal são adaptadores, e o dado é exportável. **O custo de sair é uma decisão de projeto, não uma consequência.**

**Faixa — O que se preserva quando a fronteira é padronizada.**
Investimento em plataforma de atendimento costuma ficar preso ao caso de uso que o justificou. Aqui ele tem três saídas, e são três coisas distintas: o que se constrói é **extraível** — lógica declarativa, versionada, verificável por linha de comando; é **alcançável** — cada pool publicado como agente em protocolo aberto, chamável por qualquer sistema da empresa; e é **reaproveitável sem regressão de governança** — cada novo consumidor herda a mesma fila, a mesma capacidade medida por recurso, o mesmo SLA, a mesma avaliação de qualidade com versão de deploy carimbada e a mesma trilha de auditoria, sem reconstruir nada. **O segundo consumidor herda o que o primeiro pagou.** É a diferença entre publicar um agente e publicar uma chamada de API — e é também por que padronizar a fronteira ganha de importar agentes de terceiro: importar pede que a plataforma garanta capacidade, encerramento e auditoria sobre código que ela não controla, corroendo exatamente a camada que dá valor ao reaproveitamento. Protocolo aberto é preço de entrada; o que não vira commodity está atrás do endpoint.

### 6.2 HTML pronto para colar (substitui as linhas 940–994)

```html
<!-- ================= 12 · FRONTEIRA PADRONIZADA ================= -->
<section class="page">
  <div class="kicker">Fronteira</div>
  <h2>A fronteira se padroniza — ela não se dissolve</h2>
  <p style="max-width:172mm">
    Uma plataforma que se propõe a orquestrar agentes não pode presumir que todos os agentes sejam dela. A resposta
    comum é hospedar o agente do outro — trazer código de terceiro para dentro e prometer, sobre ele, as mesmas
    garantias de capacidade, disponibilidade, encerramento e auditoria. Nós fazemos o inverso: <strong>o seu agente
    continua onde está</strong>, no seu framework, sob o seu deploy. O que atravessa a fronteira é contrato —
    protocolo declarado, permissão verificada, chamada registrada. E atravessa nos dois sentidos: o que a operação
    já tem entra sem ser descartado, o que for construído aqui sai sem ser reescrito.
  </p>

  <div class="grid2" style="margin-top:3mm">
    <div class="card dark">
      <h4>Seus sistemas acionam nossos agentes <span class="seal pale">webhook em operação</span></h4>
      <p>Um processo da plataforma é acionado de fora com o mesmo modelo de sessão de qualquer contato: cria sessão,
      roteia, suspende aguardando sinal externo e retoma por token — inclusive dias depois. O passo seguinte é
      publicar esse mesmo endereço como <strong>agente em protocolo aberto</strong>: cada pool anuncia um cartão de
      capacidades — o que faz, o que aceita, o que devolve, em que versão —, recebe mensagem, abre uma tarefa e
      entrega o resultado. O agente escrito para atender no WhatsApp passa a ser chamável pelo ERP, pelo portal e
      pelo orquestrador que o seu time escreveu — <strong>sem reescrita e sem um segundo projeto de
      integração</strong>. <em>A versão publicada é a versão do deploy; não há catálogo paralelo para manter.</em>
      <span class="seal road" style="margin-left:0">protocolo aberto · roadmap</span></p>
    </div>
    <div class="card">
      <h4>Nossos agentes acionam seus sistemas <span class="seal">em operação</span></h4>
      <p>Nenhum agente — humano ou de IA — alcança sistema de negócio diretamente. Toda integração passa por
      servidores de ferramenta autorizados, e a política de auditoria é declarada <strong>na ferramenta</strong>, não
      na chamada: o chamador não pode optar por sair. O efeito é de arquitetura, não de esforço — a mesma capacidade
      fica disponível para o agente de IA, para o fluxo automatizado e para o Console, com uma única trilha.</p>
    </div>
    <div class="card teal">
      <h4>O que a operação já tem não é descartado <span class="seal pale">em operação</span></h4>
      <p>O histórico de atendimento de outro contact center entra no pipeline de qualidade por um leitor plugável:
      um fluxo de eventos canônicos, com mascaramento aplicado na entrada, que reidrata sessão, segmento e
      transcrição como se tivessem nascido aqui. Serve para <strong>medir a operação instalada antes de migrar
      qualquer tráfego</strong> — o investimento já feito vira linha de base, não perda. O caminho inverso também
      existe: o histórico interno pode ser reexportado e reavaliado com um formulário novo, sem contaminar a
      medição de produção — todo dado carrega a procedência.</p>
    </div>
    <div class="card">
      <h4>Não aprisionar <span class="seal">em operação</span></h4>
      <p>A lógica de atendimento é declarativa e portável, não código proprietário: extraível, versionada e
      verificável por linha de comando quanto a contrato de execução e a isolamento de dependências. Os modelos são
      trocáveis por configuração, as operadoras e provedores de canal são adaptadores, e o dado é exportável.
      <strong>O custo de sair é uma decisão de projeto, não uma consequência.</strong></p>
    </div>
  </div>

  <div class="band" style="margin-top:4mm">
    <div class="kicker pale" style="margin-bottom:2mm">O que se preserva quando a fronteira é padronizada</div>
    <p>Investimento em plataforma de atendimento costuma ficar preso ao caso de uso que o justificou. Aqui ele tem
    três saídas, e são três coisas distintas: o que se constrói é <strong>extraível</strong> — lógica declarativa,
    versionada, verificável por linha de comando; é <strong>alcançável</strong> — cada pool publicado como agente em
    protocolo aberto, chamável por qualquer sistema da empresa; e é <strong>reaproveitável sem regressão de
    governança</strong> — cada novo consumidor herda a mesma fila, a mesma capacidade medida por recurso, o mesmo
    SLA, a mesma avaliação de qualidade com versão de deploy carimbada e a mesma trilha de auditoria, sem
    reconstruir nada. <strong>O segundo consumidor herda o que o primeiro pagou.</strong> É a diferença entre
    publicar um agente e publicar uma chamada de API — e é por isso que padronizar a fronteira ganha de importar
    agentes de terceiro: importar pede que a plataforma garanta capacidade, encerramento e auditoria sobre código
    que ela não controla, corroendo exatamente a camada que dá valor ao reaproveitamento. Protocolo aberto é preço
    de entrada; o que não vira commodity está atrás do endpoint.</p>
  </div>
  <div class="foot"><span>Arquitetura · fronteira padronizada</span><span>13</span></div>
</section>
```

> **Nota de layout:** o cartão 1 ficou com dois selos (um no título, um no fim do parágrafo). Se poluir na prova
> impressa, a alternativa é dividir em dois cartões — "acionado por webhook" (em operação) e "publicado como agente
> em protocolo aberto" (roadmap) —, o que consome a linha do cartão 4 e exige mover "Não aprisionar" para a faixa.

### 6.3 Figura para a própria página "Fronteira" (opcional, mas é onde ela rende)

Das páginas de arquitetura (8 a 16), a Fronteira é **a única sem figura** — e é a única cujo argumento é, em si, uma **topologia**: quem chama quem, e o que atravessa a borda em cada sentido. Uma figura aqui trabalha mais do que qualquer edição nas figuras existentes, e mata visualmente a metáfora antiga: a borda aparece como **linha contínua com um portão**, não como abertura.

Encoding proposto: quatro travessias, todas passando pelo mesmo portão (contrato declarado · credencial · escopo · mascaramento · auditoria); a única em roadmap recebe chip próprio no rótulo, as demais são presente.

**Custo de espaço:** ~59 mm de altura. A página hoje tem kicker + título + abertura + grade 2×2 + faixa; a faixa também cresceu (§7.6). É provável que caiba em A4, mas **precisa de prova impressa** — se estourar, o corte natural é fundir os cartões 2 e 4 (ambos "em operação", ambos sobre mediação e portabilidade) ou deixar a figura substituir a grade, mantendo só a faixa.

```html
  <svg viewBox="0 0 760 250" xmlns="http://www.w3.org/2000/svg" style="margin-top:4mm">
    <defs>
      <marker id="f1" markerWidth="10" markerHeight="10" refX="8" refY="3.2" orient="auto">
        <path d="M0,0 L8,3.2 L0,6.4 z" fill="#990011"/>
      </marker>
    </defs>

    <!-- portão: tudo que atravessa passa por aqui -->
    <rect x="352" y="18" width="56" height="222" rx="6" fill="#F2F2F2" stroke="#990011" stroke-width="1.6"/>
    <text transform="rotate(-90 380 129)" x="380" y="133" text-anchor="middle" font-family="Calibri,Arial"
          font-size="9" font-weight="700" fill="#990011" letter-spacing="0.8">CONTRATO · CREDENCIAL · ESCOPO · MASCARAMENTO · AUDITORIA</text>

    <!-- fora -->
    <rect x="6" y="18" width="168" height="222" rx="6" fill="#2E3138"/>
    <text x="90" y="42" text-anchor="middle" font-family="Calibri,Arial" font-size="11.5" font-weight="700" fill="#fff">FORA</text>
    <text x="22" y="66" font-family="Calibri,Arial" font-size="9.5" fill="#C9CDD4">o orquestrador do seu time</text>
    <text x="22" y="82" font-family="Calibri,Arial" font-size="9.5" fill="#C9CDD4">agentes em outros frameworks</text>
    <text x="22" y="98" font-family="Calibri,Arial" font-size="9.5" fill="#C9CDD4">ERP, portal, app interno</text>
    <text x="22" y="114" font-family="Calibri,Arial" font-size="9.5" fill="#C9CDD4">sistemas de negócio</text>
    <text x="22" y="130" font-family="Calibri,Arial" font-size="9.5" fill="#C9CDD4">o contact center instalado</text>
    <text x="22" y="162" font-family="Calibri,Arial" font-size="9.5" font-weight="700" fill="#F3D6DA">continua onde está</text>
    <text x="22" y="178" font-family="Calibri,Arial" font-size="9" fill="#9AA0AA">seu framework · seu deploy</text>

    <!-- dentro -->
    <rect x="586" y="18" width="168" height="222" rx="6" fill="#0E4E4E"/>
    <text x="670" y="42" text-anchor="middle" font-family="Calibri,Arial" font-size="11.5" font-weight="700" fill="#fff">DENTRO</text>
    <text x="602" y="66" font-family="Calibri,Arial" font-size="9.5" font-weight="700" fill="#fff">os agentes da plataforma</text>
    <text x="602" y="82" font-family="Calibri,Arial" font-size="9" fill="#CDE3E3">endereçados por pool</text>
    <text x="602" y="108" font-family="Calibri,Arial" font-size="9" fill="#CDE3E3">e o que vem junto com eles:</text>
    <text x="602" y="126" font-family="Calibri,Arial" font-size="9.5" fill="#fff">fila e espera medida</text>
    <text x="602" y="142" font-family="Calibri,Arial" font-size="9.5" fill="#fff">capacidade por recurso</text>
    <text x="602" y="158" font-family="Calibri,Arial" font-size="9.5" fill="#fff">SLA</text>
    <text x="602" y="174" font-family="Calibri,Arial" font-size="9.5" fill="#fff">avaliação com versão de deploy</text>
    <text x="602" y="190" font-family="Calibri,Arial" font-size="9.5" fill="#fff">trilha de auditoria por chamada</text>
    <text x="602" y="216" font-family="Calibri,Arial" font-size="9" font-weight="700" fill="#7FCFCF">o segundo consumidor herda</text>
    <text x="602" y="230" font-family="Calibri,Arial" font-size="9" font-weight="700" fill="#7FCFCF">o que o primeiro pagou</text>

    <!-- travessias -->
    <text x="380" y="62" text-anchor="middle" font-family="Calibri,Arial" font-size="9.5" font-weight="700" fill="#2E3138">seus sistemas chamam nossos agentes</text>
    <text x="380" y="74" text-anchor="middle" font-family="Calibri,Arial" font-size="8.5" fill="#6B7280">webhook hoje · protocolo aberto de agentes na mesma entrada — roadmap</text>
    <line x1="176" y1="84" x2="584" y2="84" stroke="#990011" stroke-width="2" marker-end="url(#f1)"/>

    <text x="380" y="112" text-anchor="middle" font-family="Calibri,Arial" font-size="9.5" font-weight="700" fill="#2E3138">nossos agentes chamam seus sistemas</text>
    <text x="380" y="124" text-anchor="middle" font-family="Calibri,Arial" font-size="8.5" fill="#6B7280">sempre por ferramenta mediada, com auditoria não-optável</text>
    <line x1="584" y1="134" x2="176" y2="134" stroke="#990011" stroke-width="2" marker-end="url(#f1)"/>

    <text x="380" y="162" text-anchor="middle" font-family="Calibri,Arial" font-size="9.5" font-weight="700" fill="#2E3138">o histórico da operação instalada entra</text>
    <text x="380" y="174" text-anchor="middle" font-family="Calibri,Arial" font-size="8.5" fill="#6B7280">reidratado e avaliado antes de migrar tráfego</text>
    <line x1="176" y1="184" x2="584" y2="184" stroke="#990011" stroke-width="2" marker-end="url(#f1)"/>

    <text x="380" y="212" text-anchor="middle" font-family="Calibri,Arial" font-size="9.5" font-weight="700" fill="#2E3138">a lógica e o dado saem</text>
    <text x="380" y="224" text-anchor="middle" font-family="Calibri,Arial" font-size="8.5" fill="#6B7280">declarativos, versionados, extraíveis por linha de comando</text>
    <line x1="584" y1="234" x2="176" y2="234" stroke="#990011" stroke-width="2" marker-end="url(#f1)"/>
  </svg>
```

> **Nota de honestidade na figura:** as quatro setas estão desenhadas iguais, e só o rótulo da primeira diz "roadmap". Se a prova impressa deixar isso discreto demais, a alternativa é tracejar a seta 1 — ao custo de enfraquecer visualmente justamente a travessia que carrega o posicionamento novo. É uma escolha, não um detalhe de desenho.

## 7. Pontos abertos para a discussão

1. **Qual caminho de §5** — (a) publicar já com selo, (b) segurar até A1, (c) duas etapas.
2. **Nomear o protocolo?** O texto acima diz "protocolo aberto entre agentes" sem citar A2A. Nomear ajuda o leitor técnico e ancora em padrão de indústria (v1.0, Linux Foundation); não nomear evita comprometer com um contrato antes da fase A2. *(Colisão de nome não afeta esta peça — "A2A" não aparece no folder; o conflito é interno, nos docs, onde "A2A" também designa delegação entre agentes da plataforma.)*
3. **Existe oportunidade em aberto apoiada em "traga o seu agente"?** Se sim, a peça precisa de uma resposta explícita para ela antes de a página mudar.
4. **A página 12 entra neste mesmo lote?** É o passivo maior, e reescrevê-la depende do ADR de borda única — hoje proposto, com a primeira fase sendo medir.
5. **Qual metade de "preservação de investimento" lidera a página** — a defensiva (o que a operação já tem não é descartado, cartão 3) ou a de amortização (o que se constrói aqui rende fora, cartão 1 + faixa)? O texto acima carrega as duas separadas, com a segunda na faixa. Se a peça tiver de escolher uma, a decisão é de posicionamento comercial, não de arquitetura.
6. **A faixa cresceu.** Passou de ~5 para ~9 linhas ao absorver o eixo de reaproveitamento. Se estourar a prova impressa, o corte natural é o último período ("Protocolo aberto é preço de entrada…") ou mover o argumento *importar × padronizar* para nota de rodapé da página 12.
7. **Exposição é opt-in — vale dizer isso na peça?** O reaproveitamento pode ser lido como "abre tudo". Não é: publicação é por pool, com contrato declarado, chamador credenciado, escopo de pools por credencial, cota própria e mascaramento sem opção. Uma linha sobre isso ("reaproveitar não é abrir") desarma a objeção de segurança antes de ela ser feita — ao custo de espaço numa página já cheia.
