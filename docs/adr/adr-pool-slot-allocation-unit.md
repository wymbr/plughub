# ADR: O slot do pool vira unidade de ALOCAÇÃO — lançamento gradual sem mudar o endereço

**Status:** Proposto.
**Data:** 2026-09-24
**Componentes:** `packages/agent-registry` (`PoolSkillSlot`, `set-next`/`promote`/`rollback`, `SkillDeployment`),
`packages/orchestrator-bridge` (`get_pool_current_flow`, `instance_bootstrap`, carimbo do segmento),
`packages/routing-engine` (saída da fila, alocação), `packages/skill-flow-engine` (`pipeline_state`),
`packages/analytics-api` (`segments`, relatórios por pool), `packages/platform-ui` (deploy, análise).
**Relacionado:** [`adr-deploy-time-content-snapshot.md`](adr-deploy-time-content-snapshot.md) (o snapshot do
slot congela flow **e** conteúdo), [`adr-human-agent-pool-scoped-identity.md`](adr-human-agent-pool-scoped-identity.md),
[`adr-journey-session-segment-model.md`](adr-journey-session-segment-model.md) (D10: dois pools),
`docs/product/skill-versioning-deploy-spec.md`, `docs/product/decagon-paralelo-2026-09.md` § 7,
`CLAUDE.md` § Invariants (*"O POOL é a unidade endereçável"*).

---

## Contexto

O deploy de um pool tem três slots (`previous` · `current` · `next`, `schema.prisma:232`), e só o
`current` **executa**. Promover é trocar 100% do tráfego de uma vez: não existe canário, nem A/B, nem
forma de comparar a versão candidata com a atual sobre tráfego real antes de assumir o risco. Os
concorrentes diretos já tratam isso como requisito básico (Sierra *release governance*, Decagon
*Experiments*, ver `competitive-analysis-2026-09.md` § 5.2).

Três formas foram consideradas na conversa de 2026-09-24:

| Opção | O que é | Por que não (ou por que sim) |
|---|---|---|
| A. Peso entre slots, sem recurso | `current`/`next` continuam só snapshots; o bridge sorteia qual executar | Não reaproveita instância, capacidade nem roteamento; não abre caminho para roteamento por carga ou máquina |
| B. Grupo endereçável → pools executores | dois pools (`x_current`, `x_next`) e um endereço lógico acima | Reaproveita tudo, mas **muda o invariante** e obriga os ~10 pontos que endereçam pool (webhook, `mentionable_pools`, hooks, transferência, `queue_config`, agenda, campanha, `accessible_pools`, SLA, dashboards) a resolver o alias |
| C. Sub-pool como id composto | `pool_id_current` / `pool_id_next`, relatórios por `pool_id*` | Mantém o endereço, mas põe a relação **no nome**: coringa por prefixo casa errado (`retencao*` ⊃ `retencao_humano_current`), o escopo ABAC filtra por id exato e perde as linhas do sub-pool **em silêncio**, e toda série histórica muda de nome no dia do corte |

**Decisão: C, com a relação em CAMPOS, não no nome** — que é também a evolução natural do que existe:
o slot já é por pool; ele passa a ter recursos.

---

## A decisão em uma frase

> **O endereço continua sendo o `pool_id`; a unidade de ALOCAÇÃO passa a ser o par `(pool_id, slot)`,
> cada um com instâncias, capacidade e snapshot próprios, e o roteamento escolhe o slot na saída da fila
> por uma regra declarada.**

---

## Decisões

### D1 — Endereço, fila, escopo e relatório não mudam

`pool_id` segue sendo o que webhook, hook, transferência, agenda, campanha, `accessible_pools`, SLA e
dashboards apontam. A fila é do **pool**, não do slot. Nenhum id composto existe em lugar nenhum: o
slot é campo próprio em todo registro que precisa dele.

### D2 — O slot é unidade de alocação, com recursos próprios

`(pool_id, slot)` ganha instâncias (o `instance_bootstrap` reconcilia por slot), capacidade declarada e
o snapshot que já tem. `slot` ∈ {`current`, `next`}; o domínio fica **aberto** para o caso futuro de
alocação por máquina ou carga (`host_a`…), sem mudar o modelo. `previous` segue sendo alvo de rollback,
sem tráfego.

**Pool sem `next` com recurso funciona exatamente como hoje.** Isso cobre os pools humanos: o id da
instância humana é derivado do pool (`human_agent_{pool}`) e não se fragmenta por slot neste ADR.

### D3 — A escolha do slot é do Routing Engine, na saída da fila

Continua valendo *"o Routing Engine é o único árbitro"*. A regra do pool, declarada no registry e
editável na tela, decide o slot: **peso** agora (`{current: 90, next: 10}`), carga ou máquina depois.
Sem regra, 100% `current`. A regra é config do pool, **nunca** env.

### D4 — A sessão fica presa ao slot em que nasceu, e a versão ao pipeline

O slot é sorteado **uma vez**, na primeira alocação da sessão, e gravado no meta da sessão. Retomada
(`suspend`/`collect`/`delegate`/recuperação de queda) e transferência de volta ao mesmo pool voltam ao
mesmo slot. Isso é defesa em profundidade: a garantia de fundo é a **`SFE-03`** (a versão é fixada no
nascimento do pipeline e a retomada executa aquele snapshot), que vale com ou sem este ADR.

### D5 — O segmento carrega `pool_id` + `slot` + `deploy_version`

O slot diz **qual lado da divisão** atendeu; o `deploy_version` (`set_at`) diz **qual código** rodou.
Não são o mesmo fato: depois do promote, `current` roda outra coisa. Relatório por pool soma os slots;
o slot é drill-down, e é nele que mora a comparação entre versões. **Somar slots de pools diferentes
não tem significado** e nenhuma tela o oferece.

### D6 — Promote é mudança de peso seguida de drenagem

`promote` passa o peso para o `next` e deixa o `current` **drenar**: sessões em voo terminam na
versão em que nasceram (D4 + `SFE-03`). Quando o `current` esvazia, os papéis giram
(`next → current → previous`) sem renomear pool. O `SkillDeployment` registra o peso de cada mudança —
a pergunta *"qual config está rodando?"* passa a ter como resposta *"estas duas, com estes pesos, desde
tal momento"*, e essa resposta tem de estar gravada, nunca só em memória.

### D7 — Capacidade é do pool; o slot reparte, não multiplica

A soma declarada dos slots respeita a licença do pool (`lib/capacity.ts`). Um `next` com 10% do peso e
capacidade própria não pode fazer o pool consumir mais licença de IA do que tem.

---

## O que este ADR NÃO decide

- Roteamento por carga ou por máquina — o domínio aberto do slot (D2) o permite; a regra é outra decisão.
- Slots para pools humanos.
- Critério automático de promoção. A simulação com histórico (`RRH-02`) é quem vai propor um portão.

## Consequências

- Lançamento gradual e A/B real sobre tráfego, com comparação por `deploy_version` que já existe.
- O invariante *"O POOL é a unidade endereçável"* **não muda**; ganha um corolário: *a unidade de
  alocação é `(pool, slot)`*.
- Custo: bootstrap por slot, uma regra no roteamento, uma coluna nova em `segments` e no meta da
  sessão, o giro de papéis no promote, e a tela de deploy com peso.

## Fichas

`SLT-01..05` em `pending.md`, sob este ADR. Pré-requisito: `SFE-03`.
