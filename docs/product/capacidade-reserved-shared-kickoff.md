# Kickoff — capacidade de IA `reserved` × `shared`

> **Para:** sessão nova (abrir com **Opus**, contexto limpo).
> **Decisão já tomada:** [`adr-pool-capacity-reserved-shared.md`](../adr/adr-pool-capacity-reserved-shared.md)
> e [`adr-pool-no-resource-policy.md`](../adr/adr-pool-no-resource-policy.md).
> **Este arquivo não repete os ADRs.** Ele registra o que foi **medido**, o que é **inferência**,
> a ordem das fases (que inverte o que parece natural) e as armadilhas de ambiente.
> **Data da medição:** 2026-08-18, `tenant_demo`.

---

## 1. O problema, em três linhas

Capacidade de IA hoje é híbrida e as duas metades não se falam: o **provisionamento** é reservado
(N instâncias criadas por pool, exclusivas dele) e a **admissão** é compartilhada (um SET de
tenant, ordem de chegada). Resultado: um pool pode esgotar o direito de admissão de outro e ser
recusado na porta **com instância própria ociosa**. E o modo estatístico não é configurável,
porque sobre-assinar é justamente o que `deployViolation` proíbe.

---

## 2. O que foi MEDIDO — não re-derivar, re-verificar se desconfiar

| # | Medida | Resultado |
|---|---|---|
| M1 | Σ `max_concurrent_sessions` nos slots `current` de pools de IA | **329** em **30 pools** |
| M1b | pools humanos com slot de deploy | **0** |
| M2 | `SCARD tenant_demo:admission:kind:ai` | **1** (tracking vivo) |
| M3 | `quota:capacity:ai_agent` / `:human_agent` / `:max_concurrent_sessions` | **as três `nil`** |
| M4 | `checkConcurrentSessions` / `{t}:quota:concurrent_sessions` | órfã (0 call sites) / `EXISTS = 0` |
| M5 | `pools.max_concurrent_sessions` × `slot.config_json` | **0 linhas divergentes** ⇒ populados e espelhados |

**A consequência que muda a natureza do arco:** com as três quotas `nil`, `_type_limit()` devolve
`None` e `contractedCapacity()` devolve `null` — **admissão, provisionamento e login estão todos
fail-open.** Nada está sendo imposto neste ambiente.

> ⇒ **É construção, não conserto.** Não há comportamento em vigor a regredir. O defeito é
> **latente**, e **329** é o número que colide no dia em que o pricing for ligado.

Para re-medir (comandos completos no §6):

```bash
docker compose -f docker-compose.demo.yml exec -T redis redis-cli \
  MGET tenant_demo:quota:capacity:ai_agent tenant_demo:quota:capacity:human_agent
```

---

## 3. O que é INFERÊNCIA, não medição

Ler esta seção antes de agir sobre qualquer item dela. São afirmações que **parecem medidas** nos
ADRs mas não são.

1. **Que a coluna `pools.max_concurrent_sessions` seja resíduo do modelo pré-slot.** O que se sabe:
   `deployViolation` lê o **slot** (`slotDeclared`), e o docstring do bootstrap
   (`instance_bootstrap.py:1092`) diz que a autoridade é o slot. O que **não** se sabe: quem
   escreve a coluna. **Falta ler a rota do agent-registry que compõe
   `deployed_max_concurrent_sessions`.** É a primeira tarefa da fase P2 e o primeiro trabalho
   concreto deste arco.
2. **Que `checkConcurrentSessions` nunca tenha rodado.** Medido: zero call sites *hoje*, chave
   inexistente *hoje*. Não medido: se já teve consumidor e o caminho foi removido pela metade.
   Irrelevante para a decisão, relevante se aparecer dado velho na chave.
3. **Que `C_ai` do contrato seja ~360.** Vem de um número citado no `CLAUDE.md` (*"360 IA + 10
   humanos = 370"*), **não** de leitura de `pricing.installation_resources`. Não usar para
   dimensionar. Medir antes de ativar (§5, risco 1).

---

## 4. A ordem das fases inverte o que parece natural

**P1 vem antes de P0.** Não é engano.

- **P1** = `deployViolation` passa a ler `{t}:quota:capacity:ai_agent` em vez de
  `{t}:quota:max_concurrent_sessions` (`capacity.ts:27`) e a somar só pools de IA. **Uma linha e
  um filtro.**
- **P0** = ligar o pricing (popular `pricing.installation_resources`, confirmar que `sync_tenant`
  grava as três chaves).

Enquanto tudo é fail-open, o P1 é **inócuo** — nenhum comportamento muda, porque a comparação nem
acontece. Depois do P0, a mesma linha é **mudança de comportamento em produção**: deploys que
passavam começam a receber 422.

> **Corolário:** todo conserto do defeito C deve entrar antes de as quotas existirem. A janela é
> agora e ela fecha no P0.

Sequência completa no ADR §5 (P0…P6). O **maior risco do arco não é técnico**: é o P0 nunca
acontecer, as fases P3–P5 serem entregues, as quotas seguirem `nil`, e a suíte ficar verde sem
provar nada — o teste que não pode reprovar.

---

## 5. Três armadilhas de ambiente que já custaram round-trip

1. **`docker-compose.demo.yml` está na RAIZ do repo**, não em `infra/`. Serviços: `redis` e
   `postgres` (user `plughub`).
2. **O banco do registry é `plughub_registry`.** O `plughub_demo` tem uma tabela `pools`
   **fóssil**, com as mesmas colunas — consultá-la devolve retrato velho que passa por medição.
   Tabelas em snake_case no schema `public` (`pools`, `pool_skill_slots`), apesar do Prisma.
3. **`infra/registry/*.yaml` NÃO é a fonte do número.** Os YAML não declaram
   `max_concurrent_sessions` em lugar nenhum (ausente ⇒ default 1, `registry_syncer.py:496`), o
   que daria Σ ≈ 30. O vivo é **329** — os máximos foram definidos **pelo deploy de cada pool**.
   **Erro de 11× para quem dimensionar pelo arquivo.** Fonte é `pool_skill_slots.config_json`.

---

## 6. Comandos de medição, prontos

```bash
cd /home/a1/projects/plughub

# quotas por tipo — se nil, NENHUM gate está armado
docker compose -f docker-compose.demo.yml exec -T redis redis-cli \
  MGET tenant_demo:quota:capacity:ai_agent \
       tenant_demo:quota:capacity:human_agent \
       tenant_demo:quota:max_concurrent_sessions

# ocupação de IA
docker compose -f docker-compose.demo.yml exec -T redis redis-cli \
  SCARD tenant_demo:admission:kind:ai

# Σ declarada por tipo de pool (o número que colide com C_ai)
docker compose -f docker-compose.demo.yml exec -T postgres \
  psql -U plughub -d plughub_registry -c "
SELECT COALESCE(p.agent_kind,'(sem kind)') AS kind,
       COUNT(*) AS pools_com_slot,
       SUM(COALESCE(NULLIF(s.config_json->>'max_concurrent_sessions','')::int, 1)) AS soma_declarada
FROM public.pool_skill_slots s
LEFT JOIN public.pools p ON p.pool_id = s.pool_id AND p.tenant_id = s.tenant_id
WHERE s.tenant_id='tenant_demo' AND s.slot='current' AND s.skill_id IS NOT NULL
GROUP BY 1 ORDER BY 1;"
```

O `COALESCE(..., 1)` é deliberado: slot sem a chave vale **1**, não zero — contar zero encolhe a
soma e faz a comparação com `C_ai` mentir para o lado tranquilizador.

---

## 7. Primeira tarefa concreta

**Ler a rota do agent-registry que compõe `deployed_max_concurrent_sessions`** e responder: quem
escreve `pools.max_concurrent_sessions`? Três desfechos, três caminhos:

| Achado | Ação |
|---|---|
| Ninguém escreve (só leitura legada) | coluna sai na P2; slot é fonte única |
| O mesmo write path escreve os dois | derivar a coluna do slot, ou removê-la |
| Escritores independentes | é violação de one-source ativa, e vira item próprio **antes** da P3 |

Sem isso, o campo `capacity_mode` da P3 nasce ao lado de uma duplicação que ele vai herdar.

---

## 8. O que NÃO fazer

- **Não reviver** `pools.session_reservation`, `{t}:admission:shared`,
  `{t}:admission:reserved:{pool}`, `{t}:admission:member:{sid}`. A reserva **é o
  provisionamento** — as instâncias já criadas. Sem balde novo, sem aritmética nova em Redis.
- **Não criar simetria humano/IA.** Humano é licença por **login**, instância por **usuário**,
  sem provisionamento. `capacity_mode` em pool humano deve ser **rejeitado no registro**, não
  ignorado — ignorar ensina que existe.
- **Não somar o que não soma** na tela da P5: `Σ available(pool)` conta o mesmo recurso uma vez
  por pool, e `by_channel` é projeção, não partição.
- **Não dimensionar pelo YAML** (§5.3).
- **Não ligar o pricing com `C_ai < 329`** sem antes conferir a Σ declarada — o gate liga já
  recusando, em pools com instância ociosa.

---

## 9. Referências

- ADRs: [`adr-pool-capacity-reserved-shared.md`](../adr/adr-pool-capacity-reserved-shared.md) ·
  [`adr-pool-no-resource-policy.md`](../adr/adr-pool-no-resource-policy.md)
- Código central: `routing-engine/…/admission.py` · `routing-engine/…/main.py:256-295` ·
  `agent-registry/src/lib/capacity.ts` · `pricing-api/…/quota_sync.py` ·
  `orchestrator-bridge/…/instance_bootstrap.py:1085-1145` ·
  `mcp-server-plughub/src/server.ts:356-380`
- Contexto de direção: [`n8n-arco-abortado-2026-08-18.md`](n8n-arco-abortado-2026-08-18.md)
- `CLAUDE.md` § "Admissão de sessão — UM gate, na moeda certa" (lista de *não reviver*)
