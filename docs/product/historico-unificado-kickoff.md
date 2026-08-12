# Kickoff — Histórico unificado, Fase F0: o gate assimétrico do `collect`

> Cole isto no início da sessão. **Uma sessão = F0.** Não abrir UI nesta sessão.
> Desenho fechado: [`../adr/adr-historico-unificado-duas-visoes.md`](../adr/adr-historico-unificado-duas-visoes.md).
> Item no `TODO.md`: *"Ler um processo = ver seus CONTATOS em sequência, num lugar só"*.

---

## Por que F0 vem antes de tudo

F0 **muda o dado que a tela vai mostrar**. Sem ele, a visão 2 renderiza *parkings* — sessões que
existem só para esperar, sem direção e sem confirmação. Com ele, renderiza *acessos outbound com
confirmação*, que é o que foi pedido.

`collect` já é "output com confirmação": contata o alvo por canal, **cria sessão-filho de contato**,
suspende até resposta ou timeout, e carimba `spawn_reason='collect'`. Ele entrega de uma vez quatro
coisas que estavam sendo tratadas como features separadas:

| O que se queria | O que o `collect` já faz |
|---|---|
| output suspenso até o fim | suspende até resposta ou timeout |
| a perna do output como sessão | cria sessão-filho de contato |
| direção do acesso | `spawn_reason='collect'` = **outbound** |
| pertença ao processo | `origin_session_id` → **proveniência**, sem `journey_merge` |

---

## O que medir ANTES de tocar em qualquer coisa

**O defeito está registrado, não medido por nós.** `skill_limite_entrega_v1.yaml:41-42` diz:

```
# Tem de ser `delegate`, não `collect`: o collect NÃO gera pendência — o engine
# envia customer_resumable/resume_policy mas o endpoint não lê os campos.
```

O `CLAUDE.md` corrobora de lado: a dual-write de `pending_by_customer` foi gated em `handle_delegate`
**e** `handle_delegate_conference`; `handle_collect` não aparece na lista. Mas corroboração não é
medição, e um comentário de YAML pode ter envelhecido.

**Preflight — casar o TOKEN da chamada, não o identificador.** Contar `customer_resumable` em
`webhook.py` devolve número inflado: o comentário que documenta a ausência reescreve a palavra.
Contar a leitura, não a menção:

```bash
DC="docker compose -f docker-compose.demo.yml"
```

```bash
$DC exec -T channel-gateway sh -c "
grep -n 'customer_resumable' \
 /app/src/plughub_channel_gateway/main.py | head -40"
```

**Contador-testemunha ao lado do contador de ausência:** a mesma varredura tem de mostrar as
ocorrências VIVAS em `handle_delegate`/`handle_delegate_conference`. Se aparecerem zero nos três, o
leitor está errado (arquivo/caminho), não o código — **inconclusivo, não "confirmado"**.

**Escreva a previsão antes de rodar**, com o total, não o delta. Três ramos:

> **Previsão já escrita (2026-08-12, sessão de desenho — não rodada).** Espero ocorrências vivas em
> `handle_delegate` **e** `handle_delegate_conference`, e **zero** em `handle_collect`. O total impresso pelo
> `grep` inclui os comentários que documentam a ausência, então o que conta é o **token da leitura**, não a
> menção. Registrada aqui de propósito: previsão que só existe no chat não sobrevive ao `/clear`, e uma
> previsão feita depois do resultado não é previsão.

- ocorrências em `handle_delegate` **e** `handle_delegate_conference`, **zero** em `handle_collect`
  → defeito confirmado, seguir para o trabalho;
- ocorrências nos três → o comentário do YAML envelheceu; **parar e remedir** por que o parking usa
  `delegate` (pode haver segunda causa);
- zero nos três → **inconclusivo**; conferir caminho do arquivo antes de concluir qualquer coisa.

---

## O trabalho

### F0.1 — honrar `customer_resumable` / `resume_policy` em `handle_collect`

Simétrico aos dois handlers de delegate. `PendingEntry.policy` já carrega `offer|auto`; a dual-write
`pending_by_customer` é a mesma. Mudança confinada ao **channel-gateway (Python)** — o schema já tem
os campos em `collect` (`schemas/src/skill.ts`, Slice 3) e o engine já os envia.

> ⚠️ Por isso **não** se aplica aqui a regra *"mudança em `@plughub/schemas` → rebuild de
> agent-registry + skill-flow-service + mcp-server juntos"*. Se você se pegar mexendo no schema,
> parou de fazer F0.

### F0.2 — migrar `skill_limite_entrega_v1.parquear_resultado` de `delegate` para `collect`

**Não é rename.** `delegate` endereça um **pool** (`limite_retorno`); `collect` contata um **alvo por
canal** e a sessão-filho é roteada normalmente. A tradução do endereçamento é trabalho de desenho
dentro de F0, não substituição de uma palavra. Preservar o ramo `encerrar_nao_retirado` (timeout de
7 dias) e o `resume_policy: auto`, que é o que faz o intake distinguir o acesso 3 do acesso 2.

> ⚠️ **Editar o YAML não publica nada.** Skills são seed-if-absent, e o pool roda o snapshot do slot
> `current`. Usar `infra/scripts/deploy_skill_to_slot.sh` — republicar `skill.flow` ou reiniciar o
> bridge é no-op, e o modo de falha é **sucesso pelo caminho antigo**.

---

## Gate de aceite

```bash
bash infra/test/probe_journey_limite.sh          # hoje 5/0
bash infra/test/smoke_limite_tres_acessos.sh     # hoje 16/0
```

Os dois têm de continuar verdes — F0 não pode regredir o cenário.

**A asserção nova de F0** é que o output ativo entra na journey **por proveniência**. Ground truth:

```bash
curl -s "http://localhost:3500/reports/journeys\
?tenant_id=tenant_demo&root_session_id=<RAIZ>"
```

**Previsão a escrever antes de rodar.** Hoje `session_count: 3` (intake, análise, entrega). Depois de
F0 a sessão-filho do `collect` aparece, logo o valor esperado é **4** — e essa contagem é exatamente
a **decisão aberta #1 do ADR**:

> *`collect` que expira sem engajamento conta como contato?* A plataforma emitiu (houve tentativa
> outbound), mas não houve interação com o cliente.

Portanto o veredicto ramifica, e **nenhum ramo é falha automática**:

- **4** → a sessão-filho conta como contato. Decidir explicitamente se fica assim ou se sai da
  contagem pelo precedente `_apply_contact_scope` (que já exclui pools internos) / status `suspended`.
- **3** → a sessão-filho não materializou ou não é contato. Medir **qual dos dois** antes de tratar
  como defeito.
- **5+** → há sessão a mais; provavelmente a antiga volta do cliente continua criando contato próprio
  **além** do filho do collect. É o caso que mais interessa entender, porque decide o escopo de F1.

Decidir isto **fecha a decisão aberta #1** e é entregável de F0 tanto quanto o código.

---

## Armadilhas de ambiente que mordem nesta tarefa

- **`e2e` apaga `tenant_demo:*` e `session:*` antes de CADA cenário** (`lib/redis-client.ts` §40).
  Contra o demo leva junto instâncias logadas, snapshots, ContextStore, claims, tokens de resume e
  sessões vivas. Não rodar e2e no meio de uma medição de journey.
- **`up -d <serviço>` sobe só o subgrafo**; o tell de que faltou algo é `kafka-init Exited`.
- **`up -d --force-recreate` apaga o `pip install`** do pytest — reinstalar antes de medir.
- **`docker cp` sobrevive a `restart`, não a `up -d`.** Mudança de código de serviço = `build`.
- Arquivo **novo** só entra na imagem com `build --no-cache`; edição de existente entra normal.
- Portas do host: analytics-api **3500** · channel-gateway **8010** · agent-registry **3300** ·
  auth-api **3202** (3200 é o ai-gateway) · redis **6380** · postgres **5433**.
- ClickHouse db = **`plughub_demo`** (`analytics` só existe nos testes); agent-registry em
  **`plughub_registry`** (`plughub_demo` tem um `pools` FÓSSIL que devolve retrato velho).

---

## Fora de escopo nesta sessão

- Qualquer UI (F3/F4). O ADR é explícito: não começar pela tela.
- `journey_merge` no intake (F1). ~~e o carimbo de endereço de entrada~~ — **o carimbo de endereço saiu do
  arco em 2026-08-12** (ADR D12b): endpoint→pool é 1:1, então o pool o substitui sem perda medida. No lugar
  dele entrou **F1b** (`entrou por`: first-write-wins em `sessions.pool_id`), que também não é desta sessão.
- `root_session_id` em `/reports/segments` (F2).
- `ContextStorePersister` (F5).
- Lente de faixas por personagem (destino registrado).
- **Auditoria LGPD.** Os achados 9/10/11 do ADR (tabelas `mcp_audit_log`/`audit_access_log` e o gate
  `_require_audit_access` documentados e **ausentes**; `mcp.audit` sem produtor vivo) são item
  próprio e não pertencem a este arco.
