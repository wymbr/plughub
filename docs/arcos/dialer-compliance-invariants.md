# Dialer Compliance — Invariantes Arquiteturais

> Status: **Proposta arquitetural — validar com time de Media Gateway**
> Último update: Maio 2026
> Escopo: media gateway interno SIP/PSTN + media gateway WebRTC, no contexto de skill-flows outbound

---

## Contexto

Quando outbound é executado pelo mesmo motor que inbound (Skill Flow) e o pacing/dialing fica no **media gateway interno**, há um risco arquitetural específico que precisa ser endereçado antes de qualquer cliente entrar em produção em campanhas de volume: um operador que configure mal o YAML pode violar regulação de pacing e expor cliente e plataforma a multas regulatórias.

As regulações mais relevantes:

- **TCPA (US)** — abandonment ratio máximo de 3% medido em janela móvel de 30 dias por campanha; gravação obrigatória; consentimento prévio em ligações automatizadas
- **LGPD (BR)** — direito ao opt-out instantâneo; respeito a janela horária; minimização de dados coletados
- **GDPR (EU)** — base legal explícita; direito ao apagamento; auditoria completa
- **Resolução Anatel (BR)** — janela de 9h às 21h em dias úteis e 10h às 16h aos sábados; proibição em domingos e feriados

O dano legal por violação pertence ao cliente, mas o dano reputacional é da plataforma. Por isso, o **compliance guard do media gateway é invariante arquitetural** — não importa o que o Skill Flow YAML declare, o motor enforça as regras antes de discar.

---

## Princípio de design

> O Skill Flow YAML configura **dentro** dos limites; nunca pode bypassar os limites.

| YAML pode configurar | YAML não pode |
|---|---|
| Algoritmo de pacing (power, predictive, progressive, preview) | Desabilitar enforcement de abandonment ratio |
| Política de retry (intervalos, máximo de tentativas) | Discar contato em lista DNC |
| Janela horária preferida (dentro dos limites legais) | Discar fora de janela horária regulada |
| DNC scope adicional (tenant + campanha) | Bypassear DNC nacional |
| Threshold de answering machine | Suprimir audit trail |

---

## Invariantes

### 1. Abandonment Ratio Compliance

| Jurisdição | Limite legal | Janela de medição |
|---|---|---|
| TCPA (US) | 3% | 30 dias móveis por campanha |
| LGPD (BR) | Não estipulado por lei — convenção 3% | Configurável; default 30 dias |
| GDPR (EU) | Não estipulado por lei — convenção 3% | Configurável; default 30 dias |

**Enforcement:**
- O media gateway monitora abandonment ratio em janela móvel **por campanha (não por flow)**
- Janela é calculada continuamente a cada disposição de chamada (Connected / No Answer / Busy / Abandoned)
- Se o ratio cumulativo na janela ultrapassar o threshold configurado:
  - Pacing é forçado para 1:1 (predictive desabilitado)
  - Alerta WARN é emitido em `dialer.compliance.events` Kafka topic
  - Audit trail registra a transição com timestamp, campanha e ratio observado
  - **Operador não pode reativar predictive até a janela móvel cair abaixo do threshold**

### 2. DNC List (Do Not Call) Enforcement

Lista DNC é mantida em **três níveis hierárquicos com precedência clara**:

| Nível | Fonte | Bypass possível |
|---|---|---|
| **Nacional** | Import automático de DNC registries quando aplicável (FTC US, etc.) | Nunca |
| **Tenant** | Lista do cliente — uploads, deduplicação automática, audit de alterações | Nunca |
| **Campanha** | Opt-out específico desta campanha (gerado pelo próprio cliente durante atendimento) | Nunca |

**Enforcement:**
- Antes de cada dial, o media gateway verifica os três níveis em sequência
- **Match em qualquer nível = bloqueio**. A chamada não sai.
- Bloqueio é registrado em audit (`dnc_block_audit`) com motivo do bloqueio e nível que disparou
- O YAML pode declarar política de retry para contatos bloqueados (skip, mover para revisão humana, escalar), mas não pode tentar discar de novo no mesmo ciclo

### 3. Janela Horária por Timezone do Contato

**Enforcement:**
- A janela horária é avaliada **pelo timezone do contato, não pelo timezone do operador**
- O contato carrega `timezone` no mailing; se ausente, fallback é o timezone da campanha
- Limites legais por jurisdição (configurados na plataforma):

| Jurisdição | Dias úteis | Sábado | Domingo/Feriado |
|---|---|---|---|
| BR (Anatel) | 9h – 21h | 10h – 16h | Proibido |
| US (TCPA) | 8h – 21h | 8h – 21h | Sem restrição federal (estaduais aplicam) |
| UK | 8h – 21h | 8h – 21h | Proibido por convenção |

- O YAML pode estreitar a janela (ex.: "discar só entre 14h e 18h") mas **nunca alargar**
- Tentativas fora de janela são bloqueadas e reagendadas automaticamente para próxima janela válida

### 4. Pacing Algorithm Limits

Os quatro algoritmos suportados, com seus limites embutidos:

| Algoritmo | Dial Multiplier | Restrições |
|---|---|---|
| **Preview** | 1:1, com confirmação humana antes do dial | Sem restrições adicionais |
| **Progressive** | 1:1, dial automático ao agente ficar disponível | Sem restrições adicionais |
| **Power** | N:1 (N configurável, max 3:1 default) | Multiplier > 2:1 só permitido se abandonment ratio < 1% nos últimos 7 dias |
| **Predictive** | Dinâmico baseado em connection probability + AHT | Multiplier teto = 3:1; desabilitado automaticamente se abandonment > threshold |

A configuração de multiplier máximo é feita na plataforma (config namespace `dialer.pacing_limits`), não no YAML — campanhas individuais não podem subir teto sem ação administrativa.

### 5. Audit Trail Completo

Toda decisão do dialer gera evento em `dialer.compliance.events` Kafka topic:

| Evento | Conteúdo |
|---|---|
| `dial_attempt` | campanha, contato, timezone, janela aplicada, decisão (dial / block / defer) |
| `dial_blocked` | motivo (DNC / janela / quota / abandonment), nível de DNC se aplicável |
| `dial_deferred` | razão, próxima janela válida calculada |
| `pacing_change` | algoritmo anterior, novo algoritmo, motivo (manual / auto-throttle) |
| `dnc_added` | contato, nível, fonte, operador |
| `abandonment_threshold_crossed` | campanha, ratio observado, janela, ação tomada |

Eventos têm retenção mínima de 7 anos em ClickHouse (cumpre TCPA + LGPD + SOX) com particionamento por mês e índice por campanha + contato.

---

## Configuração via Config API

Namespace `dialer.compliance`:

| Chave | Default | Descrição |
|---|---|---|
| `abandonment_threshold_pct` | `3.0` | Ratio máximo antes de forçar 1:1 |
| `abandonment_window_days` | `30` | Janela móvel de medição |
| `pacing_max_multiplier` | `3.0` | Multiplier máximo permitido (power/predictive) |
| `dnc_national_sync_interval_hours` | `24` | Frequência de sync da DNC nacional |
| `legal_window_jurisdiction` | `BR` | Default de jurisdição para janela horária |
| `audit_retention_years` | `7` | Retenção mínima de eventos compliance |

Mudanças nessa namespace requerem role `admin` + audit. Alterações são logadas em `config.audit.events`.

---

## Posicionamento competitivo

A maior parte dos dialers do mercado (Genesys Outbound Engagement, NICE Outbound ex-Mature, Five9 Power Dialer, Avaya POM, Aspect) tem compliance enforcement *como feature configurável* — ou seja, o operador pode desabilitar. Isso transfere responsabilidade legal inteira para o cliente e historicamente tem gerado multas significativas em campanhas mal-supervisionadas (vide settlements TCPA documentados pela FTC).

A escolha do PlugHub de **tornar compliance um invariante do motor, não uma feature do flow**, gera dois efeitos:
- **Argumento direto com CISO/Legal**: a plataforma garante por design que campanhas operam dentro dos limites; risco legal é menor
- **Vendor lock-in invertido**: clientes que tentaram sair de plataformas com compliance configurável e ganharam autonomia depois percebem que perderam o guard — voltar para guard arquitetural é uma decisão estrutural difícil de desfazer

---

## Itens em aberto para validação

- [ ] Confirmar com time de Media Gateway que abandonment ratio é medido por campanha (não por flow) — alternativa seria por tenant agregado, com trade-offs próprios
- [ ] Validar lista de jurisdições suportadas no `legal_window_jurisdiction` — escopo inicial é BR + US + UK; expansão para EU full e LATAM em fase posterior
- [ ] Definir se o YAML pode declarar exceções para campanhas B2B (que têm requisitos regulatórios diferentes em algumas jurisdições)
- [ ] Confirmar retention de 7 anos para audit — alguns setores (financeiro BR) exigem 10 anos

---

## Referências cruzadas

- [`product/value-proposition.md`](../product/value-proposition.md) — Diferencial 5 (Outbound unificado)
- [`product/competitive-analysis.md`](../product/competitive-analysis.md) — matriz de capacidades, linha "Outbound unificado"
- [`product/overview.md`](../product/overview.md) — Atendimento omnichannel, media gateways
- [`adr/`](../adr/) — esse doc pode virar ADR após validação técnica
- [`kafka-eventos.md`](../kafka-eventos.md) — `dialer.compliance.events` topic schema (a definir)
