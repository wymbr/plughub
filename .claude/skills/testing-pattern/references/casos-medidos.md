# Casos medidos — o porquê de cada regra da skill `testing-pattern`

> Texto movido **integralmente** do `CLAUDE.md` § *Postura de Engenharia* em 2026-09-16 (piloto
> de skills). Lá ficou a regra em uma ou duas linhas; aqui fica a medição que a justifica. Não
> resuma este arquivo: o valor dele é o caso concreto — número, arquivo, data —, que é o que
> impede a regra de ser relida como gosto.

---

## 1. Um instrumento pode ser falseável, ramificado e honesto — e ainda medir a proposição ERRADA

Não é o teste que não pode reprovar (essa família já está abaixo); é o teste que reprova
corretamente **uma pergunta adjacente à que se fez**. Medido em 2026-08-24 na D14.1: o probe do
aging inerte tinha três ramos (`VIVO`/`LATENTE`/`INCONCLUSIVO`) e testemunha de presença ao lado,
e mesmo assim não sabia responder o que importava — porque *"contato esperou neste pool"* e *"a
espera foi longa o bastante para o alvo importar"* são **dois fatos**, e só o segundo é dano. A
medição saiu `VIVO` (16 de 63 esperas em pools de alvo absurdo) enquanto o dano era **zero** (as
esperas ali são de 5 a 14 segundos, e quem espera 8 s não precisa de aging). Um relatório fiel ao
ramo teria publicado um defeito que não existe. **Ao desenhar o veredicto, pergunte de qual
PROPOSIÇÃO cada ramo é evidência** — e quando a pergunta tem a forma *"isto machuca?"*, exposição
e dano são grandezas separadas, que precisam de dois números, nunca de um ramo só. *(Irmão de
`exposicao-latente-e-hipotese`, na direção inversa: lá faltou contar quem sofre antes de declarar
inócuo; aqui contou-se quem foi exposto e chamou-se de sofrimento.)*

---

## 2. Um teste que não pode reprovar é pior que teste nenhum — ele compra confiança sem dar nada

O modo de falha é sempre o mesmo: a asserção nunca alcança a condição que deveria julgar, e o
resultado (verde, ou `skipped`) parece resposta. Catálogo do que já aconteceu: `skipped` por ler
`REDIS_URL` quando o serviço define `PLUGHUB_REDIS_URL` (9 testes do claim pull, **nunca** rodaram
no container) · `MagicMock` devolvendo truthy para `analytics_open_access` (14 testes de RBAC
trocaram de caminho) · `set -e` + `VAR=$(curl …)` matando o script sem imprimir quando o serviço
ainda sobe · `jq '.campo // empty'` tratando `false` como ausente · janela por `started_at`
cobrando dado gravado antes do deploy (o corte certo é `ingested_at`). **Antes de aceitar um
verde, pergunte o que o faria ficar vermelho** — e prefira que o teste se declare INCONCLUSIVO a
passar por ausência de amostra.

### 2a. Um mock não verifica que o alvo existe; ele o CRIA (2026-09-07, VOZ-03)

`voice.py` chamava **seis** métodos que não existiam em lugar nenhum do MRO, e a
suíte os mockava sob o comentário *"Mock inherited base methods"*: o comentário afirmava
herança e a atribuição da linha seguinte tornava a afirmação verdadeira **dentro do teste**.
O teste prova a CHAMADA e esconde a AUSÊNCIA. No produto o `AttributeError` caía num
`except Exception` largo que o reportava como fim NORMAL do laço, em `debug` — o canal de voz
nunca publicou nada e nada ficou vermelho. Regra: **ao mockar um método do próprio objeto sob
teste, asserte `hasattr` antes**; e o censo que vale é AST sobre a população (`hasattr`
responde por uma classe de cada vez e exige a imagem de pé). Gate:
`infra/test/probe_adapter_self_calls.sh`.

### 2b. O irmão de CONTRATO, do mesmo dia

`sms.py` publicava `payload["answers"]` e o bridge
lê `payload["result"]` — ninguém lia `answers`, e o teste do SMS afirmava `answers`. Produtor
e teste olhando um para o outro, **nenhum dos dois para o consumidor**. Um contrato de payload
não mora em nenhum dos lados: mora ENTRE eles, e por isso nenhum `grep` num arquivo o alcança.
O gate que o fecha (`probe_menu_result_contract.sh`) **mede a chave no LEITOR** — escrevê-la
como constante mediria a concordância dos produtores com o gate, e trocar a chave no bridge
deixaria os quatro verdes contra um leitor que mudou.

### 2c. Esperar por CONTAGEM DE YIELDS é adivinhar a estrutura interna da corrotina (2026-08-30)

`await asyncio.sleep(0)` depois de um
`ensure_future` não espera a task: espera **um** turno do loop. Medido no emissor de tokens,
`sources()` só enche a partir de **2** yields e os dois eventos a partir de **5** — dois
testes vermelhos com o produto CERTO, e a leitura óbvia (*"a emissão não acontece"*)
apontando para uma regressão inexistente. Pior, o gêmeo deles **passava por acidente**: o
caminho dele tinha um `await` a mais DEPOIS do agendamento, e era ele que dava os turnos —
mesmo produto, veredictos opostos, decididos por uma linha alheia à proposição. **Espere
pelas TASKS**, e mantenha o conjunto delas no PRODUTO, não no teste: um helper que use
`asyncio.all_tasks` varre também as tasks de quem chamou. Aqui o conjunto já precisava
existir por outra razão — `ensure_future` sem guardar o retorno deixa o loop como único dono
e o CPython avisa que a task pode ser coletada no meio da execução; num produtor de CUSTO
isso é fail-silent com a evidência na FATURA. Ver `CHANGELOG.md` 2026-08-30.

### 2d. INCONCLUSIVO emitido depois de um ✗ esconde o defeito (2026-09-23, APF-02)

A primeira versão do `probe_apf02_session_complexity.sh` conferia a população do ramo D (as
transferências) **no relatório** e **depois** dos veredictos. Com o relatório quebrado pela
mutação M1 (o alias `s` removido, o defeito original), B e C reprovavam, e o D, sem população
no relatório, emitia INCONCLUSIVO — que troca o `exit 1` por `exit 2`. Na bateria, `2` não é
"pega": a M1 **sobrevivia** justamente por causa do defeito que plantava. O conserto foi medir a
população no **censo independente** e **antes** de qualquer veredicto. Regra: a pergunta *"há
amostra?"* se responde na fonte que não está sob teste, e se responde primeiro. Ver
`CHANGELOG.md` § 2026-09-23 (5).

### 2e. Mutação sem população na janela fica verde e parece proteção (2026-09-23, APF-01)

A bateria `mut_apf01_performance_source.sh` podia plantar três defeitos que o gate não tinha
como ver: job sem `FINAL`, sem o filtro de `origin` e sem a regra da transferência. Na janela
de 7 dias havia **0 versões não fundidas, 0 linhas não-live e 0 transferências** — contar
versões e contar segmentos dava o mesmo número, e as três mutações ficariam verdes. Uma
mutação verde é lida como *"o gate não pega isto"* só por quem sabe que a população é zero;
para todo o resto ela parece proteção. O que foi feito: a bateria planta só os dois defeitos
com população (M1 `system` de volta ao score, M2 família de escalação encolhida — os dois
pegos) e **declara no cabeçalho** os três que não mede e quem os guarda (o unitário sobre o
SQL executado, `test_reads_segments_final_not_the_mv_APF01`). A `mut_apf02_session_complexity.sh`
repete o formato para o `FINAL`. Ver `CHANGELOG.md` § 2026-09-23 (4).

### 2f. `sed` sem endereço e gate que confere o próprio censo (2026-09-23, APF-01/APF-02)

Dois erros de instrumento do mesmo dia, da mesma família — o instrumento media outra coisa:
- **O `sed` da M1 da APF-02 não tinha endereço** e mudou **12 queries** de `reports_query.py`
  em vez da única sob teste. Um vermelho assim não diz que o gate pega o defeito, diz que o
  arquivo quebrou. A mutação ficou restrita à função por intervalo
  (`/^def f/,/^# ───/ s/…/`), e o `muta()` das baterias confere com `cmp` que o arquivo mudou
  — idêntico ao original sai como *"a mutação NÃO se aplicou — não mede"*.
- **A primeira versão do ramo C da APF-01 conferia o CENSO**, que exclui `system` por
  construção — logo não podia reprovar. Foi reescrita sobre as linhas que a query do **job**
  devolve, e o ramo B compara o job com um censo feito por outro caminho (`argMax(…,
  row_version)` na tabela crua, em vez de `FINAL`). Ver `CHANGELOG.md` § 2026-09-23 (4) e (5).

### 2g. `ioredis-mock` compartilha dados entre instâncias (2026-09-21, MEN-08; 2026-09-23, ORQ-19)

Instâncias diferentes de `ioredis-mock` no mesmo processo de teste enxergam o mesmo dado. Na
MEN-08 o teste novo achou isso e passou a limpar no `beforeEach`. Na ORQ-19
(`menu-option-description.test.ts`) o `[0]` do stream era o menu de um teste anterior, e a
asserção passou a ler `.at(-1)`, com o comentário explicando por quê. Um teste que lê o `[0]`
pode ficar verde ou vermelho pelo teste vizinho, não pelo produto. Ver `CHANGELOG.md`
§ 2026-09-21 (6) e commit `0e1f6277`.

---

## 3. Um ambiente que só sobe porque já subiu antes não está sendo verificado — está sendo lembrado

Estado herdado (volume, imagem, linha de DB, coluna criada por `db push`) é entrada não declarada
do boot: enquanto ele existir, o aplicador pode estar quebrado sem que nada fique vermelho. Três
defeitos ANTIGOS caíram juntos no primeiro `down -v` (2026-08-05): `migrations` do agent-registry
atrás do `schema.prisma` (o `db push` aplicava o schema direto, então batia sempre); a ordem do
DDL do ClickHouse (a MV já existia de instalações passadas); e o `eval-seed` sem credencial desde
o G-PROBE fase 2 (o `GET` achava o formulário e retornava **antes** de exercer o gate, saindo 0).
Nenhum foi causado pelo wipe — o wipe foi o instrumento. **Instalação limpa é um teste, e teste
que nunca roda não é cobertura**: rode `infra/scripts/rebuild-all.sh --wipe` de propósito e em dia
calmo, não no dia em que você precisa da stack de pé. Corolário para diagnóstico: quando um
serviço falha logo após um wipe, a hipótese ordenada não é "o wipe quebrou", é "o wipe revelou".

### 3a. Pergunte à IMAGEM, nunca ao container (2026-08-30)

O `TODO.md`
dizia que **quatro** Dockerfiles Python não instalavam pytest. Medido: **nenhuma das 14
imagens** tinha, e os quatro containers em que a suíte "rodava" tinham o pytest instalado
**à mão** — estado que um `up -d` apaga. `docker exec` no container e `docker run` sobre a
imagem respondem perguntas DIFERENTES, e só a segunda é reprodutível. Quando o defeito é
*"isto só funciona aqui"*, o instrumento tem de ser o artefato, não a instância dele. Com o
pytest na imagem apareceram **15 falhas reais em 3 serviços** — nenhum deles o que o TODO
apontava, e 12 eram testes que ficaram **para trás de um portão de autorização** (a sexta
ocorrência do padrão da § Security). Gate: `infra/test/probe_python_suites.sh`, que separa
**declaração** (o Dockerfile pede `.[dev]`) de **imagem** (o pytest está lá) de **execução**
— a primeira sem a segunda é promessa sem mecanismo; a segunda sem a primeira fica verde por
container herdado.

### 3b. O runner óbvio nasce permanentemente vermelho

Rodar `pytest` da raiz do monorepo
(`cd /app`) troca o **rootdir**, e com ele o `[tool.pytest.ini_options]` de cada pacote
(`asyncio_mode = "auto"`) deixa de ser lido: **476 falsos vermelhos** contra 15 reais. Um
gate assim ensina todo mundo a ignorá-lo, que é pior que gate nenhum. Rode no WORKDIR do
pacote. O que denunciou foi comparar com uma medição anterior do MESMO serviço
(`channel-gateway`: 699/0 antes, 594/187 depois, **mesmo código**) — um número sozinho não
diz de qual proposição ele é evidência.

### 3c. "Rodou tudo o que a lista cita" e "a lista cita tudo" são DOIS fatos (2026-09-04, GAT-01)

Só o primeiro tinha mecanismo. O `run_gates.sh` executava fielmente
o `gates.manifest`; o manifesto declarava **44 de 281** scripts de `infra/test/`. Rodando os
144 não declarados com cara de gate, **100 saíram VERDES** — cobertura que já funcionava e
ninguém colhia. É a mesma família do teste que não pode reprovar, uma casa acima: *uma lista
parece completa por ser uma lista*, e o que falta não aparece em contagem nenhuma. Hoje o
manifesto presta contas de TODO `.sh` em quatro classes (AUTO · `!`assistido · `=`isento com
motivo · `?`não-triado NOMEADO), e `probe_gates_manifest_coverage.sh` reprova script que não
caia em nenhuma. ⚠️ **A população é "tudo" por medição, não por zelo**: um critério textual
para *"quem precisa ser declarado"* foi refutado duas vezes, nas duas por falso NEGATIVO
(`exit "$FAIL"` fora do padrão; os 35 `test_*` que julgam com `✅`/`❌`) — e critério que
decide quem é COBRADO pode esconder arquivo, que é justamente o defeito a fechar. O critério
sobrevive rebaixado a INFORMAÇÃO, ordenando a fila de triagem.
