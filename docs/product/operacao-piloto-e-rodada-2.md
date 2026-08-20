# PlugHub — Operação-piloto e dimensionamento da rodada 2

> Estado: base de trabalho · Data: 2026-07-29 · Uso: interno
> **Este documento é a base da qual derivam dois materiais distintos:** o do **sponsor** (primeiro) e o do
> **investidor** (depois, evidenciado pelo primeiro). Não é nenhum dos dois.
> Fontes: [`por-que-plughub-existe.md`](por-que-plughub-existe.md),
> [`plughub-descritivo-tecnico-funcional.md`](plughub-descritivo-tecnico-funcional.md),
> [`competitive-analysis-2026-07.md`](competitive-analysis-2026-07.md)

---

## 0. A sequência, e por que ela importa

A ordem escolhida — **sponsor → investidor → mercado** — não é detalhe de execução. É o que resolve o problema
central da tese: a plataforma tem capacidade comprovada em arquitetura e **nenhuma prova em campo**.

Um sponsor assinado converte isso. O investidor deixa de ser perguntado "financio esta hipótese?" e passa a ser
perguntado "financio esta expansão?". São dois preços e dois níveis de diluição diferentes.

| Etapa | Audiência | Pergunta que ela faz | O que a convence |
|---|---|---|---|
| **1. Sponsor** | Diretor de operação / BPO | "O que eu ganho e o que eu arrisco?" | Operação pequena e completa, com métrica de sucesso acordada e saída garantida |
| **2. Investidor** | Fundo / anjo | "Isto vira negócio?" | Sponsor em produção + números do piloto + modelo de custo bottom-up |
| **3. Mercado** | Contas grandes | "Por que vocês e não o incumbente?" | Referência nomeada + certificações + prova de escala |

**Consequência prática:** o material do sponsor não é uma versão reduzida do material do investidor. São
documentos com propósitos opostos — um vende **redução de risco operacional**, o outro vende **retorno sobre
risco**. Escrever um só para os dois é o erro clássico e produz um material que não convence nenhum.

---

## 1. A operação-piloto — fatia vertical, não horizontal

### 1.1 O princípio

**Todas as camadas, nem todos os canais.** Uma fatia vertical fina que atravessa a plataforma inteira prova que o
sistema funciona como sistema. Uma fatia horizontal — muitos canais, poucas camadas — prova só que existem
conectores, que é justamente o que qualquer concorrente também tem.

O critério de corte: **entra tudo que é difícil de provar depois; fica de fora tudo que é só volume de trabalho.**

### 1.2 O que entra — e por quê

| Camada | Por que é inegociável no piloto |
|---|---|
| **Co-presença humano + IA** | É a tese. Sem ela, o piloto vira "mais um bot na frente do atendimento" |
| **Ciclo de vida em 3 camadas + wrap-up destacado** | Produz o número que vende o próximo negócio: AHT verdadeiro |
| **Especialista convocável** (`@mention` + `task`) | Prova o mesmo artefato servindo robô e humano — a economia de certificar uma vez |
| **Qualidade: avaliação, contestação, calibração** | O diferencial mais forte, e o que **exige dados reais** para ser demonstrável (ver §1.4) |
| **Processo multi-contato (journey)** | A aposta de categoria. Sem processo real atravessando contatos, não se prova |
| **Guard de MCP + masking por role** | O que abre a porta do CISO na próxima conta. Barato de manter ligado, caro de retrofitar |
| **Console + inbox pull** | É onde o operador vive; sem ele não há operação, só demo |
| **Analytics com drill de 3 níveis** | É o que o sponsor mostra internamente para justificar a continuidade |

### 1.3 O que fica de fora — e por quê

| Item | Motivo |
|---|---|
| **Multi-tenant completo** | Um sponsor = um tenant. Hardening entra na rodada, não no piloto |
| **Otimização de discagem (pacing preditivo)** | ⚠️ *Corrigido 2026-08-19:* a campanha outbound **funciona pelos canais de texto**; ~~inclusive por voz~~ **não por voz** (depende do adapter em reconstrução). O pacing preditivo não é o que falta primeiro — falta o plano de mídia. Segue valendo: não confundir com "outbound não existe" |
| **WFM** | Não é foco; integra via MCP quando houver |
| **Maioria dos canais** | Um síncrono + um assíncrono bastam para provar o modelo |
| **Escala** | O piloto prova **valor**; a rodada prova **escala**. Não confundir os dois entregáveis |

> 🔴 **A decisão abaixo foi tomada sobre uma premissa FALSA — medido em 2026-08-19. Não usar o roteiro de
> discurso dela.**
>
> A decisão de 2026-07-29 se apoia inteiramente em *"voz existe"*. Não existe. `VoiceAdapter.handle_inbound`
> chama cinco métodos que não existem em `packages/channel-gateway` (`adapters/voice.py:236,247,433,558,565`),
> mockados em `tests/test_voice_adapter.py:116-121` — verde na suíte, `AttributeError` no runtime, e nenhuma
> sessão de voz jamais existiu no ambiente. O canal `webrtc` também não tem plano de mídia.
>
> **Consequência direta na sala:** a frase *"temos voz e escolhemos não começar por ela"* **não pode ser dita**.
> A posição honesta hoje é *"voz está em reconstrução e por isso não entra no piloto"* — que é, de fato, a mesma
> recomendação de escopo, com fundamento diferente e sem risco de descoberta pelo sponsor. E *"se o sponsor
> insistir em voz, é negociação de escopo e de preço — não impedimento técnico"* está **invertido**: é
> impedimento técnico, com arco de construção próprio
> ([`../adr/adr-voice-media-plane.md`](../adr/adr-voice-media-plane.md), fases V-F0 a V-F5).
>
> **Outbound:** a campanha ativa roda de verdade — mas **pelos canais de texto**. O disparo por voz depende do
> mesmo adapter, então *"funciona por qualquer canal, inclusive voz"* é falso. O substrato (audiência, fadiga,
> importação, máquina de estado por destinatário) está implementado e é entregável real.
>
> **A decisão de escopo do piloto permanece a mesma; o fundamento e o discurso mudam.** Retomar com o time antes
> da próxima conversa comercial. Texto original preservado abaixo como registro.

> **Decisão tomada — canais do piloto (2026-07-29).** Voz **existe e fica fora do escopo do piloto**. São duas
> afirmações diferentes e as duas importam:
>
> - **Existe.** O canal de voz é entregável da rodada 1 — tronco PSTN via Twilio, STT e TTS de provedores
>   externos, atrás das três interfaces (`IVoiceProvider`, `ISTTProvider`, `ITTSProvider`) com encadeamento de
>   fallback. Não estamos construindo stack de telefonia: somos camada fina sobre provedores substituíveis, e o
>   Twilio é tronco, não cérebro. Isso elimina risco de infraestrutura de telefonia e evita lock-in de provedor.
> - **Fica fora do piloto, por escolha.** Voz é o caminho mais caro (minuto de STT/TTS, PSTN) e o **menos
>   exercitado** da stack — não o menos completo. Começar por texto — um síncrono e um assíncrono, tipicamente
>   webchat + WhatsApp — deixa o piloto mais barato, mais rápido e com menos risco operacional para o sponsor.
> - **Outbound é caso à parte e não deve ser omitido.** A campanha ativa está implementada ponta a ponta e
>   **funciona por qualquer canal, inclusive voz**. Falta só a otimização de discagem (pacing preditivo). Se o
>   sponsor tiver operação ativa relevante — típico em BPO —, campanha entra no escopo do piloto e vira um dos
>   itens de validação mais valiosos, porque é justamente código pronto e pouco exercitado.
>
> A diferença de discurso é relevante na sala: *"temos voz e escolhemos não começar por ela"* é uma posição
> completamente diferente de *"não temos voz"*. Se o sponsor insistir em voz, é negociação de escopo e de preço —
> não impedimento técnico.

### 1.4 O piso de volume — o ponto mais fácil de errar

A plataforma de qualidade é o diferencial que mais depende de **dados**, e não de funcionalidade. Um piloto
pequeno demais prova a arquitetura e **não prova o valor**:

- Amostragem por cota exige um mínimo de contatos por agente para a cobertura significar alguma coisa.
- A lente por deploy usa `min_sample=30` por versão — abaixo disso o próprio sistema sinaliza baixa significância.
- A calibração avaliador-IA × humano precisa de massa de divergências para a curva ter sentido.

**Regra prática:** o piloto precisa de agentes e volume suficientes para produzir dezenas de avaliações por
agente por mês, e de pelo menos um processo que **realmente** atravesse múltiplos contatos. Uma operação de 5
agentes com volume baixo não entrega isso.

Dimensionar isso com o sponsor é parte da negociação de escopo — e é um argumento a favor de escolher a operação
**pequena dentro de uma empresa grande**, e não uma empresa pequena.

### 1.5 As métricas que o piloto tem que produzir

Definir **antes** de começar, porque são elas que vendem a expansão e a próxima conta:

| Métrica | O que prova | Comparação |
|---|---|---|
| **AHT verdadeiro** (contato fecha na saída do cliente) | O wrap-up destacado | Contra o AHT reportado hoje, que inclui ACW |
| **Cobertura de monitoria** | A plataforma de qualidade | Contra os 2–5% da amostragem manual atual |
| **Tempo de resolução do processo** (ponta a ponta) | A tese de categoria | Não existe hoje — é métrica nova, e isso é o argumento |
| **Contatos por processo resolvido** | O custo real do retrabalho | Hoje aparece como N contatos independentes |
| **% de etapas com automação** e sua evolução | O "dial do anteparo" girando | Começa baixo por design; a curva é o produto |
| **Tempo de captura de dado sensível** e escopo PCI | A delegação sem ver o dado | Contra transferência para URA ou pausa de gravação |

> Toda métrica precisa de **linha de base medida antes do piloto**. Sem baseline, o resultado é indefensável — e
> capturar baseline é a primeira atividade do engajamento, não um detalhe.

---

## 2. A oferta ao sponsor

### 2.1 O que ele ganha

Uma operação real modernizada com risco contido, e influência sobre um produto que ele vai usar por anos:
prioridade de roadmap, preço preferencial em condições de fundador, e a chance de resolver dores que o fornecedor
atual dele nunca resolveu — captura de dado sensível sem transferência, AHT que reflete a realidade, monitoria
com cobertura ampla em vez de amostra, e processo medido de ponta a ponta.

Há também um ganho não-óbvio e real: **ele ajuda a desenhar a categoria**. Para um diretor de operação com
ambição, ser a referência nomeada de uma plataforma nova é ativo de carreira.

### 2.2 O que ele compromete

Ser explícito aqui é o que torna a proposta séria:

- Uma operação delimitada, com agentes reais e volume real.
- Acesso a dados e a sistemas via MCP, em ambiente controlado.
- Um patrocinador interno com autoridade para destravar TI, segurança e jurídico.
- Tempo do time de operação em desenho de processo e treinamento.
- Compromisso comercial — mesmo com desconto de fundador, **o piloto deve ser pago**. Piloto grátis não tem dono
  interno e morre na primeira prioridade concorrente.

### 2.3 O que não está na mesa

| Item | Posição |
|---|---|
| Preço preferencial e condição de fundador | **Sim** — é o que ele compra ao correr o risco |
| Prioridade de roadmap | **Sim**, com limite: prioridade sobre a fila, não direito de veto |
| Exclusividade na vertical | **Perigosa** — fecha o mercado que a rodada 2 precisa abrir. Se for inevitável, prazo curto e escopo estreito |
| Propriedade intelectual do produto | **Nunca**. Customização é dele; a plataforma é nossa |
| Cláusula de saída | **Sim, e explícita** — é o que reduz o risco percebido e destrava a assinatura |

### 2.4 A regra anti-esteira

A armadilha do modelo é o piloto virar sistema sob medida. Regra a acordar por escrito:

> **O que serve a mais de um cliente entra no produto e é nosso investimento. O que serve só a este cliente é
> customização e é faturado como serviço.** A dúvida se resolve na direção do produto, não do projeto.

Métrica interna a acompanhar desde o dia 1: **fração do engajamento que vira ativo reutilizável** — template de
skill-flow, rubrica de avaliação, conector MCP, playbook de processo. Se essa fração não sobe a cada trimestre, o
modelo degradou para consultoria e o múltiplo do negócio vai junto.

---

## 3. Dimensionamento da rodada — modelo bottom-up

> A rodada não é um número escolhido; é o **resultado** da meta. A cadeia abaixo liga a meta de faturamento à
> necessidade de gente, e a gente ao valor. Os parâmetros marcados como `[preencher]` dependem da sua base de
> custo e do valor-hora praticado.

### 3.1 Da meta ao time

**Meta:** US$ 2M brutos no primeiro ano de operação, proporção **50% serviço / 50% licença**.

**Trilha de licença — US$ 1M/ano**

```
US$ 1.000.000 / 12 meses           = US$ 83.300 / mês
US$ 83.300 / (preço por agente simultâneo/mês)
   a US$ 100  →  ~830 agentes simultâneos
   a US$ 150  →  ~555 agentes simultâneos
```

> **Leitura honesta:** 555 a 830 agentes simultâneos no primeiro ano de operação não saem de um piloto. Saem do
> sponsor **em expansão** mais duas ou três contas, ou de um BPO que sozinho tem esse porte. Isso reforça a
> escolha de um sponsor grande — e traz junto o **risco de concentração**, que o investidor vai levantar.

**Trilha de serviço — US$ 1M/ano**

```
Capacidade por consultor = horas faturáveis/ano × valor-hora
   ~1.400h a US$ 120/h  →  ~US$ 168.000 / consultor
   ~1.400h a US$ 180/h  →  ~US$ 252.000 / consultor

US$ 1.000.000 / capacidade por consultor
   a US$ 168k  →  ~6 consultores
   a US$ 252k  →  ~4 consultores
```

> Utilização plena não existe: ramp-up, pré-venda e trabalho não faturável consomem parte. Dimensionar com folga.
> **A receita de serviço é linear em gente** — é ela que dita o tamanho do time e, por consequência, o da rodada.

### 3.2 O time-alvo

| Função | Hoje | Alvo | Por quê |
|---|---|---|---|
| Engenharia | 2 | **4–5** | Hardening de produção, certificações, suporte ao piloto e roadmap em paralelo não cabem em 2. Resolve também o *bus factor*, primeiro achado de qualquer due diligence |
| Serviço / delivery | 2 | **4–6** | Determinado pela trilha de serviço acima |
| Comercial | 1 | **2** + pré-venda | Um comercial não constrói pipeline enterprise com ciclo de 9–18 meses |
| **Total** | **5** | **11–13** | |

### 3.3 As frentes de custo

| Frente | Conteúdo | Driver |
|---|---|---|
| **Engenharia** | Hardening de produção (topologia real, multi-broker, isolamento multi-tenant, teste de caos com número publicável), fechamento dos gaps da §24 do descritivo | Headcount × meses |
| **Certificações** | SOC 2 Tipo II, adequação LGPD, ISO 27001 se o ICP exigir. Auditoria + ferramental + tempo de engenharia | Custo de auditoria `[preencher]` + 6–12 meses de calendário |
| **Modelo de serviço** | Formatação da oferta, metodologia, templates, rubricas, playbooks — o IP que torna o segundo engajamento mais barato que o primeiro | Headcount + tempo dedicado a produtização |
| **Formação de recursos** | Treinamento e certificação de consultores próprios e, depois, de parceiros | Custo por pessoa `[preencher]` |
| **Comercial** | Contratação, pré-venda, marketing técnico, canal/parceria | Headcount + CAC estimado |
| **Infra e operação** | Ambiente de produção, LLM, telefonia do piloto, observabilidade | Variável com volume |
| **Reserva** | Colchão para o ciclo enterprise escorregar | % do total |

**Horizonte a dimensionar:** 18 a 24 meses. Menos que isso não cobre o ciclo de venda enterprise somado ao tempo
de certificação, e a rodada vira ponte para outra rodada em posição fraca.

### 3.4 O número que falta

```
Rodada ≈ (time-alvo × custo médio carregado × meses de horizonte)
       + certificações
       + formação e produtização
       + comercial e canal
       + infra
       − receita realizada no período (piloto pago + primeiras contas)
       + reserva
```

Com sua base de custo, esta conta fecha rápido. O ponto metodológico que vale preservar no material do investidor:
**o valor é derivado da meta, não escolhido** — e isso é visível na apresentação. É um sinal de rigor que a maior
parte dos decks não tem.

---

## 4. Marcos — o que cada etapa destrava

| # | Marco | Destrava | Evidência que produz |
|---|---|---|---|
| **M0** | Sponsor assinado, escopo e baseline acordados | Toda a sequência | Carta de intenção ou contrato de piloto |
| **M1** | Operação-piloto em produção | A prova de valor | AHT verdadeiro, cobertura de monitoria, tempo de processo |
| **M2** | Certificações iniciadas (SOC 2 Tipo I → II) | O ICP de operações grandes | Relatório de auditoria |
| **M3** | Prova de escala | A objeção técnica principal | Número publicável de agentes simultâneos sustentados |
| **M4** | Expansão dentro do sponsor + 2ª conta | Sai do risco de cliente único | Contrato de expansão e segundo logo |
| **M5** | Modelo de serviço produtizado | A margem e o múltiplo | Fração de IP reutilizável por engajamento, subindo |

> **M0 e M1 são pré-rodada ou co-rodada** — é o que muda o preço. Quanto mais do M1 estiver de pé quando o
> investidor entrar, menor a diluição.

---

## 5. Riscos do plano

| Risco | Por que é real | Mitigação |
|---|---|---|
| **Dependência de um sponsor** | Se ele sai, não há plano B e a rodada trava | Duas conversas em paralelo, mesmo com maturidades diferentes |
| **Scope creep do sponsor** | Vira sistema sob medida e o produto não avança | A regra do §2.4, acordada por escrito antes de começar |
| **Volume insuficiente no piloto** | Prova arquitetura, não prova valor — e o material da rodada fica sem números | Dimensionar volume no escopo (§1.4); recusar piloto pequeno demais |
| **Serviço vira esteira** | Margem cai, escala trava em gente, múltiplo despenca | Métrica de IP reutilizável desde o dia 1 |
| **Concentração de receita** | Poucas contas grandes = risco que o investidor precifica | Segunda conta no M4 como marco explícito |
| **Certificação atrasa a venda** | 6–12 meses de calendário que não comprimem | Começar no mês 1 da rodada; usar o piloto como ambiente de evidência |
| **Bus factor de 2 engenheiros** | Primeiro achado de due diligence técnica | Contratação no início da rodada, não no meio |
| **Voz no escopo do piloto** | ⚠️ *Corrigido 2026-08-19 (medido):* ~~é a parte menos madura e a mais cara~~ — não é imaturidade, é **não-entrega**: o canal de voz está em reconstrução (o `VoiceAdapter` levanta `AttributeError` em runtime e o WebRTC não tem plano de mídia), com arco de construção próprio | Negociar canais no escopo; preferir começar por texto |

---

## 6. O que ainda precisa ser decidido

1. ~~**Canais do piloto**~~ — **decidido** (§1.3): ~~voz existe,~~ fica fora do piloto; começa por texto. ⚠️ *Corrigido 2026-08-19: a decisão vale, o fundamento não — **voz não existe**, está em reconstrução (§1.3).*
2. ~~**Perfil do sponsor**~~ — **decidido**: o material atende **os dois perfis**, empresa grande usuária e BPO.
   Não se sabe quem adere primeiro, e o ideal é ter mais de uma conversa viva (é também a mitigação do risco de
   sponsor único, §5). O material do sponsor tem espinha comum e dois blocos variantes — a dor e a contrapartida
   mudam por perfil.
3. **Vertical** — define a linguagem das métricas e os primeiros templates de processo. A base já tem material de
   telco (portabilidade, retenção) como ponto de partida.
4. **Base de custo e valor-hora** — os `[preencher]` da §3, que fecham o número da rodada.
5. **Termos com o sponsor** — desconto, prioridade de roadmap, exclusividade, cláusula de saída (§2.3).

---

## Apêndice — de onde derivam os dois materiais

**Material do sponsor** (primeiro): §1 escopo, §1.5 métricas, §2 oferta e contrapartidas, §4 marcos M0–M1.
Tom: redução de risco operacional. Não menciona rodada, valuation nem estratégia de mercado.

**Material do investidor** (depois): §0 sequência, §3 modelo bottom-up, §4 marcos completos, §5 riscos, mais o
argumento de existência (`por-que-plughub-existe.md`) e o estado técnico (`plughub-descritivo-tecnico-funcional.md`
§24 para a honestidade de estágio). Tom: retorno sobre risco, com o sponsor como evidência.
