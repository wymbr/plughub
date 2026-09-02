#!/usr/bin/env bash
# probe_seed_drift_named.sh — GATE da D7 (arco ALLOWLIST): o seed COMPARA e LOGA
# em vez de pular mudo.
#
# ── A proposicao ────────────────────────────────────────────────────────────────
#
# O seed e seed-if-absent, e isso e a politica certa: depois do primeiro boot o DB
# e a fonte de verdade. Ate 2026-08-29 o pulo era MUDO — `skipped += 1` e nada mais.
# O modo de falha foi medido DUAS vezes na mesma sessao:
#   · `masking.types` ganhou o tipo `texto` na declaracao e a base seguiu com 10;
#   · `masking.context_rules` tem 23 regras declaradas e 14 gravadas — uma base
#     semeada hoje herdaria a politica anterior ao conserto de 2026-08-26.
#
# O gate NAO afirma que a base esta em dia (nao esta, e nao e trabalho do seed
# consertar). Afirma que a divergencia e VISIVEL: contada, nomeada, com as DUAS
# direcoes separadas — e que o seed continua NAO escrevendo.
#
# ── Por que as duas direcoes sao ramos separados ────────────────────────────────
#
# Reaplicar uma key ACRESCENTA o que so existe na declaracao e DESCARTA o que so
# existe no banco. Medido no `__global__` vivo, `masking.context_rules` diverge nos
# dois sentidos: 10 regras so no declarado E `session.cpf_titular` so no gravado,
# que nenhum glob declarado cobre (`*.cpf` casa o sufixo `.cpf`, nao `cpf_titular`).
# Um log que dissesse so "diverge" faria alguem rodar `--overwrite` e apagar uma
# regra de mascaramento sem saber. Nomear a direcao E a entrega.
#
# Seis ramos:
#   A. DECLARACAO ATUAL   — o seed.py do container e o do repo (senao a comparacao
#                           julga uma declaracao velha; foi o que aconteceu hoje)
#   B. NOMEIA             — divergencia injetada sai contada e com o nome da key
#   C. DUAS DIRECOES      — so-no-declarado, so-no-gravado e o aviso destrutivo
#   D. TESTEMUNHA NEGATIVA— key IGUAL nao aparece no relatorio
#   E. NAO CURA           — o valor gravado segue intacto depois da passada
#   F. COMPARADOR PURO    — os testes de unidade do `config_drift`
#
# O ramo D existe porque um comparador que marcasse tudo passaria em B e C e nao
# valeria nada. O ramo E porque "nomear" e "consertar" sao decisoes diferentes, e
# esta ADR recusa a segunda.
#
# ⚠️ AVISO A QUEM FOR MUTAR ESTE GATE. Este probe so escreve na key-cobaia, e a
# restaura no `trap`. Mas a mutacao que prova o ramo E (fazer o seed CONSERTAR)
# transforma a passada num `--overwrite` de TODA key divergente — e ela escreve no
# store de verdade. Aconteceu em 2026-08-29: a bateria reescreveu
# `__global__.masking.context_rules` e removeu `session.cpf_titular` (restaurado no
# mesmo dia; o override do `tenant_demo` ficou intacto e nada de mascaramento
# mudou). Antes de rodar uma bateria assim, snapshote TODA key ja divergente, nao
# so a cobaia.
#
# Tres estados: OK / FALHA / INCONCLUSIVO (nunca OK com ramo inconclusivo).
# ⚠️ UTF-8 explicito na SAIDA do python. No Windows o `stdout` decodifica com cp1252 e
# um `print` de texto acentuado estoura `UnicodeEncodeError`, derrubando o probe por
# motivo de bancada — ou, pior, mutila o texto que o shell vai comparar.
#
# ⚠️ E o que esta linha NAO conserta, porque o diagnostico foi REFEITO em 2026-09-02:
# a corrupcao que motivou a CNS-12 nao vinha do `sys.stdin` (medido: `curl | python3 ->
# arquivo` preserva `Almoco`/`Reuniao` intactos). Vinha da VARIAVEL DE SHELL — passar
# JSON nao-ASCII por `VAR=$(…)` o mutila, medido 321 bytes contra 325. Contra isso a
# unica defesa e nao passar por variavel: producao e consumo por ARQUIVO.
export PYTHONIOENCODING=utf-8

set -u

cd "$(dirname "$0")/../.." || exit 2

CONFIG_API="${CONFIG_API:-http://localhost:3600}"
CONFIG_TOKEN="${CONFIG_TOKEN:-demo_config_admin_token}"
CONTAINER="${CONFIG_CONTAINER:-plughub-demo-config-api-1}"
# Nesta bancada o shell e Git Bash (MSYS), que CONVERTE um argumento comecando com
# `/` em caminho Windows antes de o docker o ver: `test -f /app/...` chega ao
# container como `C:/Program Files/Git/app/...` e da rc=1. O ramo A entao publicava
# "seed.py nao existe no container" sobre um arquivo que ESTA la — e como ele zera o
# `drift_ok`, os ramos vivos deixavam de julgar em cascata. Terceira ocorrencia da
# mesma familia em 2026-09-01 (as outras: `npx` sob UNC e `/tmp` resolvido contra a
# raiz do cwd), e todas com a mesma forma: bancada reprovando no lugar do produto.
export MSYS_NO_PATHCONV=1
IN_CONTAINER="/app/packages/config-api/src/plughub_config_api"

# ⚠️ O JSON da cobaia NAO passa por variavel de shell, e isto e conserto medido
# (2026-09-02). O valor tem acento (`Almoco`, `Reuniao`), e nesta bancada a ida-e-volta
# por variavel DUPLO-CODIFICA o UTF-8: medido 321 bytes via shell contra 325 direto, e
# cada ciclo empilha mais uma camada. O efeito no gate era o pior possivel — o ramo E
# comparava o gravado com o que ele PENSAVA ter escrito, nunca batia, e publicava
# "o seed REESCREVEU a key divergente": acusacao ao PRODUTO por defeito da BANCADA,
# e justamente sobre a unica decisao que esta ADR faz questao de nao tomar (consertar
# sozinho). Pior: cada rodada corrompia a cobaia de verdade.
SNAP_F="$(cd "$(dirname "$0")" && pwd)/.seed_drift_snapshot.json"
MUT_F="$(cd "$(dirname "$0")" && pwd)/.seed_drift_mutated.json"
export SNAP_F MUT_F

# ⚠️ CORRECAO DE DIAGNOSTICO (2026-09-02, CNS-17). Este bloco afirmava que a causa
# raiz era o `sys.stdin` do python decodificando em cp1252. **Falso, e remedido:**
# `curl | python3 -c 'json.load(sys.stdin)' -> arquivo` preserva `Almoco`/`Reuniao`
# intactos. A causa e SO a VARIAVEL DE SHELL — 321 bytes contra 325 na mesma volta.
#
# O conserto funcionou, mas pelo outro motivo: o que salvou foi migrar para ARQUIVO,
# nao o `sys.stdin.buffer`. Este ficou porque e explicito e nao custa nada, mas nao e
# ele que impede a corrupcao — atribuir o conserto ao mecanismo errado e como se
# reintroduz o defeito no proximo probe, 'copiando a parte que nao era a que curou'.

# Key-cobaia: lista de dicts com `id`, sem consumidor critico, restaurada no fim.
PROBE_NS="agent_activity"
PROBE_KEY="pause_reasons"
# Key-testemunha: tem de continuar FORA do relatorio.
WITNESS_NS="sentiment"
WITNESS_KEY="thresholds"

fail=0
inconclusive=0
bad()  { echo "  x $*"; fail=$((fail+1)); }
ok()   { echo "  v $*"; }
huh()  { echo "  ? $*"; inconclusive=$((inconclusive+1)); }

echo "=== probe_seed_drift_named — D7 do arco ALLOWLIST ==="

SNAPSHOT=""
restore() {
  # Restaura do ARQUIVO, nunca de variavel: o snapshot tem acento e a volta pelo shell
  # duplo-codifica (ver o comentario de SNAP_F). Restauracao corrompida e pior que
  # nenhuma — devolve a cobaia parecendo intacta, com o dado estragado.
  if [ -s "$SNAP_F" ]; then
    curl -sS -o /dev/null -X PUT \
      -H "X-Admin-Token: $CONFIG_TOKEN" -H 'Content-Type: application/json' \
      "$CONFIG_API/config/$PROBE_NS/$PROBE_KEY" \
      --data-binary "@$SNAP_F" 2>/dev/null
    echo "  (cobaia $PROBE_NS.$PROBE_KEY restaurada)"
  fi
  rm -f "$SNAP_F" "$MUT_F"
}
trap restore EXIT

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- A. DECLARACAO ATUAL (o container compara o que o repo declara) --"
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  huh "A: container $CONTAINER ausente — nenhum ramo vivo pode julgar"
else
  drift_ok=1
  for f in seed.py config_drift.py; do
    src="packages/config-api/src/plughub_config_api/$f"
    if ! docker exec "$CONTAINER" test -f "$IN_CONTAINER/$f" 2>/dev/null; then
      bad "A: $f nao existe no container"; drift_ok=0; continue
    fi
    a=$(sha256sum "$src" 2>/dev/null | cut -d' ' -f1)
    b=$(docker exec "$CONTAINER" sha256sum "$IN_CONTAINER/$f" 2>/dev/null | cut -d' ' -f1)
    if [ -z "$a" ] || [ -z "$b" ]; then
      huh "A: nao consegui comparar $f"; drift_ok=0
    elif [ "$a" != "$b" ]; then
      # Nao e detalhe de deploy: o seed compara a declaracao QUE ELE TEM. Com a
      # imagem atrasada, "divergent=0" e uma afirmacao sobre um arquivo que nao
      # e o do repo — foi exatamente o que se mediu em 2026-08-29 antes desta D7.
      bad "A: $f do container != do repo — a comparacao julgaria declaracao VELHA (rebuild do config-api)"
      drift_ok=0
    fi
  done
  [ "$drift_ok" = "1" ] && ok "A: seed.py e config_drift.py do container sao os do repo"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- B/C/D/E. passada viva com divergencia INJETADA --"
if [ "$inconclusive" -gt 0 ]; then
  huh "B-E: sem container, nao ha passada viva a julgar"
else
  # `?tenant_id=` e OBRIGATORIO neste GET (422 sem ele) — omiti-lo faz o ramo sair
  # INCONCLUSIVO parecendo "servico fora do ar". E a mesma pegadinha ja registrada
  # no CLAUDE.md sobre a leitura de config falhar em camadas de aparencia igual.
  curl -sS --max-time 10 "$CONFIG_API/config/$PROBE_NS/$PROBE_KEY?tenant_id=__global__" 2>/dev/null \
    | python3 -c 'import sys,json,io,os;json.dump({"tenant_id":None,"value":json.load(sys.stdin.buffer)["value"],"description":"restaurado por probe_seed_drift_named"},io.open(os.environ["SNAP_F"],"w",encoding="utf-8"),ensure_ascii=False)' 2>/dev/null
  SNAPSHOT=$([ -s "$SNAP_F" ] && echo ok)
  if [ -z "$SNAPSHOT" ]; then
    huh "B-E: nao consegui ler $PROBE_NS.$PROBE_KEY em $CONFIG_API"
  else
    # A injecao produz as TRES formas de divergencia de uma vez:
    #   · `almoco` sai           → so no DECLARADO
    #   · `probe_drift` entra    → so no GRAVADO  (a direcao destrutiva)
    #   · `intervalo.label` muda → DIFEREM
    python3 -c 'import json,io,os,subprocess,sys;snap=json.load(io.open(os.environ["SNAP_F"],encoding="utf-8"))["value"];r=subprocess.run([sys.executable,"infra/test/_seed_drift_mutate.py"],input=json.dumps(snap),capture_output=True,text=True,encoding="utf-8");json.dump({"tenant_id":None,"value":json.loads(r.stdout),"description":"injetado por probe_seed_drift_named"},io.open(os.environ["MUT_F"],"w",encoding="utf-8"),ensure_ascii=False)'
    code=$(curl -sS -o /dev/null -w '%{http_code}' -X PUT \
      -H "X-Admin-Token: $CONFIG_TOKEN" -H 'Content-Type: application/json' \
      "$CONFIG_API/config/$PROBE_NS/$PROBE_KEY" \
      --data-binary "@$MUT_F" 2>/dev/null)
    if [ "$code" != "200" ]; then
      huh "B-E: PUT da divergencia devolveu $code (token?)"
    else
      OUT=$(docker exec "$CONTAINER" plughub-config-seed 2>&1)

      # B — contada e NOMEADA
      if echo "$OUT" | grep -q "DIVERGE $PROBE_NS.$PROBE_KEY"; then
        ok "B: a divergencia injetada e NOMEADA ($PROBE_NS.$PROBE_KEY)"
      else
        bad "B: a divergencia injetada nao aparece no relatorio — o pulo voltou a ser mudo"
      fi
      if echo "$OUT" | grep -qE 'divergent=[1-9]'; then
        ok "B: contada em divergent="
      else
        bad "B: o contador divergent nao subiu"
      fi

      # C — as DUAS direcoes + aviso destrutivo
      LINE=$(echo "$OUT" | grep "DIVERGE $PROBE_NS.$PROBE_KEY" | head -1)
      c_ok=1
      # Cada item e cobrado JUNTO da sua direcao. Um `grep almoco` solto passaria
      # com o item citado no lado errado — e o lado e a informacao que decide se
      # `--overwrite` perde alguma coisa.
      echo "$LINE" | grep -q 'DECLARADO=1 \[almoco\]'          || { bad "C: 'so no DECLARADO' nao nomeia o item removido (almoco)"; c_ok=0; }
      echo "$LINE" | grep -q 'GRAVADO=1 \[probe_drift\]'       || { bad "C: 'so no GRAVADO' nao nomeia o item extra (probe_drift)"; c_ok=0; }
      echo "$LINE" | grep -q 'DIFEREM=1 \[intervalo\.label\]'  || { bad "C: campo alterado nao nomeado (intervalo.label)"; c_ok=0; }
      echo "$LINE" | grep -q "DESCARTA 2 item"                 || { bad "C: nao avisa que --overwrite DESCARTA os 2 itens que so estao no banco"; c_ok=0; }
      if [ "$c_ok" = "1" ]; then
        ok "C: as duas direcoes nomeadas + aviso de descarte"
      else
        # Ramo que reprova sem mostrar o que viu obriga a reproduzir a mao — e
        # reproduzir a mao e onde o diagnostico erra.
        echo "      linha observada: $LINE"
      fi

      # D — TESTEMUNHA NEGATIVA: key igual nao pode aparecer
      if echo "$OUT" | grep -q "DIVERGE $WITNESS_NS.$WITNESS_KEY"; then
        bad "D: key IGUAL ($WITNESS_NS.$WITNESS_KEY) reportada como divergente — o comparador marca tudo"
      else
        ok "D: key igual ($WITNESS_NS.$WITNESS_KEY) fora do relatorio"
      fi
      if echo "$OUT" | grep -qE 'skipped=[1-9][0-9]*'; then
        ok "D: a maioria segue contada em skipped (nao virou tudo divergencia)"
      else
        bad "D: skipped zerou — sinal de comparador que marca tudo"
      fi

      # E — NAO CURA
      # A comparacao acontece DENTRO do python, contra o arquivo que foi enviado —
      # nunca entre duas variaveis de shell. Era ali que o UTF-8 se perdia e o ramo
      # acusava o produto por um estrago da bancada.
      VEREDICTO=$(curl -sS --max-time 10 "$CONFIG_API/config/$PROBE_NS/$PROBE_KEY?tenant_id=__global__" 2>/dev/null \
        | python3 -c 'import sys,json,io,os;esperado=json.load(io.open(os.environ["MUT_F"],encoding="utf-8"))["value"];gravado=json.load(sys.stdin.buffer)["value"];print("IGUAL" if json.dumps(gravado,sort_keys=True,ensure_ascii=False)==json.dumps(esperado,sort_keys=True,ensure_ascii=False) else "DIFERE")' 2>/dev/null)
      if [ "$VEREDICTO" = "IGUAL" ]; then
        ok "E: o valor gravado segue INTACTO — o seed nomeia, nao conserta"
      else
        bad "E: o seed REESCREVEU a key divergente — conserto automatico nao e a decisao desta ADR"
      fi
    fi
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- F. COMPARADOR PURO --"
if command -v python3 >/dev/null 2>&1 && python3 -m pytest --version >/dev/null 2>&1; then
  if ( cd packages/config-api && PYTHONPATH=src python3 -m pytest \
        src/plughub_config_api/tests/test_config_drift.py \
        src/plughub_config_api/tests/test_store.py -q >/tmp/seed_drift_pytest.log 2>&1 ); then
    ok "F: testes do comparador e do seed verdes ($(grep -oE '[0-9]+ passed' /tmp/seed_drift_pytest.log | head -1))"
  else
    bad "F: testes reprovaram — ver /tmp/seed_drift_pytest.log"
  fi
else
  huh "F: pytest indisponivel"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
if [ "$inconclusive" -gt 0 ]; then
  echo "INCONCLUSIVO ($inconclusive ramo(s) sem poder julgar, $fail falha(s))"
  exit 2
elif [ "$fail" -gt 0 ]; then
  echo "FALHA ($fail)"
  exit 1
else
  echo "OK — divergencia contada, nomeada nas duas direcoes, e nao consertada em silencio"
  exit 0
fi
