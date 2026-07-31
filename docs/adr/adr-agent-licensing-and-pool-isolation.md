# ADR — Licenciamento de agentes e isolamento entre pools

**Status:** proposto (2026-07-31) — modelo, D9 (partição por pool) e D10 (licenças materializadas)
acordados; D6 revogada; pendente revisão final
**Relacionado:** `docs/product/shared-capacity-pool-as-tag-design.md` (arco de *relatar* capacidade) ·
arco separado da costura `acquire/release` (a criar) · `docs/arcos/queue-attended-model.md`

---

## 1. Contexto

### 1.1 O fóssil do tronco

Na telefonia, o tronco é admissão **física**: limita a demanda que entra e, de quebra, protege todos
os serviços — ninguém precisa desenhar isolamento porque o cabo o impõe. Canal digital (WhatsApp,
webchat, SMS) remove o cabo: a demanda passa a ser ilimitada, mas o recurso escasso não desapareceu —
**mudou de lugar**, do canal para o agente e para o que está atrás dele.

A plataforma recriou a admissão artificialmente, e corretamente. O que sobreviveu à mudança foi o
**formato** do modelo antigo: `{tenant}:quota:max_concurrent_sessions` é um nome com forma de tronco
("N canais simultâneos") aplicado a uma grandeza com forma de agente. Toda a cadeia de defeitos abaixo
decorre disso — não de descuido, mas de um modelo mental que sobreviveu ao seu objeto.

### 1.2 O que existe hoje (medido/lido no código, 2026-07-31)

| Camada | Unidade real | Onde | Estado |
|---|---|---|---|
| Licença humana | logins concorrentes | `registerHumanAgent ≤ C_human` | **correta** |
| Licença IA | — | `admission._type_limit('ai_agent')` gateia **sessões** | unidade errada |
| Teto misto | — | `C = ai_agent + human_agent` → gateia **sessões** de qualquer pool | errado (§1.3) |
| Reserva/shared | sessões | `pool.session_reservation`, `{t}:admission:reserved:{pool}` / `:shared` | unidade errada |
| Semáforo do recurso | sessões por instância | `{t}:instance:{iid}:sessions` (Lua atômica) | **correto** |
| Projeção (snapshot) | — | `active_count` → `available` | quebrado — tratado no doc de produto |

`quota_sync.py` descreve `C` com as próprias palavras: *"capacidade contratada de **agentes**
(ai_agent + human_agent)"*. É gravado numa chave cujo nome diz **sessões** e consumido por
`AdmissionController._shared_limit()`, que o compara com `SCARD` de um SET de `session_id`.

### 1.3 Os três erros empilhados no balde compartilhado

1. **Funde licenças não-fungíveis** — sessão de IA consome headroom que é do humano e vice-versa.
2. **Gateia sessão humana** — o limite humano já foi cobrado no login; cobrá-lo de novo por sessão é
   dupla cobrança em moeda errada.
3. **Mede em sessões o que foi contratado em instâncias** — coincidem só quando todo agente tem
   `max_concurrent = 1`. É configurável por deploy (campo "Concurrent sessions" da tela).

Efeito prático: 10 licenças humanas (`max_concurrent 3`) + 10 de IA ⇒ `C = 20` gastos em sessões,
quando só os humanos serviriam 30. A porta fecha em 20 e devolve `cause="shared_full"` → outage.
**Rejeita contato real com capacidade ociosa.** É defeito de ALOCAÇÃO, não de relatório.

### 1.4 Por que a partição precisa existir

Sem tronco, nada impede um serviço de consumir tudo o que foi planejado para os outros. O limite
existe para que nenhum serviço degrade o ambiente dos demais. Como a capacidade **não** é fragmentada
fisicamente (invariante do `CLAUDE.md`: *capacidade é do RECURSO e não fragmenta por pool*), a
partição é **contábil**. Consequência que justifica todo o esforço deste arco:

> Com partição física, medir mal é tolerável — o hardware ainda segura.
> Com partição contábil, **a medição é a imposição**. Um `available` que mente não é relatório errado:
> é um limite que não existe.

---

## 2. Decisões

### D1 — Licença é o direito de instanciar um recurso; há duas, não-fungíveis

`ai_agent` e `human_agent` são moedas distintas. Nunca somadas, nunca substituíveis. O total misto
deixa de ser um gate.

### D2 — Licença humana é cobrada na CONEXÃO ao pool

O login pode falhar por falta de licença humana (`registerHumanAgent ≤ C_human` — já é assim).
**Sessão humana não é gateada por licença.** O teto de sessões humanas é **consequência**
(`Σ max_concurrent` dos conectados), nunca contrato. Pool humano não participa de reserva/shared.

### D3 — Licença de IA é cobrada na instanciação, e instanciação é observável

Instância não é pré-criada como recurso em execução: o registro que o Bootstrap mantém é *instância
possível*. Logo **licença ocupada ⟺ instância com `SCARD(sessions) ≥ 1`** — derivada do mesmo SET,
nunca contador paralelo.

Cobrança na transição **0 → 1** de ocupação, devolução no retorno a **0**. A transição é detectável
atomicamente: `claim_instance` já devolve a nova ocupação, e `1` significa "acabou de começar a rodar".
O portão de licença cabe **dentro do passo atômico que já existe**.

### D4 — Piso e teto são números SEPARADOS

O `session_reservation` atual é "cap AND guarantee" — um número servindo a dois propósitos opostos.
Separar:

| `license_floor` | `license_ceiling` | Regime | Uso |
|---|---|---|---|
| 0 | C | melhor esforço | pool sem prioridade nem risco |
| 0 | Y | **contenção sem garantia** | "não fuja com tudo" — não existe hoje |
| X | X | partição rígida | equivalente ao `session_reservation` atual |
| X | Y > X | **garantido com burst** | não existe hoje |

Invariante de configuração: **`Σ license_floor ≤ C_ai`** (garantia insatisfazível se violar — o
agent-registry já valida o análogo e devolve 422). Tetos **podem** somar mais que `C_ai`: é o que
permite compartilhar.

**Naturezas — e a condição que as separa.** Piso = garantia = **sempre determinístico** (retido, D10).
Teto = **determinístico ou probabilístico conforme a sobre-subscrição**:

| Regime | Condição | Teto é |
|---|---|---|
| sub-subscrito | `Σ license_ceiling ≤ C_ai` | **determinístico** — a soma dos apetites cabe no pote, não há disputa possível, cada pool sempre alcança o próprio teto |
| sobre-subscrito | `Σ license_ceiling > C_ai` | **probabilístico** — por ordem de chegada, dentro do limite |

Corolário que responde "quando preciso de piso?": **o piso existe para dar determinismo sob
sobre-subscrição.** Instalação que não sobre-subscreve não precisa de piso — `license_floor = 0` em
todos os pools já dá divisão determinística, e **sem retenção**: as licenças ficam no pote e só saem
quando usadas.

Corolário 2: `floor = 0` com `Σ ceiling ≤ C_ai` é **estritamente melhor** que a partição rígida
`floor = ceiling = X` — mesma garantia, mesmo limite, zero objeto pré-particionado. A linha "partição
rígida" da tabela acima é obtenível só com tetos.

Economia do modelo: sobre-subscrever é o que permite **comprar menos licenças que a soma dos picos**.
Quem sobre-subscreve precisa de piso para proteger o serviço prioritário; quem não sobre-subscreve tem
determinismo e nenhuma ociosidade, de graça. Não é trade-off escondido — é decisão de dimensionamento.

**`ceiling − floor` é a parcela de empréstimo**: quanto o pool pode tomar do compartilhado além da
própria garantia.

**Risco operacional — `Σ ceiling ≤ C_ai` é propriedade GLOBAL.** Adicionar um pool novo com qualquer
teto pode virar a instalação inteira de determinística para disputada, em silêncio, sem tocar em
nenhum pool existente. Exige indicador de sobre-subscrição (`Σ license_ceiling − C_ai`) visível na
configuração. **Reportar, não validar** — bloquear sobre-subscrição mataria a razão econômica dela
existir.

O teto é também o que torna `license_floor = 0` uma configuração viável no regime sobre-subscrito: um
pool sem garantia própria só está protegido porque os outros estão limitados — sem tetos, o balde
compartilhado seria winner-take-all e quem abriu mão da garantia ficaria à mercê de quem chegar
primeiro.

**O teto limita, não ordena.** Se dois pools disputam a última licença compartilhada, o teto não diz
quem ganha — diz até onde cada um pode ir. Ordenação real (A vence C no empate) seria um **terceiro**
mecanismo, na aquisição, e deve permanecer separado. Não sobrecarregar o teto com ela.

### D5 — Regra de aquisição *(semântica; implementação em D10)*

Na transição 0→1 para o pool `P`, na ordem:

```
1. tem livre no conjunto do PRÓPRIO piso de P   E  used_P < license_ceiling_P  → concede
2. senão, tem livre no conjunto COMPARTILHADO   E  used_P < license_ceiling_P  → concede
3. senão                                                                       → nega (D7)
```

Um pool com piso livre **nunca** pode ser negado. O piso alheio é indisponível **estruturalmente**
(D10) — não há preempção nem espera.

> A formulação original desta ADR calculava `withheld = Σ max(0, floor_Q − used_Q)` para descontar as
> garantias alheias do total livre. Aritmética correta, mas **só necessária porque o piso era
> virtual**. Com as licenças materializadas (D10) o cálculo desaparece: o que está no conjunto de
> outro dono simplesmente não é alcançável.

### D6 — ~~Empréstimo com devolução preguiçosa~~ **REVOGADA** por D10 (2026-07-31)

A decisão original era emprestar o piso ocioso e devolvê-lo por término natural de sessão, aceitando
que a garantia chegasse com atraso de um handle time — argumento de **magnitude** ("desprezível para
IA").

O argumento correto é **categórico**: garantia que exige espera não é garantia, é outra coisa. Se A
precisa aguardar a sessão de terceiro terminar para exercer o próprio piso, o piso virou
probabilístico — e deixou de servir ao propósito pelo qual existe. A revogação troca a otimização de
uma métrica (utilização) pela preservação da propriedade que o mecanismo existe para dar
(determinismo).

O custo real do empréstimo — ociosidade do piso — não desaparece: passa a ser **escolha explícita do
operador**, por pool. Quem não quer pagá-lo configura `license_floor = 0` e vive do compartilhado.
Trade-off exposto na configuração, não escondido numa política de plataforma que enfraquece toda
garantia do sistema de uma vez.

Registrada em vez de apagada porque a versão errada é convincente: ela otimiza um número real e o
custo que ela cria não aparece em nenhuma métrica de utilização.

### D7 — Duas negações, dois escapes

| Condição | Significado | Escape |
|---|---|---|
| `used_P ≥ license_ceiling_P` | o serviço bateu no **próprio plano** (ambiente pode estar vazio) | fila do próprio pool + alerta; conversa é "revisar o plano de P" |
| `free − withheld ≤ 0` | o ambiente não tem folga **não prometida** | fila + oferta de troca de canal; conversa é "instalação no limite" |

Hoje os dois viram `shared_full`. Motivos distintos ⇒ donos distintos ⇒ ações distintas.

**Em canal digital a porta estaciona, não rejeita.** Sem tronco não há sinal de ocupado: o contato
rejeitado repete, e a repetição vira demanda nova — rejeitar aumenta a carga e transfere a degradação
para o cliente do próprio serviço. A fila muda (`{t}:queue:unadmitted`, isenta de `C` enquanto espera)
já é o desenho certo; esta ADR não o altera, apenas o torna a saída padrão dos dois casos acima.

### D8 — Política de falha explícita

**Porta de entrada falha FECHADA; meio de sessão falha ABERTO** (nunca derrubar sessão viva). Hoje
isso existe distribuído em dois `if` e um comentário; passa a ser regra nomeada. Erro de infra ao
consultar a licença degrada ABERTO **com WARNING** — dar licença de graça é preferível a outage, mas
nunca em silêncio.

### D10 — Licenças MATERIALIZADAS: um objeto por licença *(supersede D5-aritmético e D6)*

Em vez de um teto conferido por aritmética, **um objeto por licença contratada**, materializado no
boot a partir de `C_ai`. Contagem deixa de existir: há N objetos, cada um livre ou tomado.

O que isso muda de natureza:

- **contagem deixa de exigir contador** — `SCARD` do conjunto, derivada, coerente com o invariante 4.
  (Não é que a aritmética não pudesse ser correta: dentro do Lua atômico uma checagem *é* barreira. O
  ganho é não ter um número mantido, que foi o que derivou em todos os pontos deste arco — ver §10.10);
- **estado inspecionável** — dá para olhar e ver quem está com o quê, sem instrumentação;
- "com quem está" responde à atribuição (§3) sem bookkeeping novo;
- `Σ license_floor ≤ C_ai` é imposto pela **existência** dos objetos: piso que não pode ser
  materializado no boot é erro alto, não privação descoberta em runtime;
- o teto vira consequência — é o que sobra depois de materializados os pisos.

**Piso = dono, retido.** As licenças do piso de `P` ficam no conjunto de `P` e **não** são
emprestáveis (D6). O determinismo deixa de ser regra a verificar e passa a ser propriedade física.

**Codificação:**

```
{t}:license:ai:pool:{P}:free   SET   — livres do piso de P (dono)
{t}:license:ai:shared:free     SET   — livres sem dono (excedente = C_ai − Σ floor)
{t}:license:ai:held            HASH  — license_id → "{instance}::{pool}::{expires_at_ms}"
```

`SCARD` de cada conjunto livre responde direto "quantas sobram e de quem".

**Co-commit obrigatório.** A tomada da licença roda **dentro do mesmo passo atômico** da transição
0→1 (o Lua do `claim_instance`). Em passos separados, `held` e `SCARD(sessions) ≥ 1` divergem no
primeiro crash — e teríamos construído a **quarta** representação da ocupação, a exata classe de
defeito que originou este arco. Co-commitados, o objeto não é verdade paralela: é a mesma verdade com
outro índice.

**Vazamento é o custo da materialização.** Objeto tomado não se auto-cura — o contador recontava do
`SCARD`. Exige a mesma disciplina do hold de wrap-up: `expires_at_ms` no valor, expirados descartados
em qualquer aquisição. Padrão já existente no código, não invenção.

**Contrato que encolhe.** Licença a retirar que esteja tomada não é arrancada: marcada para
aposentadoria, some na devolução.

**Pool humano não tem objeto-licença.** Pelo D2 a licença humana é cobrada na conexão. Piso/teto de
pool humano — inclusive os espelhos `-int` — são **N/A, não 0**. `0` diria "explicitamente nenhuma" e,
se algum dia o teto for aplicado a pool humano, `ceiling = 0` bloquearia todo atendimento. Zero e
não-aplicável são fatos diferentes, e confundi-los é a mesma armadilha do valor plausível que produziu
o `available_agents` (doc de produto §3.1).

**A licença é o direito; a instância é o exercício.** Materializar no boot **não** pré-instancia
agente: `poolA` segurar 3 licenças ociosas significa que ele *pode* instanciar 3 quando precisar — não
que existam 3 agentes parados.

---

## 3. Mensuração — "degradação" não se mede, decompõe-se

Uma métrica única de degradação seria o valor plausível que esconde tudo. Três medidas, todas
derivadas do caminho de aquisição:

1. **Privação** — `negado(P) ∧ used_P < license_floor_P`. Violação de garantia: booleano por evento,
   contável em ocorrências e em segundos. Se > 0 alguma vez, o contrato quebrou — é incidente, não
   percepção.
2. **Consequência ao cliente** — espera e abandono por pool (já existem e são sãos: abas Fila/SLA).
   Degradação = espera/abandono subindo **sem** a demanda do próprio pool ter subido.
3. **Atribuição** — `negado(A) ∧ used_C > license_floor_C` é a forma mensurável de "vizinho
   barulhento". Exige atribuição por pool do recurso compartilhado: a tag do semáforo (doc de produto
   §1) e o `shared_pools` da admissão.

Escada de alerta, com predicados em vez de julgamento:

| Predicado | Nível |
|---|---|
| `used_P > license_floor_P` | usando excedente — informativo |
| `used_P ≥ license_ceiling_P` | no limite do plano — alerta |
| `negado(P) ∧ used_P < license_floor_P` | garantia rompida — incidente |

---

## 4. Invariantes

1. Licença de IA e de humano **nunca** se somam nem se substituem.
2. Licença humana é cobrada na conexão; **sessão humana não é gateada por licença**. O teto de sessões
   humanas é consequência (`Σ max_concurrent` dos conectados), nunca contrato.
3. Licença de IA é cobrada na instanciação (0→1) e devolvida no retorno a 0; reserva/shared partem de
   `C_ai` e só de `C_ai`.
4. **Licença ocupada ⟺ `SCARD(sessions) ≥ 1`** — derivada, nunca contador paralelo.
5. `Σ license_floor ≤ C_ai`, **validado na escrita da config** (§9.3), nunca na aquisição;
   `Σ license_ceiling` pode exceder `C_ai`.
5b. Relatar capacidade e debitar licença são caminhos distintos: um pool pode **anunciar** um recurso
   compartilhado sem que isso constitua uma segunda licença (§9.2).
6. Capacidade **não** fragmenta fisicamente; a partição é contábil — logo a medição é a imposição.
7. Nenhuma negação é silenciosa: motivo nomeado (D7) e logado.
8. **A licença é o direito; a instância é o exercício.** Materializar licença não pré-instancia agente.
9. Piso é **sempre determinístico** (retido, nunca emprestado). Pool com piso livre nunca pode ser
   negado. O teto é determinístico sob `Σ ceiling ≤ C_ai` e probabilístico acima disso — logo **o piso
   só é necessário em instalação sobre-subscrita** (D4).

---

## 5. Consequências

### 5.1 Simplificação da admissão

Se sessão humana sai dos baldes (D2), `{t}:admission:shared` e `:reserved:{pool}` passam a conter **só
IA**. O SET `{t}:admission:kind:ai` — que existe justamente para reconstituir "quais destas são IA" —
torna-se redundante. Três estruturas paralelas (`member`, `kind`, `shared_pools`) colapsam em duas, e
a invariante `Σ pisos ≤ C` passa a comparar IA com IA.

### 5.2 Migração de campos e chaves

| Hoje | Alvo |
|---|---|
| `pool.session_reservation` — coluna **quente** (Prisma, `schemas/agent-registry.ts`, UI) | `license_floor` + `license_ceiling` **no slot de deploy** (§10.5); migração inicial `floor = ceiling = session_reservation`. **Não é renomeação: é mudança de governança** (quente → cerimônia agendável). Sem isso escrito, alguém reexpõe o campo na edição do pool |
| `{t}:quota:max_concurrent_sessions` (misto) | deixa de ser gate; `quota_sync` escreve só `capacity:ai_agent` e `capacity:human_agent` |
| `admission._shared_limit` = `C − Σ reservas` | `C_ai − Σ license_floor`, aplicado só a pool de IA |
| — (não existe) | objetos-licença (D10): `{t}:license:ai:pool:{P}:free`, `:shared:free`, `:held` |

**Consumidor a migrar (verificado, chamadores não rastreados):** `mcp-server-plughub/src/lib/quota-check.ts`
`checkConcurrentSessions()` lê a chave mista. Precisa decidir se vira gate de IA, some, ou passa a ler
o teto derivado.

### 5.3 Impacto no arco de relatório

- O rollup por tenant passa a ser **por tipo de licença** — disponibilidade de IA e de humano não são
  somáveis (moedas diferentes). Um KPI único repetiria a falácia de aditividade um nível acima.
- Pool humano: `available = Σ max_concurrent (conectados) − ocupação`. Sem teto de licença.
- Pool de IA: `available = min(folga de licença × max_concurrent, capacidade provisionada − ocupação)`.
  O `total_capacity` do doc de produto soma sobre instâncias **provisionadas** — capacidade *possível*,
  não licenciada. Precisa do segundo teto.
- Divisão de trabalho: **a tag serve o humano** (multi-pool, capacidade compartilhada); **a licença
  serve a IA** (reserva/shared). Não são duas soluções para o mesmo problema.

### 5.4 Fora de escopo desta ADR

Licença de agente é **um** dos recursos compartilhados. Um pool de IA dentro da sua licença ainda pode
degradar os outros saturando uma **conta LLM** compartilhada — `Pool.llm_account_ids` já é uma segunda
partição, com a mesma intenção e nenhuma relação com licença. Além disso, `voice`/`webrtc` têm
concorrência **fisicamente real** (tronco, capacidade do SFU): ali um limite por canal não é simulado.
Um modelo de isolamento completo precisa enumerar quais recursos são particionados; esta ADR cobre só
o de agentes.

---

## 6. Alternativas rejeitadas

| Alternativa | Por que não |
|---|---|
| Manter piso = teto (`session_reservation` como está) | encalha licença ociosa — estranha justamente o recurso que a reserva existe para otimizar |
| Só piso, sem teto | foi a proposta inicial deste autor e **está errada**: sem teto um pool sem garantia consome todo o não-prometido e não há limite a impor. O dano estava na *fusão*, não no teto |
| Só teto, sem piso (status quo dos pools shared) | contém custo, **não** isola: limitar o barulhento só protege o vizinho se a capacidade estiver particionada; senão o que sobra segue em disputa |
| **Empréstimo do piso ocioso** (D6, revogado) | garantia que exige espera não é garantia. Trocava a propriedade que o mecanismo existe para dar por utilização. A ociosidade do piso é o preço da garantia e agora é escolha explícita do operador (`floor = 0`), não política de plataforma |
| Teto conferido por aritmética (`free − withheld`) | correto, mas só necessário porque o piso era virtual; materializado (D10), o cálculo desaparece |
| Preempção para reclamar piso | derruba sessão viva — inaceitável |
| Rejeitar na porta em canal digital | sem sinal de ocupado, o cliente repete: rejeição aumenta a carga e move a degradação de vítima |
| Reservar vagas de sessão por pool (fragmentar o recurso) | contraria o invariante "capacidade é do recurso"; já rejeitada no TODO |
| Uma métrica única de "degradação" | valor plausível que esconde privação, espera e atribuição — as três coisas que importam |

---

## 7. Fases sugeridas

Cada fase com uma verificação que **precisa poder ficar vermelha**.

**L1 — separar as moedas.** `quota_sync` para de escrever a chave mista; `admission` deixa de gatear
sessão humana; gate de IA passa a ler `capacity:ai_agent`.
*Verificação:* tenant com 10 humanos (`max_concurrent 3`) + 10 IA admite a 21ª sessão humana. Hoje
rejeita com `shared_full` — o teste nasce vermelho.

**L2 — unidade de licença = instância.** Contagem passa a ser `#{instâncias com SCARD ≥ 1}` por pool;
cobrança na transição 0→1 dentro do `claim_instance`.
*Verificação:* 1 licença de IA com `deployed_max_concurrent_sessions = 3` serve 3 sessões. Hoje serve 1.

**L3 — licenças materializadas.** Objetos criados no boot a partir de `C_ai`; campos
`license_floor`/`license_ceiling` (migração `floor = ceiling = session_reservation`); conjuntos por
dono + compartilhado; tomada co-commitada ao 0→1; expiração; negações nomeadas (D7).
*Verificação:* (a) pool com piso livre **nunca** é negado, nem com o compartilhado vazio — se for, o
determinismo quebrou; (b) pool com `floor = 0` é negado quando o compartilhado esvazia, **mesmo
havendo licença livre no conjunto de outro dono** — se passar, o piso alheio não está retido;
(c) matar o processo entre a licença e o claim não pode deixar `held` sem sessão correspondente.

**L4 — mensuração.** Contadores de §3 + escada de alerta.
*Verificação:* provocar privação num ambiente controlado e ver `starvation` > 0; provocar teto e ver
alerta sem privação. Se os dois acenderem juntos, a decomposição está errada.

**L5 — UI e limpeza.** Piso/teto na tela de **deploy** do pool (não na edição quente); por pool
`materializado`/`pendente`; indicador global de sobre-subscrição `Σ ceiling − C_ai` e `Σ floor` contra
`C_ai` (§10.8); aviso na redução de contrato no pricing (§10.6); remover `kind:ai` se §5.1 se
confirmar; decidir o destino de `checkConcurrentSessions`.
*Verificação:* criar um pool novo com teto qualquer e ver o indicador global virar o regime de
determinístico para sobre-subscrito — se a tela não mudar, a propriedade global não está sendo
calculada.

---

## 8. Relação com os outros arcos

- **Relatar capacidade** (`shared-capacity-pool-as-tag-design.md`): consome esta ADR em §5.3 — e é
  **pré-requisito** dela na direção inversa. A reconstrução do estado de licença (§10.3) precisa saber
  a que pool cada instância em execução está atribuída, e isso é a **tag de pool no occupant** (§1
  daquele documento). Existe alternativa (`{t}:session:{sid}:serving_pool`, escrita pelo `mark_busy`),
  mas custa N lookups e tem TTL de 24 h. **A tag é a via limpa, e a L3 depende dela.** Correção de uma
  afirmação anterior desta ADR de que os dois arcos eram totalmente paralelos (§10.10).
- **Costura `acquire/release`** (arco separado, a criar): é onde D3/D5/D7 vivem fisicamente. Os três
  portões (licença, admissão, semáforo) passam a compor num caminho só, com uma taxonomia de falha.
  Push e pull passam a diferir apenas em **quem escolhe o recurso**.

---

## 9. D9 — A partição é por POOL *(decidido 2026-07-31)*

O balde de D4/D5 é chaveado por `pool_id`. Sem entidade de classe.

A objeção levantada contra "por pool" era dívida de configuração sob criação automática de pool: o
espelho `-int` é auto-provisionado (ADR author-bound) e a linha de base mediu **3 pools para 1
recurso**. A objeção **cai**, por dois motivos independentes:

1. **Pools auto-criados são internos e humanos.** Pelo D2, pool humano não consome licença por sessão —
   foi cobrada no login. Não há piso/teto a configurar num pool que não participa do licenciamento,
   logo não há dívida.
2. **"Pool interno não tem recurso próprio" não é caso especial** — cai fora do D3 sozinho. Cobrando na
   transição 0→1 da INSTÂNCIA, trabalho que chega pelo pool interno a uma instância já em execução é
   1→2: não consome licença, sem código dedicado.

### 9.1 Amarra — pool interno resolve licenciamento no PAI

Borda remanescente: se a transição **0→1 ocorrer pelo pool interno**, a licença seria debitada no balde
do interno (sem piso/teto → compartilhado), enquanto o pool real — que pode ter piso — não é debitado.
Não quebra a contagem (segue uma licença por instância), mas põe o débito no balde errado.

Regra: pool interno **não tem balde próprio**; seu licenciamento resolve no pool pai. Forma mínima —
um campo anulável no pool (ex.: `license_parent_pool_id`), não uma entidade nova.

**Hoje isto é guarda, não trabalho:** todos os pools auto-criados são humanos, então nenhum caminho de
IA exercita a borda. Implementar a resolução antes de existir pool interno de IA seria adiantar
complexidade sem caso de uso — registrar a regra basta.

### 9.2 Distinção a preservar

O `-int` **anuncia** capacidade no snapshot (medido: `available 3`). Isso é correto para *relatório* —
é o mesmo humano compartilhado, e a tag/recompute existe justamente para esse 3 não ser somado ao do
pai. O que nunca pode acontecer é o anúncio virar uma **segunda licença**. Relatar capacidade e debitar
licença são caminhos distintos e permanecem distintos.

### 9.3 Onde o invariante `Σ license_floor ≤ C_ai` é imposto

Na **escrita da configuração** (422, como o agent-registry já faz para o análogo de
`session_reservation`), nunca na aquisição. Piso insatisfazível descoberto em runtime é privação
garantida — e a mensagem de erro chegaria ao cliente em vez de ao administrador. Com D10 a imposição
fica ainda mais dura: o piso que não puder ser **materializado** no boot é erro alto. Pools humanos —
inclusive os espelhos `-int` — não entram nessa soma: piso/teto são **N/A**, não 0 (D10).

### 9.4 O default é seguro e observável

Pool novo sem configuração nasce `floor = 0`, `ceiling = C_ai` (melhor esforço): não rouba garantia de
ninguém (o piso alheio está no conjunto do dono, D10) e, se começar a consumir, aparece na escada de
alerta do §3. A dívida de configuração, quando existir, é **observável** em vez de silenciosa.

---

## 10. Implementação, recuperação e mutação de configuração

### 10.1 Princípio — materializado no runtime, derivado na recuperação

**Pool não segura licença.** Pool é configuração, não processo: não existe "processo do pool" para
cair. Quem segura é a **instância em execução** (D3).

A licença tomada não é fato independente — é projeção de dois fatos que já existem e já são
autoritativos: *a instância está rodando* (`SCARD(sessions) ≥ 1`) e *o trabalho é deste pool* (a tag de
pool no occupant). Logo o estado de licença é **função pura**:

```
licenças = f( C_ai , {license_floor_P} , ocupação das instâncias , tag de pool nos occupants )
```

Portanto: **materializado em Redis para o caminho quente** (é lá que a corrida acontece) e
**reconstruído por derivação** em qualquer recuperação. Nenhum ledger durável novo — ele seria a
terceira fonte de verdade capaz de discordar das outras duas.

**Onde a durabilidade cabe:** no histórico, não no estado. `license.acquired` / `license.released` em
Kafka → ClickHouse, como o resto da plataforma. Um ledger em Postgres tentaria ser estado e história
ao mesmo tempo e falharia nas duas.

### 10.2 O que é durável e o que é derivado

| Fato | Onde | Durável |
|---|---|---|
| `C_ai` (inventário) | pricing Postgres → `quota_sync` (já tem `sync_all` no boot, auto-cura após flush do Redis) | sim |
| `license_floor` / `license_ceiling` | slot de deploy do pool (§10.5) | sim |
| quem está com o quê | derivado de ocupação + tag | **não precisa ser** |
| `floor_pending_P` | derivado: `max(0, floor_P − used_P − \|livres_do_próprio_P\|)` | não |

### 10.3 Reconstrução

```
1. lê C_ai e os pisos/tetos (fontes duráveis)
2. varre instâncias; toda com SCARD ≥ 1 segura UMA licença
3. atribui cada uma ao pool da tag do occupant
4. por pool: as primeiras floor_P contra o conjunto próprio, o excedente contra o shared
5. materializa o restante: conjunto do dono até o piso, sobra no shared
```

Idempotente e determinístico. Se `Σ tomadas > C_ai` (encolhimento de contrato, §10.6): loga alto,
**não derruba sessão viva** (D8) e aposenta na devolução (D10).

### 10.4 Gatilhos

| Evento | Ação | Reusa |
|---|---|---|
| boot | reconcile completo | padrão `ReconciliationReport` do `instance_bootstrap` |
| periódico (~60 s) | reconcile — cura vazamento | cadência do reconciler de admissão |
| `registry.changed(pool)` | rebalanceia piso (§10.5) | tópico já consumido pelo routing-engine |
| quota alterada | redimensiona inventário | `quota_sync` já dispara |
| caminho quente | toma/devolve dentro do Lua do claim | co-commit (D10) |

**O reconciler é o reaper.** Como a licença é derivável da ocupação, quem já limpa instância morta
(`crash_detector`, TTL do SET) libera a licença de tabela assim que o reconcile re-derivar. Não
construir um segundo varredor.

**Nenhum estado de licença em memória de processo.** Tudo em Redis — é o que torna o restart do
routing-engine um não-evento. Cachear o conjunto livre num dicionário Python destruiria a propriedade.

### 10.5 Mutação de configuração = artefato de DEPLOY, não coluna quente

`license_floor` e `license_ceiling` vivem no **slot do pool**, ao lado de
`deployed_max_concurrent_sessions`. Mudam por `set-next` → `promote`, podem ser **agendadas** e são
versionadas. Três razões:

1. **Coerência com o invariante existente** — "o que roda é o snapshot do slot `current`, não a
   edição". Capacidade seguir a mesma regra elimina uma exceção em vez de criar uma.
2. **Os dois fatores do produto promovem juntos** — capacidade de sessão é `licenças ×
   max_concurrent`; com ambos no mesmo artefato, o produto muda atomicamente. Hoje `deployed_max_
   concurrent_sessions` é do deploy e `session_reservation` é coluna quente: dá para mudar um fator
   sem cerimônia e o outro com.
3. **Mudar o piso de um pool muda o que sobra para todos os outros** — é redistribuição, não config
   local.

**A cerimônia e a convergência resolvem uma à outra.** Aumentar piso em pico pode não materializar na
hora (converge conforme os outros devolvem). Mas se a mudança só ocorre no promote, e promotes são
agendados para janela de baixo movimento, o pote está ocioso e a materialização é **instantânea**.
`floor_pending > 0` deixa de ser o caso normal e vira exceção; a convergência preguiçosa fica como
rede de segurança.

**O promote revalida — não herda a validação do `set-next`.** Entre marcar o próximo slot e promovê-lo
às 3h passa tempo, e `C_ai` pode ter encolhido no intervalo. `POST /v1/pools/:id/promote` verifica
`Σ license_floor ≤ C_ai` com o valor do próximo slot, junto com a checagem de capacidade que já faz
(422). O caminho agendado já trata corretamente: `pool_promote` devolve `isError` em não-2xx e cai no
`on_failure` — promoção nenhuma em silêncio.

**Gatilho:** o promote publica `registry.changed(pool)`, já consumido pelo routing-engine. O
rebalanceamento pendura ali. O **rollback** do slot usa o mesmo tratador na direção inversa.

#### Mecânica por tipo de mutação

**Teto — trivial** (não é materializado; é só a comparação `used_P < ceiling_P` na aquisição):
aumentar não exige nada; diminuir abaixo do uso atual **não é erro** — o pool não adquire mais até
drenar (negação `ceiling_reached`, D7) e converge sozinho. Efeito global: muda `Σ ceiling` e o
indicador de sobre-subscrição.

**Piso — aumentar.** Licenças que o pool já usa **já são dele**: só reclassificam. Sai do
compartilhado apenas

```
a_materializar_P = max(0, floor_P − used_P) − |livres_do_próprio_P|
```

Duas checagens distintas: **validade estática** (`Σ floor ≤ C_ai`, verificável sem runtime → 422,
rejeita) e **materialização dinâmica** (config válida mas licenças momentaneamente tomadas → aceita e
converge; com a invariante estática satisfeita a convergência é garantida, pois quem segura está acima
do próprio piso por definição).

> Isso precisa o D10: *"erro alto se não puder materializar no boot"* vale **porque no boot tudo está
> livre** — falhar ali significa violação da invariante estática, erro de configuração real. Em
> runtime o mesmo sintoma é transiente.

**Piso — diminuir.** Tira do conjunto livre do próprio pool → devolve ao compartilhado. Se não houver
livres suficientes (o pool está usando), o excedente é marcado para rebaixamento e volta **na
devolução** — mesma máquina preguiçosa da aposentadoria por encolhimento de contrato (D10).

**Criar pool.** `floor = 0` (default, §9.4): nada a materializar. `floor > 0`: idêntico a aumentar a
partir de zero, com `Σ floor + novo ≤ C_ai` na escrita. Pool interno auto-criado é humano ⇒ sem
objeto-licença (D10): **a criação automática nunca toca licenciamento**, que era a aposta do D9.

**Remover pool.** Livres do conjunto dele voltam ao compartilhado na hora. As **tomadas** seguem com a
sessão viva e voltam na devolução — e o dono já não existe. Regra explícita: **licença cujo pool dono
foi removido volta para o compartilhado.** Sem isso ela some do sistema: livre em lugar nenhum.

### 10.6 Assimetria — o pote é quente, a pretensão é cerimonial

`C_ai` muda a quente (fato comercial, pricing). A pretensão de cada pool sobre ele muda por cerimônia.
Logo um **encolhimento de contrato pode violar `Σ floor ≤ C_ai` sem que nenhum deploy aconteça**.

Invariante que atravessa dois domínios de governança: um tem de ceder, e não pode ser o comercial. O
lado do deploy cede — e a forma honesta de ceder é o excesso ficar **não-materializável e visível**
(`floor_pending` permanente, com a lista dos pools afetados). **Nunca** reduzir pisos proporcionalmente
em silêncio: seria fabricar um valor plausível onde havia um conflito real.

Aviso no momento certo: na tela do pricing, ao reduzir — *"esta redução deixa N pools com piso não
honrável"*.

### 10.7 Os tratadores são otimização do reconciler, não a autoridade

Como o estado inteiro é função pura (§10.1), nenhum tratador de mutação precisa ser
transacionalmente perfeito. Eles existem para a tela responder na hora; se um tiver bug, crashar no
meio de uma transferência ou perder um evento, o reconcile seguinte re-deriva e conserta.

Muda o critério de qualidade do código: de *"esta transferência precisa ser atômica em todos os
caminhos"* para *"esta transferência precisa convergir"*. Bem mais fácil de acertar, e falha
barulhenta em vez de silenciosa. É o mesmo padrão que a plataforma já usa para snapshot de pool
(escreve no evento, reconcilia por período).

### 10.8 Superfície de configuração

Por pool: `piso` · `teto` · `materializado` · `pendente`. Global: `Σ ceiling − C_ai`
(sobre-subscrição, D4) e `Σ floor` contra `C_ai`. `pendente > 0` persistente indica piso que o
ambiente não consegue honrar — informação que hoje não existe em lugar nenhum.

### 10.9 Duas armadilhas de nome

**"Reserva" já significa outra coisa no pricing.** `POST /v1/pricing/reserve/{tenant}/{pool}/activate`
é capacidade extra comprada por dia de ativação: **aumenta o pote**. `license_floor` **particiona o
pote existente**. Eixos diferentes, mesma palavra, e podem coexistir no mesmo pool — alguém vai somar
as duas num relatório se não estiverem nomeadas lado a lado.

**`session_reservation` é coluna quente hoje.** Movê-lo para o slot **não é renomear** — é mudança de
governança. A migração precisa dizer isso, senão alguém "conserta" reexpondo o campo na edição do pool.

### 10.10 Correções a decisões anteriores desta ADR

- **§8 estava errado** ao dizer que o arco de relatório e este podem avançar totalmente em paralelo: a
  reconstrução (§10.3) depende da **tag de pool no occupant**. Corrigido no §8.
- **D10 exagerou** ao dizer "over-alocação vira impossível em vez de checada". Dentro de um script
  atômico, uma checagem aritmética *é* uma barreira — o Lua não tem corrida. O argumento honesto a
  favor dos objetos é outro (e continua bom): contagem exige **contador**, e contador é o que derivou
  em todos os pontos deste arco; com conjuntos a contagem é `SCARD`, derivada, coerente com o
  invariante 4 — mais a inspecionabilidade. Corrigido no D10.
