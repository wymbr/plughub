# Decagon × PlugHub — o que adotar e o que mudar

> **Revisado em 2026-09-25:** a autoria por linguagem natural (§ 1 da primeira versão) foi
> **descartada** pelo dono e substituída pelo editor tipado com execução observável e narrativa
> derivada; entraram política como dado, cenários de aceite, biblioteca de blocos e mineração de
> processo (§ 9). O raciocínio da troca está no próprio § 1.
>
> Estado: direção de produto aceita pelo dono em 2026-09-24 · Fichas em `pending.md`, sob este
> documento e sob os grupos citados em cada seção.
> Origem: [`competitive-analysis-2026-09.md`](competitive-analysis-2026-09.md) § 5.2 (a Decagon abriu
> escritório em São Paulo em 15/09/2026).

## Método

Sobre a Decagon: blog e páginas oficiais, mais terceiros — quase todos **concorrentes** dela, então as
críticas pesam menos. **A sintaxe da AOP não é pública** (a documentação exige login). Sobre o PlugHub:
medido no código e no ledger em 2026-09-24.

---

## 1. Autoria: o oposto da AOP

**Decagon.** A AOP é texto que "compila" para lógica executável. A interface com o backend **não** é
resolvida em linguagem natural: integração, autenticação e schema de ferramenta são dos engenheiros,
versionados em git; os passos críticos (reembolso, identidade) rodam em código. A crítica mais citada é
depurar: com um artefato só, não se sabe se o erro está no texto, na lógica compilada ou na integração.

**PlugHub hoje.** Skill Flow YAML (42 skills), editor Monaco com validação ao vivo
(`POST /v1/skills/validate`). O editor gráfico prometido quando o arco n8n foi abortado **não começou**.

**Primeira decisão (2026-09-24), DESCARTADA em 2026-09-25:** texto em linguagem natural convertido por
LLM em YAML. Motivo do descarte, nas palavras do dono: quem é leigo e precisa usar as expressões
certas para o texto gerar a alteração certa **está escrevendo numa linguagem de programação sem
gramática publicada**; isso tende a retrabalho, e é o que as críticas à Decagon descrevem (depuração
difícil, engenheiro dedicado). Somaram-se os riscos próprios do conversor: o mesmo texto gerando YAML
diferente, e texto e YAML divergindo com o tempo.

**Decisão vigente — o contrato formal fica, e a linguagem natural vai só para a LEITURA.**

- **Editor tipado.** Os tipos de step já têm schema em `@plughub/schemas`; dele sai o template de cada
  step, o autocompletar e a validação enquanto se digita. O editor também confere **referências**:
  step de destino inexistente, tag `@ctx.` fora do catálogo do ContextStore, `form_id` e tool
  inexistentes — uma lista única de referências resolvíveis, lida pelo editor e pelo portão de deploy.
- **Descritivo padrão por step, complementável.** Cada step nasce com uma frase FIXA derivada dos seus
  CAMPOS (não só do tipo), em vocabulário de negócio — o que se edita é um fluxo negocial N3. Ex.:
  *"Coleta dados pelo formulário Plano de saúde"* (fixo) + *"do titular e dependentes"* (adicionado).
  Num `invoke`, a parte fixa é a descrição da tool escrita no construtor de tools. Nunca "invoke",
  "choice" ou "escalate" na frase.
- **Visão narrativa.** A sequência dos descritivos (fixo + complemento) dos steps e das tools, sem LLM:
  a parte fixa nunca mente, porque sai da estrutura. O complemento é carimbado com o hash do step; se o
  step mudou depois, a visão marca *"descritivo possivelmente desatualizado"*. Um LLM, no máximo,
  SUGERE o complemento, com aprovação humana — único lugar em que resta IA na autoria.
- **Construtor de tools**, ao estilo Postman: declara a chamada REST, os parâmetros, o schema de
  entrada e de saída e a descrição. Riscos desenhados desde o início: credencial em cofre referenciado
  (nunca na definição), allowlist de hosts por tenant (senão a plataforma vira proxy), saída mapeada
  para schema declarado e **mascarada**, e as tools servidas por um servidor MCP genérico cuja
  definição é config de um store único, com guarda de injeção e auditoria.
- **Tools do editor conforme o ABAC, e `tools[]` preenchido sozinho.** São duas autorizações: o ABAC
  de quem escreve decide o que ele pode inserir; o `tools[]` declarado no skill e assinado no token
  (CAP-06) decide o que o fluxo chama ao rodar. Ao inserir um `invoke`, o editor declara a tool no
  `tools[]` — hoje 0 de 44 skills o fazem, porque ninguém escreve isso à mão.
- **Execução observável.** Passo a passo com breakpoints e as leituras e escritas do ContextStore por
  step (não só o estado final); tools em modo simulado; `@masked.*` nunca exibido. Simulação de contato
  de qualquer canal pelo bloco `render` real, contra o snapshot do `next`, com a sessão marcada como de
  teste (`adr-quality-substrate-isolation`). Replay de atendimento do histórico **até divergir** (§ 3).

**Para quem.** O fluxo é de quem constrói (implantação, `devops`); o **conteúdo** (textos, opções,
formulários) e a **política** (§ 9) são da operação. Posicionamento oposto ao da Decagon: o negócio
edita política e escreve cenários com regras claras; o técnico constrói com ferramenta tipada; todos
leem a mesma narrativa derivada da estrutura.

As instruções dos steps `reason` continuam precisando de casa versionada: `AIG-01`.

Fichas: `NLF-01` (construtor de tools e catálogo tipado) · `IDE-01` (editor tipado e referências) ·
`IDE-02` (descritivo padrão e visão narrativa) · `IDE-03` (execução passo a passo) · `IDE-04`
(simulador de canal). `NLF-02`/`NLF-03` (conversor texto ↔ YAML) foram fechadas como descartadas.

## 2. Canais: DialogForm e interpretadores

**Decagon.** Um procedimento para todos os canais; no chat, *generative UI* com biblioteca **fixa** de
componentes aprovados, que o modelo escolhe e preenche por chamada de ferramenta com dados do backend.
DTMF/IVR não aparecem documentados.

**PlugHub.** O DialogForm é a mesma ideia e é **vantagem**: serve fluxo com e sem IA sobre o mesmo roteiro.

**Decisão — a IA usa formulário CADASTRADO e nunca inventa campo.** Gerar formulário a cada chamada só
empurraria para o prompt o que hoje é versionado, validado e mascarado. A IA escolhe qual formulário
apresentar e preenche **só valores dinâmicos** (opções vindas do backend, interpolação), por uma
ferramenta `form_present(form_id, valores)` que devolve o mesmo bloco `render` do `form_get` e passa
pelos mesmos adaptadores. O que falta para isso é **opção dinâmica no DialogForm** (hoje a interpolação
é de passe único, `NIV-12`). E "um fluxo para todos os canais" só vale com os adaptadores funcionando
(`NIV-14..18`).

Ficha: `DLG-35`.

## 3. Testar com histórico

**Decagon.** Conversas reais que falharam viram clientes sintéticos (persona e objetivo extraídos por
LLM), que conversam com a versão nova com ferramentas simuladas; resultado aprovado/reprovado por lote e
rastro da decisão, antes do A/B.

**PlugHub.** Tem as peças e não o recurso: Session Replayer, `quality-export`, avaliador. O *Comparison
Mode* **não é simulação** — o texto de replay é fornecido pelo próprio avaliador
(`evaluation.ts:419`, `session-replayer/models.py:70-72`); nada executa uma versão nova. O harness de
gravação e replay é especificação sem dono (`RRH-01`).

**Decisão — é a peça de maior alavanca**, porque torna seguras a autoria por texto (§1) e o assistente
(§4). Encaixa no modelo de slots: candidato no `next`, clientes sintéticos gerados de sessões reais, MCP
em modo gravado (`RRH-01`), o avaliador existente pontua `next` e `current` com o mesmo formulário, e o
resultado propõe um portão no `promote`.

**Replay de histórico tem um limite, e ele vira resultado (2026-09-25).** Reproduzir as falas do
cliente só funciona enquanto a versão nova pergunta o mesmo que a antiga; no primeiro ponto em que
diverge, a resposta gravada deixa de fazer sentido, e a saída das tools no histórico foi descartada por
mascaramento (R7). Por isso: replay **até divergir**, com a divergência como achado (*"no step X a
versão nova pergunta outra coisa"*), continuação opcional pelo cliente sintético e tools sempre
simuladas.

Ficha: `RRH-02`.

## 4. Assistente de construção

**Decagon.** AOP Copilot (SOP → AOP), Duet (transcrições e QA → AOPs, testes, sugestões) e Duet
Autopilot, em que **toda mudança exige aprovação humana com diff** e é testada contra a conversa de
origem, a regressão e um conjunto de referência. Eficácia: só depoimento.

**PlugHub.** Ausente. (A skill `skill-flow-authoring` é de Claude Code para quem desenvolve o repo; é
base de conhecimento reaproveitável, não recurso do produto.)

**Decisão — construir DEPOIS da §3.** Gerador sem testador produz fluxo plausível e errado. Entrada:
SOP ou transcrições; saída: rascunho de skill + DialogForm + casos de teste, sempre para o `next`, com
diff para aprovação. Fica sem ficha até `RRH-02` e o editor tipado (`IDE-01`) existirem, e
gera STEPS tipados que passam pela mesma validação — nunca texto que compila.

## 5. Informação × análise

**Decagon.** *Ask AI* (perguntas em linguagem natural sobre as conversas), *Suggestions* (artigos de
conhecimento gerados de como os melhores humanos resolveram), *Root Cause* (agrupa as piores conversas e
aponta procedimento, conhecimento ou ferramenta faltante).

**PlugHub.** ~83 rotas de relatório e ~10 telas de análise, todas descritivas; nenhuma diz o que mudar.
O paradoxo: o dado daqui é **melhor** para isso (segmento por participante, desfecho, qualidade de
roteamento por folha, nota por critério, época de deploy).

**Decisão — camada de analista, nesta ordem:** (1) perguntas sobre as conversas, respeitando o escopo de
pool; (2) diagnóstico periódico que agrupa sessões escaladas, transferidas ou mal avaliadas e aponta a
causa em folha, step, conhecimento ou ferramenta; (3) propostas que viram ficha ou rascunho no `next`.
**Pré-requisito: a verdade do dado** (`SFE-01`, `SFE-02`, `RPL-02`) — análise sobre desfecho errado
amplifica o erro com cara de insight.

Fichas: `ANL-01` · `ANL-02` · `ANL-03`.

## 6. Qualidade

**Decagon.** *Watchtower*: critério em linguagem natural aplicado a 100% das conversas, com alertas e
drill-down até a transcrição; *QA Hub* para a amostra humana.

**PlugHub.** O módulo é **mais completo** (contestação, calibração, humanos e IA, 100% pelo modo `all`,
critérios já em linguagem natural com `scoring_guidance`/`applies_when`) e **mais pesado de ligar**: três
configurações antes da primeira nota (formulário, pool avaliador com skill promovido, campanha).

**Decisão — dois modos.** *Monitor rápido*: critério em texto, avaliador fornecido pela plataforma (não
promovido pelo tenant), 100%, alertas. *Avaliação formal*: formulário, campanha e contestação, como hoje.
O custo de LLM em 100% das conversas pede a `USG-04` antes.

Ficha: `QMN-01`. Registradas também, porque só existiam no documento de arco: `EVM-01` (R14) e `EVM-02` (R16).

## 7. Lançamento gradual

**Decisão:** o slot do pool vira unidade de alocação, com o endereço inalterado —
[`adr-pool-slot-allocation-unit.md`](../adr/adr-pool-slot-allocation-unit.md). A conversa passou por três
formas (peso entre snapshots, grupo endereçável, sub-pool com id composto); a escolhida mantém a relação
em **campos**, nunca no nome.

**Achado no caminho, independente do ADR:** a sessão retomada executa o `current` **do momento da
retomada**, não o da versão em que nasceu (`orchestrator-bridge/main.py:1245`; `engine.ts:497-551`).
Depois de um promote, o step gravado pode não existir (falha barulhenta) ou existir com outro
significado (erro calado). Ficha: `SFE-03`.

## 8. Guardrails

**Decisão: opcionais e quase nunca bloqueantes.** Um supervisor LLM revisando toda resposta antes do
envio custa latência (grave em voz) e gera falso positivo. Ficam:

- **Mascaramento no caminho de saída de TODA mensagem de agente** — não um validador externo. Medido:
  hoje ele só roda no `message_send`, só para papel `customer`/`primary`, com falha em `catch {}` mudo, e o
  `notification_send` (por onde `notify` e `menu` dos fluxos falam) não o chama. Ficha: `MSK-04`.
- **Política sobre saída estruturada de step sensível** — um `choice` conferindo o `output_schema` de um
  `reason` (ex.: `valor ≤ limite`) antes do `notify`. É autoria de fluxo, não componente novo.
- **Monitor assíncrono na conferência** — agente que recebe `agents_only` e age no turno seguinte
  (aciona supervisor, `escalate`, alerta), sem segurar a resposta. Já previsto no modelo de sessão.

## 9. Outros modelos, que mudam O QUE o negócio edita (2026-09-25)

- **Política como dado — tabelas de decisão.** O que muda com frequência num atendimento é a política
  (limite de reembolso, elegibilidade, oferta por plano, prazo por região), não a estrutura do fluxo.
  Tabela com condições e saídas em colunas TIPADAS (ligadas a tags do ContextStore e a campos de
  retorno de tool), validada contra sobreposição, lacuna e tipo, com caso de teste por linha; o fluxo
  tem um step que decide pela tabela e não muda quando a política muda. É a quinta costura, ao lado de
  conteúdo × controle × canal × segredo: **política**. Congelada no snapshot do slot no promote, como o
  DialogForm. É a resposta confiável ao "mude sem programar". Ficha: `DTB-01`.
- **Cenários de aceite com vocabulário fechado.** Dado/Quando/Então sobre tags, tools e desfechos do
  catálogo, com autocompletar. Não gera fluxo: TESTA o fluxo no simulador. Cenário vermelho bloqueia o
  promote; o conjunto cresce com os casos reais que falharam. Ficha: `SCN-01`.
- **Biblioteca de blocos certificados.** Sub-fluxos reutilizáveis e versionados (identificar cliente,
  validar PIN, confirmar endereço, transferir com contexto) com interface declarada e descritivo do
  bloco inteiro. Formaliza o que `delegate` + `dialog_runner` + OTP já fazem. Ficha: `BLK-01`.
- **Mineração de processo.** O caminho REAL dos contatos sobre o fluxo DESENHADO, com volume por ramo:
  ramo morto, desvio frequente, onde se perde cliente. Dado já existe (segmentos, `pipeline_state`
  persistido, `deploy_version`). Entrada natural do `ANL-02`. Ficha: `PMN-01`.
- **Por objetivo e requisitos** (declarativo: o processo diz o que precisa, o motor planeja a ordem e
  só pergunta o que falta; semente em `required_context` + `@ctx.__gaps__` + `resolve`). **Sem ficha**:
  revisitar quando o editor existir, porque tira do autor a visão da sequência e torna o simulador
  obrigatório.

## Ordem

1. Verdade do dado: `SFE-01`, `SFE-02`, `SFE-03`, `AIG-01`.
2. `IDE-01` editor tipado e `NLF-01` construtor de tools.
3. `IDE-02` descritivo padrão e visão narrativa.
4. `DTB-01` política como dado.
5. `IDE-03` execução passo a passo, `IDE-04` simulador de canal, `RRH-02` replay e simulação, com
   `SCN-01` cenários de aceite.
6. `QMN-01` monitor rápido de qualidade.
7. `ANL-01..03` camada de analista, com `PMN-01` mineração de processo.
8. `BLK-01` biblioteca de blocos.
9. Assistente de construção (sem ficha).
10. `SLT-01..05` lançamento gradual.

## Fontes (Decagon)

[AOP](https://decagon.ai/product/aop) ·
[From SOPs to AOPs, 2025-11-26](https://decagon.ai/blog/from-sops-to-agent-operating-procedures) ·
[Simulations, 2025-09-23](https://decagon.ai/blog/decagon-simulations) ·
[AOP Copilot, 2025-09-23](https://decagon.ai/blog/aop-copilot) ·
[Duet, 2026-03-19](https://decagon.ai/blog/introducing-duet) ·
[Duet Autopilot, 2026-06-09](https://decagon.ai/blog/autopilot) ·
[Ask AI, 2025-07-16](https://decagon.ai/blog/decagon-ask-ai) ·
[Suggestions](https://decagon.ai/product/suggestions) ·
[Automatic optimization / Root Cause, 2026-04-23](https://decagon.ai/blog/automatic-optimization) ·
[Watchtower](https://decagon.ai/product/watchtower) ·
[QA Hub, 2026-05-28](https://decagon.ai/blog/qa-hub) ·
[Guardrails em camadas, 2025-07-29](https://decagon.ai/resources/designing-layered-guardrails-for-reliable-ai-agents) ·
[Generative UI](https://decagon.ai/glossary/what-is-generative-ui) ·
[Setup, 2026-01-10](https://decagon.ai/blog/ai-customer-support-setup) ·
críticas de terceiros: [fin.ai (concorrente)](https://fin.ai/learn/ai-agent-procedures-aops-journeys),
[eesel (concorrente)](https://www.eesel.ai/blog/decagon-review).
