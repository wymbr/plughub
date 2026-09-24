# Decagon × PlugHub — o que adotar e o que mudar

> Estado: direção de produto aceita pelo dono em 2026-09-24 · Fichas em `pending.md`, sob este
> documento e sob os grupos citados em cada seção.
> Origem: [`competitive-analysis-2026-09.md`](competitive-analysis-2026-09.md) § 5.2 (a Decagon abriu
> escritório em São Paulo em 15/09/2026).

## Método

Sobre a Decagon: blog e páginas oficiais, mais terceiros — quase todos **concorrentes** dela, então as
críticas pesam menos. **A sintaxe da AOP não é pública** (a documentação exige login). Sobre o PlugHub:
medido no código e no ledger em 2026-09-24.

---

## 1. Autoria: AOP em linguagem natural × YAML × editor gráfico

**Decagon.** A AOP é texto que "compila" para lógica executável. A interface com o backend **não** é
resolvida em linguagem natural: integração, autenticação e schema de ferramenta são dos engenheiros,
versionados em git; os passos críticos (reembolso, identidade) rodam em código. A crítica mais citada é
depurar: com um artefato só, não se sabe se o erro está no texto, na lógica compilada ou na integração.

**PlugHub hoje.** Skill Flow YAML (42 skills), editor Monaco com validação ao vivo
(`POST /v1/skills/validate`). O editor gráfico prometido quando o arco n8n foi abortado **não começou**.
Linguagem natural já em uso: instrução de `reason` dentro do `input`, e `description`/`examples` nas
folhas do orquestrador.

**Decisão — o texto é a superfície de autoria e o YAML é o artefato gerado; o editor gráfico sai.**

- O **LLM entra só na autoria**, nunca na execução. O conversor decide, passo a passo, o que vira step
  determinístico (menu, `invoke`, `choice`, coleta mascarada) e o que vira `reason` com instrução. Um
  fluxo sem IA sai sem `reason` nenhum: o mesmo texto serve aos dois tipos de agente, e em runtime não há
  LLM.
- **Uma fonte só:** o texto é a fonte do autor; o YAML, gerado, é o que se revisa e o que roda. Os dois são
  versionados juntos no snapshot. Há o sentido inverso (YAML → texto) para os skills existentes.
- **Conversão incremental com diff:** mudar um parágrafo só muda os steps dele; reescrita fora do escopo
  é reprovação.
- **Não inventa interface:** usa só ferramentas do catálogo MCP e DialogForms cadastrados; texto que cita
  o que não existe é recusado com o nome do que falta. Por isso o **catálogo tipado vem antes** — hoje
  `interface.input_schema` é *campo → descrição em texto* (`skill.ts:75-78`) e o `mcp_server` declarado
  não é validado (`CAP-08`).
- As instruções em texto precisam de casa versionada: é a `AIG-01` (o `prompt_id` que não aponta para nada).

Fichas: `NLF-01` (catálogo tipado) · `NLF-02` (conversor) · `NLF-03` (YAML → texto).

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

Ficha: `RRH-02`.

## 4. Assistente de construção

**Decagon.** AOP Copilot (SOP → AOP), Duet (transcrições e QA → AOPs, testes, sugestões) e Duet
Autopilot, em que **toda mudança exige aprovação humana com diff** e é testada contra a conversa de
origem, a regressão e um conjunto de referência. Eficácia: só depoimento.

**PlugHub.** Ausente. (A skill `skill-flow-authoring` é de Claude Code para quem desenvolve o repo; é
base de conhecimento reaproveitável, não recurso do produto.)

**Decisão — construir DEPOIS da §3.** Gerador sem testador produz fluxo plausível e errado. Entrada:
SOP ou transcrições; saída: rascunho de skill + DialogForm + casos de teste, sempre para o `next`, com
diff para aprovação. Fica sem ficha até `RRH-02` e `NLF-02` existirem.

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

## Ordem

1. Verdade do dado: `SFE-01`, `SFE-02`, `AIG-01`, e `SFE-03`.
2. `RRH-02` — simulação com histórico sobre o `next`.
3. `NLF-01..03` — autoria em texto que gera YAML.
4. `QMN-01` — monitor rápido de qualidade.
5. `ANL-01..03` — camada de analista.
6. Assistente de construção (sem ficha até 2 e 3).
7. `SLT-01..05` — lançamento gradual.

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
