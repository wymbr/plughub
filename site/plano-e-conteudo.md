# PlugHub — Site de Divulgação: Plano, Navegação e Conteúdo

> **Decisões:** idioma **pt-BR** primeiro, com **versão em inglês** (`site/en/`) e **seletor de idioma** em todas as páginas; tom **minimamente técnico** (acessível, mas crível para avaliador técnico); CTA primário **documentação / autoatendimento** (atrair técnicos para explorar e testar). Entrega: este plano + a landing page funcional `site/index.html`.
> **Honestidade (regra de ouro):** o site só comunica o que está **implementado**. O framework *Business in Any Media* (cadastro de identidade cross-canal, commerce-cards) é **roadmap** — pode aparecer como visão/"para onde vamos", nunca como recurso pronto.
> **Data:** Junho 2026.

---

## 1. Estratégia de divulgação

**Objetivo do site:** ser a porta de entrada técnica do PlugHub — explicar o produto de forma clara, gerar credibilidade arquitetural, e **converter para autoatendimento**: explorar a documentação e criar uma conta/ambiente de teste.

**Funil (self-service, não sales-led):**
`Descoberta (home) → Entendimento (como funciona / para devs) → Confiança (arquitetura, compliance, prova) → Ação (documentação → testar)`.

**Posicionamento de categoria:** não somos "mais um chatbot/CCaaS". Somos a **plataforma de orquestração de agentes (humanos + IA) para atendimento e automação**, com **compliance e portabilidade by design**.

**Princípios de comunicação:**
- **Mostre, não prometa.** Cada afirmação forte tem lastro técnico (modelo de sessão, MCP, SDK).
- **Técnico na medida.** Frases curtas, um conceito por bloco; profundidade fica na documentação (link), não na home.
- **Neutralidade/soberania como valor** (sem lock-in, BYO LLM) — fala ao medo de aprisionamento.
- **Honesto sobre maturidade** — separar "hoje" de "roadmap".

---

## 2. Públicos e mensagem central (dev-first)

| Público | Dor | Mensagem |
|---|---|---|
| **Desenvolvedor / arquiteto (primário)** | frameworks de agente não têm canais/operação; medo de lock-in | "Traga seu LLM e seus agentes. Orquestre com um fluxo declarativo, integre só por MCP, e leve embora quando quiser." |
| **Head de CX / operações** | handoff humano↔IA quebrado; bot engessado | "Humano e IA na mesma conversa, sem transferência visível. Você dirige a IA, não substitui o time." |
| **CISO / DPO** | LGPD, auditoria, dados sensíveis | "Mascaramento por papel, auditoria em toda chamada, supervisão humana nativa. Compliance é primitiva, não plugin." |
| **Gestor de produto/negócio** | custo imprevisível de IA | "Billing por capacidade: você sabe quanto vai pagar. Sem bill shock por volume." |

> Como o CTA é autoatendimento, a home prioriza o **desenvolvedor/arquiteto**, com trilhas laterais para CX/CISO/negócio.

---

## 3. Posicionamento e taglines

- **Tagline principal (hero):** *"Orquestre humanos e IA na mesma conversa."*
- **Sub-tagline:** *"Plataforma de orquestração de agentes para atendimento e automação — compliance e portabilidade by design."*
- **Tagline de visão (seção dedicada, claramente "para onde vamos"):** *"Business in Any Media — nunca perca um negócio por causa de canal."*
- **Selos curtos (chips do hero):** MCP-first · BYO LLM · Humano + IA na mesma sessão · LGPD by design · Billing por capacidade.

---

## 4. Mapa de navegação (sitemap)

### 4.1 Estrutura-alvo (multi-página, para evolução)

```
/                      Home
/produto               Como funciona (modelo de conferência, Skill Flow, 3 níveis)
/canais                Omnichannel (voz/PSTN, WhatsApp, webchat, WebRTC, SMS, e-mail, webhook)
/solucoes              Casos de uso
   /solucoes/atendimento
   /solucoes/automacao-de-processos
   /solucoes/comercio-conversacional   (marcar visão/roadmap onde couber)
/desenvolvedores       SDK, MCP, BYO LLM, skill-flow YAML, portabilidade  ← trilha-chave
/qualidade             Avaliação por campanhas, contestação/calibração, Bancada, session replay  ← detalhada (site/qualidade.html)
/compliance            LGPD, mascaramento, auditoria, supervisão humana, BACEN/DORA
/precos                Billing por capacidade (modelo; "fale conosco" p/ cotação)
/docs                  Documentação (autoatendimento) ← CTA primário (externo)
/sobre                 Empresa, visão, contato
/recursos              Blog / relatórios de mercado / materiais técnicos
```

Navegação primária (topo): **Produto · Canais · Soluções · Desenvolvedores · Compliance · Qualidade · Documentação** + botão **"Começar a testar"**.
Rodapé: produto, soluções, desenvolvedores, compliance, empresa, docs, contato, legal (privacidade/LGPD).

### 4.3 Internacionalização (i18n) — PT + EN com seletor

Estratégia escolhida: **arquivos separados por idioma** (não i18n por JS), melhor para SEO, compartilhamento e ausência de FOUC.

```
site/
  index.html  desenvolvedores.html  qualidade.html  compliance.html   ← pt-BR (raiz)
  en/
    index.html  developers.html  quality.html  compliance.html        ← inglês (slugs em inglês)
```

- **Seletor de idioma:** chip discreto `EN`/`PT` no header (`.lang`), em **todas as 8 páginas**, apontando para o par traduzido (ex.: `index.html` ⇄ `en/index.html`; `qualidade.html` ⇄ `en/quality.html`; `desenvolvedores.html` ⇄ `en/developers.html`; `compliance.html` ⇄ `en/compliance.html`).
- **SEO:** cada página declara `<link rel="alternate" hreflang="pt-BR">` e `hreflang="en">` para o par.
- **Âncoras:** a home EN usa âncoras em inglês (`#how-it-works`, `#routing`, `#channels`, `#customer`, `#solutions`, `#devs`, `#quality`, `#compliance`, `#try`); a PT mantém as em português.
- **Adaptação de tom (não tradução literal):** "LGPD by design" → "privacy by design" na home EN; referências regulatórias muito locais suavizadas para público internacional (ex.: "ANS" → "healthcare regulators in Brazil"), **mantendo** os nomes BACEN/ANS/ANATEL/PL 2338 na página de compliance EN, pois descrevem aderência ao mercado brasileiro. Notas de honestidade preservadas (commerce = *vision*; Business in Any Media e reconhecimento cross-canal = *roadmap*; agente externo = *participant*; SOC 2/ISO = *roadmap*; sentimento mira o cliente).

### 4.2 MVP single-page (o que está em `site/index.html`)

Uma home navegável por âncoras, cobrindo o essencial do funil:

1. **Hero** — tagline + sub + chips + CTAs (Documentação / Começar a testar).
2. **A virada** — o problema (silos, handoff quebrado, lock-in) → o modelo.
3. **Diferenciais (7)** — conferência unificada · MCP + interception guard · AI Gateway agnóstico · motor único Skill Flow · billing por capacidade · **qualidade como melhoria contínua** (não só ferramenta) · **atendimentos interligados por construção** (a Journey, agora por construção). (+ bônus: omnichannel real.)
4. **Como funciona** — modelo de conferência + 3 níveis (a/b/c) + Skill Flow + callout "o fio nunca se perde" (processo interligado por construção, sem correlacionar contatos manualmente).
4b. **Roteamento inteligente** — o orquestrador decide quem atende o quê: por perfil/skill, por SLA (tempo de espera), por disponibilidade e performance; pausa/heartbeat como filtros rígidos; fila atendida (agente de IA acompanha a espera).
5. **Canais** — grade de canais (com voz/WebRTC nativos).
6. **Lado do cliente · Business in Any Media** — comece/continue/acompanhe em qualquer canal; continuidade do processo por construção (channel-abstract + suspend/resume); reconhecimento automático cross-canal marcado como **roadmap**.
7. **Casos de uso** — atendimento · automação de processos · comércio conversacional (este marcado "visão").
8. **Para desenvolvedores** — SDK, MCP, BYO LLM, YAML declarativo, portabilidade; CTA docs.
9. **Compliance & segurança** — masking, auditoria, oversight, LGPD/BACEN/DORA.
10. **Qualidade & avaliação** — campanhas, contestação/calibração, Bancada, session replay; link para `/qualidade`.
11. **CTA final** — documentação + criar ambiente de teste.
12. **Rodapé**.

---

## 5. Conteúdo página a página (resumo de copy)

### Home (implementada no index.html)
- **Hero:** "Orquestre humanos e IA na mesma conversa." / sub / chips / [Ver documentação] [Começar a testar].
- **A virada:** "Agentes de IA vivem presos em silos e o handoff humano↔IA quebra o atendimento. O PlugHub trata humano e IA como participantes iguais da mesma sessão — e te tira do lock-in."
- **Diferenciais:** 5 cards (texto curto cada — ver index.html).
- **Como funciona:** "Todo contato é uma sala de conferência" + os 3 níveis (fluxo de negócio channel-abstract → acesso a canais → agente de I/O) + "um motor declarativo (Skill Flow) para inbound, outbound e automação".
- **Canais:** grade.
- **Casos de uso:** 3 cards.
- **Devs:** SDK (TS/Python), MCP como única integração, BYO LLM, skill-flow em YAML versionável, `verify-portability`. CTA → docs.
- **Compliance:** masking por papel, auditoria em toda chamada MCP, supervisão humana nativa; ganchos LGPD/BACEN/DORA.
- **Qualidade:** Console de orquestração humana, sentimento em tempo real, session replay, bancada humano×IA.
- **CTA final + rodapé.**

### /visao (implementada — site/visao.html · en/vision.html)
- **Página de visão/manifesto** — o "porquê" estratégico que faltava ao site. Narrativa: a primeira onda (responder contatos isolados) virou commodity → o valor migrou para o **processo** → o obstáculo é **confiança** → o **dial do anteparo** (humano no comando, IA recua na velocidade que a avaliação libera) → quatro fundamentos já na arquitetura (conferência humano+IA, qualidade auditável, dado sensível sem exposição, processo como unidade/sem lock-in) → por que paga (muda o numerador do ROI).
- **Honestidade:** a seção de **modelo de entrega (produto + professional service)** do material interno foi **omitida** da página pública — é narrativa de negócio/vendas, não recurso de produto, e destoaria do CTA self-service. Todos os claims se restringem ao que está implementado.
- **Encaixe na nav:** primeiro item ("Visão"/"Vision") em todas as 8 páginas; **teaser** na home (PT/EN) logo após o hero, com "Ler a visão →". Seções deep-linkam para Como funciona, Qualidade e Compliance. Seletor PT⇄EN interligado.

### /desenvolvedores (implementada — site/desenvolvedores.html) · trilha-chave do CTA
- **Conceitos em 1 min:** sessão = conferência; contrato `agent_login→ready→busy→done`; Skill Flow declarativo; MCP única integração.
- **Skill Flow:** exemplo YAML (reason/choice/task), os 13+ tipos de passo, deploy lifecycle (draft/published, hot deploy, rollback, agendado, graceful).
- **MCP + interception guard:** permissão → anti-injeção → auditoria (<1ms, sem opt-out); nativo (in-process) × externo (proxy sidecar).
- **BYO LLM / AI Gateway:** multi-conta, fallback/rotação cross-provider, perfis realtime/balanced/evaluation.
- **SDK & portabilidade:** TS+Python; traga agente externo como **participante** e migre para nativo (`regenerate`); CLI `certify`/`verify-portability`/`skill-extract`/`proxy`; anti-lock-in por 4 vias. Nota honesta: externo = participante; orquestração plena é nativa.
- Nav interligada nas 4 páginas (home/desenvolvedores/qualidade/compliance).

### Ajuste no roteamento (home): "skill" abrange agente **e cliente**
- A seção "Roteamento inteligente" deixa claro que o peso da fila combina competência do **agente** e perfil do **cliente** (prioritário, gold, VIP), além de SLA/disponibilidade/performance.

### /qualidade (implementada — site/qualidade.html)
- **Por que importa:** Gartner — >40% dos projetos de IA agentic cancelados até 2027; o diferencial é qualidade mensurável e governada.
- **Avaliação por campanhas:** formulários (objetivos + subjetivos por dimensão); campanhas (formulário, contínua/período, % por agente, agendamento); avaliador de IA com evidência (RAG) + avaliador humano.
- **Contestação & revisão:** gate de IA pré-publicação → contestação por dimensão → decisão final humana; trilha append-only.
- **Calibração do avaliador:** curadoria amostral por regras → sinal de calibração → feedback ao avaliador via RAG (loop de melhora).
- **Bancada de comparação 360°:** humano×IA, versão×versão, deploy epochs (significância estatística), lentes (heatmap dimensão, radar, escalação, NPS, wrap-up, média de pool), cross-cut (resolução×qualidade×NPS com flags ★/⚠/◑), export CSV.
- **Os 360° / não confiar cego na nota:** avaliador de IA (operação) + revisor IA/humano (governança — supervisiona e evolui o avaliador) + NPS (voz do cliente); o Cross-cut acende o alerta quando os três discordam (ex.: avaliação alta × NPS baixo).
- **Session replay + diff:** replay determinístico + comparação turn-a-turn antes de promover.
- Conecta com compliance (masking/auditoria) e regulação (ANS/EU AI Act — rastreabilidade/oversight).

### /compliance (implementada — site/compliance.html)
- **Pilares:** mascaramento por papel (tokenização + masked input atômico + contexto), auditoria em toda chamada MCP (imutável, sem opt-out, interception guard), supervisão humana nativa, RBAC+ABAC (módulo/campo/pool/grupo-turno), visibilidade por participante, sem lock-in/soberania.
- **Aderência BR:** LGPD/ANPD, BACEN Res. 4.893, ANS RN 623, ANATEL RGC, PL 2338 (marcado "preparação").
- **Aderência Europa:** GDPR + adequação UE↔Brasil, EU AI Act (transparência/oversight/logging; nota Art. 5 — sentimento mira o cliente, não o trabalhador), DORA, soberania de dados.
- **Invariantes** (garantias da arquitetura) + **nota honesta:** SOC 2/ISO 27001 são **roadmap**; a arquitetura já produz a evidência técnica.
- Nav (home + qualidade + compliance) interligada; home traz um teaser com link "Conheça compliance & segurança →".

### Versão em inglês (en/) — implementada
- **`en/index.html`** (home), **`en/developers.html`**, **`en/quality.html`**, **`en/compliance.html`** — tradução fiel das 4 páginas PT, mesmo design system, mesmas notas de honestidade. Seletor de idioma `PT`/`EN` interligado em todas. Ver §4.3.

### Placeholders de rota — implementados
- **`/docs`** (`site/docs/index.html`) e **`/privacidade`** (PT, `site/privacidade/index.html`) e **`/privacy`** (EN, `site/privacy/index.html`): páginas "em breve"/stub on-brand para os links absolutos do site não ficarem mortos até o conteúdo real existir. Substituir por documentação e política reais no deploy.

### /solucoes
- **Atendimento:** humano+IA na mesma sessão, omnichannel, roteamento por SLA/skill/performance, qualidade.
- **Automação de processos:** workflows com aprovação humana, suspend/resume, coleta assíncrona, webhook como canal.
- **Comércio conversacional:** marcar claramente como **visão/roadmap** (Business in Any Media) — não vender como pronto.

### /precos
- Explicar **billing por capacidade** (licenças simultâneas, humanas + IA) vs. consumo/turno. CTA "fale conosco" para cotação (exceção ao self-service, pois preço enterprise é negociado).

---

## 6. Jornada de conversão (self-service)

- **CTA primário (repetido):** "Ver documentação" → `/docs`.
- **CTA secundário:** "Começar a testar" → cadastro de ambiente de teste / early access (hoje pode ser formulário simples ou e-mail; back-end depois).
- **Micro-conversões:** baixar o descritivo técnico (PDF), ler um relatório de mercado, ver um exemplo de skill-flow YAML.
- **Sem formulário pesado** na home — coerente com público técnico (eles querem ver, não preencher).

---

## 7. SEO / metadados (pt-BR)

- **Title:** "PlugHub — Orquestração de agentes humanos e IA para atendimento e automação".
- **Meta description:** "Plataforma de orquestração de agentes (humanos + IA) com compliance e portabilidade by design. MCP-first, BYO LLM, billing por capacidade, omnichannel com voz e WebRTC nativos."
- **Palavras-chave:** orquestração de agentes de IA, contact center IA, agentes de IA atendimento, MCP, BYO LLM, automação de processos com IA, CCaaS, LGPD atendimento, comércio conversacional.
- **Open Graph/Twitter cards** + favicon + sitemap.xml + robots.txt (implementar no deploy).
- **Performance:** página estática, sem dependências pesadas (a landing usa só fonte/ícones de CDN permitido).

---

## 8. Diretrizes de marca e tom

- **Cores (design system do produto):** primary `#1B4F8A`, secondary `#2D9CDB`, accent `#00B4D8`, sucesso `#059669`, alerta `#D97706`. Fundo claro, superfícies brancas, flat (sem gradientes pesados/sombras exageradas).
- **Tipografia:** Inter.
- **Tom:** direto, técnico-acessível, sentenças curtas; sem jargão gratuito; sem superlativos vazios. Português do Brasil.
- **Honestidade:** nunca apresentar roadmap como pronto; usar rótulo "visão"/"em breve" onde aplicável; claims competitivos factuais.

---

## 9. Próximos passos (pós-landing)

1. Back-end das CTAs: formulário de early access + handoff para docs reais (`/docs`).
2. Analytics (privacy-friendly) + eventos de conversão.
3. Páginas internas (/desenvolvedores, /compliance, /qualidade) ✅ implementadas; falta /solucoes detalhada e /precos.
4. Versão **inglês** (`site/en/`, mercado Europa — ver `mercado-portugal-europa-2026.md`) ✅ implementada com seletor de idioma; pt-PT pendente se priorizar lusofonia.
5. Materiais de apoio: descritivo técnico (PDF), exemplos de skill-flow, casos.
6. Substituir placeholders `/docs`, `/privacidade`, `/privacy` por conteúdo real no deploy.
