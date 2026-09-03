#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Censo de cadastro do ContextStore — a lista da D9 por ANÁLISE ESTÁTICA.

    python3 infra/test/censo_contextstore_cadastro.py [--json] [--tenant T] [--config-api URL]

⚠️ **Isto é um INSTRUMENTO, não um gate.** Não tem veredicto: não há proposição
que ele possa reprovar. Ele produz a LISTA e os números que dimensionam a
migração da D9 (`docs/adr/adr-contextstore-allowlist.md`). O gate — se a D9 for
adiante — é o portão de publish, e ele consumirá este mesmo extrator.

Por que o extrator NÃO é um walker de YAML
------------------------------------------
Medido em 2026-08-30: são **SEIS** superfícies de escrita, e quatro não aparecem
caminhando a árvore do YAML. As três correções abaixo são o TESTE que qualquer
reescrita deste extrator tem de passar — cada uma nasceu de um falso negativo:

  (1) `context_json` é uma **string JSON dentro do YAML** (7 skills, 15 nomes).
      Um walker a vê como texto e segue em frente.
  (2) `invoke tool: context_set` / `context_write` guardam o nome em
      `input.tag` — um campo de step como outro qualquer.
  (3) `delegate.context` / `collect.context` têm o prefixo `session.`
      **composto no gateway** (`webhook.py:1695`, `:2721`), não no arquivo — o
      nome final não existe no YAML.

E há a origem que NENHUMA análise estática alcança: o corpo HTTP do webhook
(`webhook.py:630` escreve cada chave verbatim, sem prefixo). É de lá que vêm as
duas únicas tags sem namespace, `campaign_id` e `target_pool`.

O veredicto é PARTIDO POR ORIGEM, e a partição não é cosmética
------------------------------------------------------------
Até 2026-09-02 havia um número só de "não cobertos", medido contra o mapa
SEMENTE. Ele estava errado de duas maneiras ao mesmo tempo:

  (1) **Mapa errado.** A semente é o que o fonte TS declara (77/98); o mapa que
      roda é o do config-api (97/119 no demo), com as adições do tenant. Lido
      como pré-condição da V4, o censo publicou **18 não declaradas onde a
      resposta era 2** — 16 nomes já cadastrados enviados de volta para a fila.
      Os dois números respondem perguntas legítimas e diferentes; o defeito era
      o relatório não dizer qual respondia.

  (2) **Populações somadas.** Escrita de CÓDIGO de plataforma e escrita de SKILL
      não têm o mesmo peso nem o mesmo juiz. Código roda para todo tenant, tem
      de estar na SEMENTE, e **nenhum portão de publish o alcança**. Skill é
      julgada pelo mapa VIVO, que é o que o portão consulta — e neste
      repositório os skills são FIXTURES de teste, em rotação: um portão que
      recuse um deles está funcionando, e o custo é zero. Somar as duas fazia o
      número grande (descartável) esconder o pequeno (que carrega peso).

Resultado de referência (2026-09-02, semente 77/98 · vivo 97/119):
    66 escritos · 0 dinâmicos · 27 lidos sem escritor
    PLATAFORMA 14 escritas, 1 não declarada  (core.workflow.reviewer_id — decisão #5)
    TENANT     52 escritas, 1 não declarada  (session.journey_echo — passo de demo)

── 2026-09-03 (ALW-11): isto deixou de ser instrumento e virou PORTÃO ───────────
As duas metades foram a ZERO e o script passou a ter veredicto (exit 0/1/2):

    PLATAFORMA 13 escritas, 0 não declaradas   (o `reviewer_id` foi REMOVIDO —
               escrita sem leitor, em namespace fechado, com id de pessoa
               `agents_only` e sem tipo; ver o comentário no `router.py`)
    TENANT     51 escritas, 0 não declaradas

Um portão que nasce vermelho ensina todo mundo a ignorá-lo — por isso o achado foi
consertado ANTES da promoção. Envelope: `infra/test/probe_contextstore_cadastro.sh`.

⚠️ **E o extrator de plataforma estava perdendo tag por DISTÂNCIA.** A regra era
*"marca de escrita nas 6 linhas antes do literal"*; um comentário dentro de um
`_write_ctx({...})` empurrava a tag para fora e ela sumia do censo — descoberto ao
escrever o comentário que documenta a remoção acima. O relatório **não mudou de
número**, porque a mesma tag é escrita num segundo sítio e o resultado é dedupado
por nome: a redundância mascarou a perda. Hoje vale o ESCOPO DA CHAMADA (balanceia
brackets, pula strings), e `--autoteste` guarda a regressão contra texto sintético.

⚠️ ACHADO QUE ESTE CONSERTO PRODUZIU — o número muda depois de um `--wipe`
------------------------------------------------------------------------
O mapa vivo do demo tem 20 canônicas de domínio de TENANT (`cartao`, `conta`,
`portabilidade`, `processo`, `reembolso`) que **nenhum arquivo provisiona**. A
ALW-04 encolheu a semente para só-plataforma, o que está certo; o que não
existe é a contraparte — não há `infra/context-map/` como há `infra/dialog/`.
Aquelas 20 vivem no store porque alguém as escreveu pela API, uma vez.

Medido: contra a SEMENTE (o estado pós-`--wipe`), **17 das 52** escritas de
skill ficam não declaradas. Com o portão de publish da V4 ligado, o
`RegistrySyncer` as recusaria no boot — e ele **não bloqueia o startup**
(`registry_syncer.py:623`), então a stack sobe, os skills ficam sem publicar e
o único sinal é o contador `skills_errors`.

É a regra do CLAUDE.md sobre ambiente que só sobe porque já subiu antes. Rodar
este censo com `--sem-vivo` mostra o estado de instalação limpa.

O zero de dinâmicos foi CONFERIDO por mutação (2026-08-30): trocando um
`tag: "approval.summary"` por `"approval.{{$.pipeline_state.x}}"` o contador
vai a 1 e nomeia o sítio; revertendo, volta a 0. Sem essa conferência o zero
seria indistinguível de um detector morto — que é o modo de falha que este
repositório cataloga como "teste que não pode reprovar".
"""
import argparse
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from urllib.parse import quote

try:
    import yaml
except ImportError:                                        # pragma: no cover
    sys.exit("PyYAML necessário: pip install pyyaml")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SKILLS = os.path.join(ROOT, "packages/skill-flow-engine/skills")
MAPTS = os.path.join(ROOT, "packages/schemas/src/context-map.ts")

# Arquivos de PLATAFORMA que escrevem no hash de contexto. Lista explícita e não
# varredura: `hset` sobre outros hashes (instância, resume_tokens, admissão) não
# é escrita de ContextStore, e incluí-los inflaria o número em silêncio.
ESCRITORES = [
    "packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py",
    "packages/channel-gateway/src/plughub_channel_gateway/adapters/webhook.py",
    "packages/routing-engine/src/plughub_routing/main.py",
    "packages/ai-gateway/src/plughub_ai_gateway/sentiment_emitter.py",
    "packages/ai-gateway/src/plughub_ai_gateway/copilot_emitter.py",
    "packages/evaluation-api/src/plughub_evaluation_api/router.py",
    "packages/mcp-server-plughub/src/tools/bpm.ts",
    "packages/mcp-server-plughub/src/tools/session.ts",
    "packages/mcp-server-plughub/src/server.ts",
]

# CNS-11 — `core` entrou no alfabeto. Sem ele o censo le os escritores de plataforma
# JA migrados e nao ve nada: em 2026-09-01 a primeira rodada pos-rename publicou
# "plataforma 0" sobre 35 escritas vivas. Instrumento que nao aprende o vocabulario
# novo nao mede menos — mede ERRADO, e para o lado tranquilizador.
LIT = re.compile(r"""["']((?:core|session|journey|caller|account|insight|approval)\.[a-zA-Z0-9_.]+)["']""")
JSONKEY = re.compile(r'"([a-zA-Z0-9_.]+)"\s*:')
# Marcas de ESCRITA na vizinhança do literal. Sem elas o mesmo literal conta como
# leitura — o arquivo escreve E lê os mesmos nomes.
MARCA_W = re.compile(r"ctx_writes\[|mapping\[|hset\(|\bmapping\b\s*[:=]|_write_ctx\(|writeContextTag")


# ── mapa vigente ─────────────────────────────────────────────────────────────
def ler_mapa_semente():
    """O mapa SEMENTE - `DEFAULT_CONTEXT_MAP` do fonte TS.

    (!) Isto NAO e o mapa que roda. O vivo (config-api) carrega as adicoes do tenant
    e e maior: medido em 2026-09-02, semente 77/98 contra vivo 97/119. Ler a semente e
    chamar o resultado de "nao cadastrado" publicou **18** onde a resposta era **2**.
    Os dois numeros estao certos para perguntas diferentes; o defeito era o relatorio
    nao dizer qual respondia. Ver `ler_mapa_vivo` e `main`.
    """
    src = io.open(MAPTS, encoding="utf-8").read()
    bloco = src[src.index("export const DEFAULT_CONTEXT_MAP"):]
    aliases, canonicas = {}, {}
    esc = dom = None
    # ⚠️ A folha do mapa pode ocupar MAIS DE UMA LINHA — um `legado` com dois
    # aliases quebra naturalmente. A versão line-based deste parser lia só a
    # primeira linha e descartava o resto do array em silêncio: em 2026-08-30 ele
    # publicou `80 aliases` contra os `82` que o oráculo da TS conta, e mostrou
    # `session.surveyed_*` como NÃO DECLARADO quando eram alias. Sub-contagem
    # ainda é mentira, mesmo quando erra para o lado do trabalho a mais.
    # Junta-se por SALDO DE CHAVES, não por heurística de sufixo.
    linhas, buf, saldo = [], "", 0
    for ln in bloco.split("\n"):
        t = ln.strip()
        if buf:
            buf += " " + t
            saldo += t.count("{") - t.count("}")
            if saldo <= 0:
                linhas.append(buf)
                buf, saldo = "", 0
            continue
        d = t.count("{") - t.count("}")
        if "tipo:" in t and d > 0:
            buf, saldo = t, d
            continue
        linhas.append(t)
    if buf:
        linhas.append(buf)

    for ln in linhas:
        s = ln.strip()
        if re.match(r"^(core|session|journey|customer):\s*\{", s):
            esc, dom = s.split(":")[0], None
            continue
        m = re.match(r"^([a-z_]+):\s*\{\s*$", s)
        if m and esc and "tipo:" not in ln:
            dom = m.group(1)
            continue
        m = re.match(r"^([a-z_0-9]+):\s*\{.*tipo:\s*\"([a-z_]+)\"", s)
        if m and esc and dom:
            canon = "%s.%s.%s" % (esc, dom, m.group(1))
            canonicas[canon] = m.group(2)
            if "legado:" in s:
                for a in re.findall(r"\"([a-zA-Z0-9_.]+)\"", s.split("legado:")[1]):
                    aliases[a] = canon
    return canonicas, aliases


# ── metade TENANT: as cinco superfícies de autoria ───────────────────────────
def extrair_de_doc(doc, arq="<doc>"):
    """Extrator sobre UM documento de skill. **É esta a função que o gate consome.**

    Existe separada de `censo_tenant()` porque a D9.2 previu que *"o gate consumirá este
    mesmo extrator"*, e porque o gêmeo TypeScript
    (`collectContextTagWrites`, em `@plughub/schemas/context-map.ts`) precisa de um lado
    Python comparável sobre a MESMA entrada. Duas implementações sem fixture comum é
    como nasce a cópia divergente que este arco inteiro persegue —
    `infra/test/probe_context_tag_extractor_parity.sh` é o mecanismo.

    Devolve `(escritas, leituras, dinamicas)`, cada `escritas[tag]` um conjunto de
    `(arquivo, superficie)`.

    ⚠️ Os nomes de SUPERFICIE sao CONTRATO, nao rotulo: o gemeo TS emite exatamente
    estas strings e o gate compara literalmente. Alinhados em 2026-09-02 —
    `"context_json (string)"` virou `"context_json"` e `"invoke X"` virou
    `"invoke.X"`. Divergencia de rotulo faria o gate acusar 100% das linhas, e a
    divergencia REAL sumiria no ruido.
    """
    escritas, leituras, dinamicas = defaultdict(set), defaultdict(set), []

    def anda(node, arq):
        if isinstance(node, dict):
            ct = node.get("context_tags")
            if isinstance(ct, dict):                       # (a) anotação
                for lado in ("outputs", "inputs"):
                    b = ct.get(lado)
                    if isinstance(b, dict):
                        for _d, ent in b.items():
                            tag = ent.get("tag") if isinstance(ent, dict) else ent
                            if not isinstance(tag, str):
                                dinamicas.append((arq, "context_tags." + lado, repr(ent)[:60]))
                                continue
                            escopo = ent.get("scope") if isinstance(ent, dict) else None
                            nome = ("segment.{segId}." + tag) if escopo == "segment" else tag
                            alvo = escritas if lado == "outputs" else leituras
                            alvo[nome].add((arq, "context_tags." + lado))
            tipo = node.get("type")
            if tipo in ("delegate", "collect") and isinstance(node.get("context"), dict):
                for k in node["context"]:                  # (b) prefixo composto no gateway
                    if not isinstance(k, str):
                        dinamicas.append((arq, tipo + ".context", repr(k)[:60]))
                        continue
                    escritas[k if k.startswith("session.") else "session." + k].add(
                        (arq, tipo + ".context"))
            sc = node.get("set_context")
            if isinstance(sc, dict):                       # (c) mention_commands
                for k in sc:
                    if isinstance(k, str):
                        escritas[k].add((arq, "mention.set_context"))
            inp = node.get("input") if isinstance(node.get("input"), dict) else {}
            cj = inp.get("context_json")
            if isinstance(cj, str):                        # (d) string JSON
                for k in JSONKEY.findall(cj):
                    escritas[k].add((arq, "context_json"))
            if node.get("tool") in ("context_set", "context_write"):   # (e) input.tag
                t = inp.get("tag")
                if isinstance(t, str) and "@" not in t and "$" not in t:
                    escritas[t].add((arq, "invoke." + node["tool"]))
                elif t is not None:
                    dinamicas.append((arq, "invoke." + str(node.get("tool")), repr(t)[:60]))
            for v in node.values():
                anda(v, arq)
        elif isinstance(node, list):
            for v in node:
                anda(v, arq)
        elif isinstance(node, str):
            for m in re.finditer(r"@ctx\.([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)*)", node):
                nome = m.group(1)
                (dinamicas.append((arq, "@ctx", nome)) if "{" in nome
                 else leituras[nome].add((arq, "@ctx")))

    anda(doc, arq)
    return escritas, leituras, dinamicas


def censo_tenant():
    """Varre `packages/skill-flow-engine/skills/` inteiro, mesclando por documento."""
    escritas, leituras, dinamicas = defaultdict(set), defaultdict(set), []
    for fn in sorted(os.listdir(SKILLS)):
        if not fn.endswith((".yaml", ".yml")):
            continue
        e, l, d = extrair_de_doc(
            yaml.safe_load(io.open(os.path.join(SKILLS, fn), encoding="utf-8").read()), fn)
        for k, v in e.items():
            escritas[k] |= v
        for k, v in l.items():
            leituras[k] |= v
        dinamicas.extend(d)
    return escritas, leituras, dinamicas


# ── metade PLATAFORMA ────────────────────────────────────────────────────────
def _fim_do_escopo(texto, ini):
    """Índice do fecho do bracket aberto em/depois de `ini`, ou None.

    Conta `()`, `[]` e `{}` juntos e PULA o conteúdo de strings — sem isso um
    literal com bracket dentro desequilibraria a contagem.
    """
    n = len(texto)
    i = ini
    while i < n and texto[i] not in "([{":
        if texto[i] == "\n":
            return None            # o bracket tem de abrir na MESMA linha da marca
        i += 1
    if i >= n:
        return None
    prof = 0
    aspas = None
    while i < n:
        c = texto[i]
        if aspas:
            if c == "\\":
                i += 2
                continue
            if c == aspas:
                aspas = None
        elif c in "\"'":
            aspas = c
        elif c in "([{":
            prof += 1
        elif c in ")]}":
            prof -= 1
            if prof == 0:
                return i
        i += 1
    return None


def _tags_do_texto(texto):
    """Nomes de tag escritos por `texto`, um por ocorrência de marca de escrita.

    Função PURA para que o autoteste possa exercê-la sem tocar o repositório —
    era exatamente o que faltava quando a janela de 6 linhas passou despercebida.
    """
    fora = []
    for marca in MARCA_W.finditer(texto):
        fim = _fim_do_escopo(texto, marca.end() - 1)
        if fim is None:
            # Marca sem bracket na própria linha: cai no comportamento mínimo (a
            # linha da marca), nunca em "ignora" — silêncio aqui seria o mesmo
            # defeito, com outra causa.
            fim = texto.find("\n", marca.end())
            fim = len(texto) if fim == -1 else fim
        for m in LIT.finditer(texto[marca.start():fim + 1]):
            fora.append(m.group(1))
    return fora


def autoteste():
    """Prova que o extrator não perde tag por DISTÂNCIA. Devolve 0 ou 1.

    ── Por que este autoteste existe ───────────────────────────────────────────

    Em 2026-09-03 o extrator usava uma janela de 6 linhas antes do literal. Um
    comentário escrito DENTRO de um `_write_ctx({...})` empurrou uma tag para fora
    da janela e ela sumiu do censo. O relatório não mudou de número porque a mesma
    tag é escrita num segundo sítio e o resultado é deduplicado por nome — ou seja,
    **a redundância mascarou o defeito**, e uma volta à janela passaria verde.

    Por isso a asserção é sobre um texto SINTÉTICO com a chave deliberadamente
    longe da marca: é a única forma de a regressão ficar vermelha sem depender de
    como o repositório real está escrito hoje.
    """
    LONGO = (
        'await _write_ctx(r, t, s, {\n'
        '    "core.workflow.primeira": a,\n'
        + "".join('    # comentario de enchimento linha %d\n' % i for i in range(12))
        + '    "core.workflow.distante": b,\n'
        '})\n'
    )
    achadas = set(_tags_do_texto(LONGO))
    erros = []
    if "core.workflow.primeira" not in achadas:
        erros.append("perdeu a tag ADJACENTE à marca")
    if "core.workflow.distante" not in achadas:
        erros.append("perdeu a tag DISTANTE da marca (a janela voltou?)")

    # Testemunha do outro lado: o que está FORA da chamada não pode contar.
    FORA = (
        'await _write_ctx(r, t, s, {"core.workflow.dentro": a})\n'
        '# "core.workflow.fora" aparece depois do fecho e NAO e escrita\n'
        'logger.info("core.workflow.fora")\n'
    )
    de_fora = set(_tags_do_texto(FORA))
    if "core.workflow.dentro" not in de_fora:
        erros.append("perdeu a tag de uma chamada de uma linha")
    if "core.workflow.fora" in de_fora:
        erros.append("contou como escrita algo FORA da chamada")

    # String com bracket dentro não pode desequilibrar a contagem.
    ASPAS = 'await _write_ctx(r, t, s, {"core.workflow.chave": "vaor com ) e ]", "core.workflow.depois": x})\n'
    com_aspas = set(_tags_do_texto(ASPAS))
    if "core.workflow.depois" not in com_aspas:
        erros.append("bracket dentro de string desequilibrou o escopo")

    if erros:
        print("AUTOTESTE REPROVADO:")
        for e in erros:
            print("   x %s" % e)
        return 1
    print("autoteste do extrator: OK (distancia, fronteira e aspas)")
    return 0


def censo_plataforma():
    """Tags escritas por CÓDIGO de plataforma, pelo ESCOPO da chamada de escrita.

    ⚠️ **Isto era uma JANELA de 6 linhas, e a janela apagava escritas em silêncio**
    (medido em 2026-09-03, ALW-11). A regra antiga era *"a marca de escrita aparece
    nas 6 linhas antes do literal"*; bastava um comentário no meio do dicionário — ou
    um dicionário com mais de seis chaves — para o literal cair fora do alcance e a
    escrita sumir do censo.

    Foi descoberto do pior jeito possível: escrevendo o comentário que documenta a
    remoção de `core.workflow.reviewer_id`, eu empurrei a irmã `round_echoed` para
    fora da janela naquele sítio. O relatório não mudou de número **porque a mesma tag
    é escrita num segundo sítio** e o resultado é deduplicado por nome — ou seja, o
    defeito estava mascarado por redundância, e teria continuado assim.

    A regra nova não tem distância: acha a marca, abre no primeiro bracket da linha e
    consome até ele fechar. O que estiver DENTRO da chamada conta; o que estiver fora,
    não. Comentário, quebra de linha e número de chaves deixam de importar.
    """
    escritas = defaultdict(set)
    for rel in ESCRITORES:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        texto = io.open(p, encoding="utf-8", errors="ignore").read()
        for nome in _tags_do_texto(texto):
            escritas[nome].add(os.path.basename(rel))
    return escritas


def ler_mapa_vivo(tenant, base):
    """O mapa VIVO, do config-api — o que o portão de publish vai consultar.

    **Levanta em qualquer falha; NUNCA devolve a semente.** Cair na semente em silêncio
    é exatamente o defeito que este conserto remove: os dois mapas respondem perguntas
    diferentes, e um relatório que troca um pelo outro sem dizer manda alguém cadastrar
    16 nomes que já estão cadastrados.
    """
    url = "%s/config/masking?tenant_id=%s" % (base.rstrip("/"), quote(tenant, safe=""))
    with urllib.request.urlopen(url, timeout=8) as r:
        body = json.loads(r.read().decode("utf-8"))
    raw = (body.get("entries") or {}).get("context_map")
    if raw is None:
        raise RuntimeError("config-api respondeu sem `entries.context_map`")
    mapa = raw if isinstance(raw, dict) else json.loads(raw)
    contexto = mapa.get("contexto")
    if not isinstance(contexto, dict):
        raise RuntimeError("mapa vivo sem nó `contexto`")
    canonicas, aliases = {}, {}
    for esc, doms in contexto.items():
        if not isinstance(doms, dict):
            continue
        for dom, campos in doms.items():
            if not isinstance(campos, dict):
                continue
            for campo, folha in campos.items():
                if not isinstance(folha, dict):
                    continue
                nome = "%s.%s.%s" % (esc, dom, campo)
                canonicas[nome] = folha.get("tipo") or "?"
                leg = folha.get("legado") or []
                if isinstance(leg, str):
                    leg = [leg]
                for a in leg:
                    if isinstance(a, str):
                        aliases[a] = nome
    return canonicas, aliases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tenant", default=os.environ.get("TENANT", "tenant_demo"))
    ap.add_argument("--config-api",
                    default=os.environ.get("PLUGHUB_CONFIG_API_URL", "http://localhost:3600"))
    ap.add_argument("--autoteste", action="store_true",
                    help="exercita o extrator contra texto sintetico e sai")
    ap.add_argument("--sem-vivo", action="store_true",
                    help="não consulta o config-api; a metade TENANT sai INCONCLUSIVA")
    args = ap.parse_args()

    if args.autoteste:
        return autoteste()

    sem_can, sem_ali = ler_mapa_semente()
    vivo_can = vivo_ali = None
    if args.sem_vivo:
        vivo_erro = "--sem-vivo"
    else:
        vivo_erro = None
        try:
            vivo_can, vivo_ali = ler_mapa_vivo(args.tenant, args.config_api)
        except Exception as exc:                            # noqa: BLE001
            vivo_erro = "%s: %s" % (type(exc).__name__, str(exc)[:120])

    tenant, leituras, dinamicas = censo_tenant()
    plat = censo_plataforma()

    def estado(n, can, ali):
        if n in ali:
            return "alias -> " + ali[n]
        if n in can:
            return "canonica (%s)" % can[n]
        if n.startswith("segment.{segId}."):
            return "FAMILIA dinamica"
        return "NAO DECLARADO"

    todos = defaultdict(lambda: {"orig": set(), "sup": set()})
    for n, v in tenant.items():
        todos[n]["orig"].add("tenant")
        todos[n]["sup"] |= {s for _a, s in v}
    for n, v in plat.items():
        todos[n]["orig"].add("plataforma")
        todos[n]["sup"] |= {"codigo:" + a for a in v}

    # ── A PARTIÇÃO, e por que ela não é cosmética ────────────────────────────
    #
    # Código de plataforma roda para TODO tenant, logo tem de estar na SEMENTE —
    # declarar só no mapa vivo de um tenant não cobre os outros. E nenhum portão de
    # publish o alcança: código não se publica.
    #
    # Skill autorada é julgada pelo mapa VIVO, que é o que o portão consulta.
    #
    # Nome escrito pelos DOIS cai no lado da PLATAFORMA: a exigência mais forte vence,
    # mesma forma do `resolve_scope` e do `core.fileMode`.
    #
    # Antes desta partição havia um número só, e ele misturava a metade descartável
    # (fixtures de teste) com a metade que carrega peso (código). O número grande
    # escondia o pequeno.
    plat_nomes = sorted(n for n in todos if "plataforma" in todos[n]["orig"])
    ten_nomes = sorted(n for n in todos
                       if "tenant" in todos[n]["orig"] and "plataforma" not in todos[n]["orig"])

    plat_nao = [n for n in plat_nomes if estado(n, sem_can, sem_ali) == "NAO DECLARADO"]
    ten_nao = (None if vivo_can is None
               else [n for n in ten_nomes if estado(n, vivo_can, vivo_ali) == "NAO DECLARADO"])

    ref_can, ref_ali = (vivo_can, vivo_ali) if vivo_can is not None else (sem_can, sem_ali)
    sem_escritor = sorted(set(leituras) - set(todos))

    if args.json:
        json.dump({
            "mapa_semente": {"canonicas": len(sem_can), "aliases": len(sem_ali)},
            "mapa_vivo": (None if vivo_can is None
                          else {"canonicas": len(vivo_can), "aliases": len(vivo_ali),
                                "tenant": args.tenant}),
            "mapa_vivo_erro": vivo_erro,
            "escritos": sorted(todos),
            "plataforma": {"escritos": plat_nomes, "nao_declarados": plat_nao,
                           "julgado_contra": "semente"},
            "tenant": {"escritos": ten_nomes, "nao_declarados": ten_nao,
                       "julgado_contra": (None if vivo_can is None else "vivo")},
            "lidos_sem_escritor": sem_escritor,
            "dinamicos": [list(d) for d in dinamicas],
        }, sys.stdout, ensure_ascii=False, indent=1)
        # ⚠️ Devolve o VEREDICTO, nunca `return` seco. Um `return` aqui daria
        # `sys.exit(None)` = 0, e o modo `--json` — justamente o que um CI usaria —
        # seria um portao que nao pode reprovar.
        return veredicto(plat_nao, ten_nao, dinamicas, vivo_erro)

    print("=" * 88)
    print("CENSO DE CADASTRO DO CONTEXTSTORE (D9) — %d nomes escritos" % len(todos))
    print("=" * 88)
    for n in sorted(todos):
        print("  %-46s %-11s %s" % (n[:46], "+".join(sorted(todos[n]["orig"]))[:11],
                                    estado(n, ref_can, ref_ali)))

    print("\nLIDOS SEM ESCRITOR CONHECIDO — %d" % len(sem_escritor))
    for n in sem_escritor:
        print("  %-46s %s" % (n, estado(n, ref_can, ref_ali)))

    print("\n" + "=" * 88)
    print("  mapa SEMENTE (schemas/context-map.ts) : %d canonicas · %d aliases"
          % (len(sem_can), len(sem_ali)))
    if vivo_can is None:
        print("  mapa VIVO    (config-api)             : INDISPONIVEL — %s" % vivo_erro)
    else:
        print("  mapa VIVO    (config-api, %-14s) : %d canonicas · %d aliases"
              % (args.tenant, len(vivo_can), len(vivo_ali)))
    print("  escritos                              : %d  (tenant %d · plataforma %d)"
          % (len(todos), len(tenant), len(plat)))
    print("  DINAMICOS                             : %d   <- a premissa da D9.2 vive deste zero"
          % len(dinamicas))
    for d in dinamicas:
        print("      %s" % (d,))

    print("\n" + "-" * 88)
    print("PLATAFORMA (código) — julgado contra a SEMENTE, porque roda para TODO tenant")
    print("-" * 88)
    print("  escritas: %d   NAO DECLARADAS: %d" % (len(plat_nomes), len(plat_nao)))
    for n in plat_nao:
        print("     x %-44s %s" % (n, " · ".join(sorted(todos[n]["sup"]))[:34]))
    if not plat_nao:
        print("     (nenhuma)")
    print("  ^ É esta a metade que CARREGA PESO: nenhum portão de publish a alcança, e")
    print("    uma tag `core.*` não semeada viola o namespace fechado da plataforma.")

    print("\n" + "-" * 88)
    print("TENANT (skill YAML) — julgado contra o mapa VIVO, que é o que o portão consulta")
    print("-" * 88)
    if ten_nao is None:
        print("  INCONCLUSIVO — sem o mapa vivo não há contra o que julgar (%s)." % vivo_erro)
        print("  NÃO caindo na semente de propósito: ela publicaria como não-declaradas")
        print("  tags que o portão aceita. Suba o config-api ou passe --config-api.")
    else:
        print("  escritas: %d   NAO DECLARADAS: %d" % (len(ten_nomes), len(ten_nao)))
        for n in ten_nao:
            print("     x %-44s %s" % (n, " · ".join(sorted(todos[n]["sup"]))[:34]))
        if not ten_nao:
            print("     (nenhuma)")
    print("  (!) PESO: os skills deste repositório são FIXTURES de teste, simplificados e")
    print("      em rotação com a evolução da plataforma. Um portão que recuse um deles")
    print("      está FUNCIONANDO, e o custo é zero. Não dimensione a decisão por este")
    print("      número — dimensione pela metade PLATAFORMA acima.")

    return veredicto(plat_nao, ten_nao, dinamicas, vivo_erro)


def veredicto(plat_nao, ten_nao, dinamicas, vivo_erro):
    """0 = verde · 1 = reprovado · 2 = inconclusivo (ALW-11, 2026-09-03).

    ── Por que as DUAS metades reprovam ────────────────────────────────────

    A metade PLATAFORMA é a que carrega peso: é código, roda para todo tenant, e
    **nenhum portão de publish a alcança** — o gate da V4 lê YAML de skill. Foi
    ela que entregou o achado que motivou esta promoção (`core.workflow.reviewer_id`,
    id de pessoa escrito `agents_only` sem tipo, ao lado de duas irmãs declaradas).

    A metade TENANT também reprova, e isso é o que o próprio relatório argumenta:
    *um portão que recuse um fixture está FUNCIONANDO, e o custo é zero*. Ela não é
    redundante com o gate de publish — aquele julga o ATO de publicar, este julga o
    ESTADO dos arquivos, e um YAML que nunca foi republicado escapa do primeiro.

    ── Linha de base: 0 e 0, medidas em 2026-09-03 ─────────────────────────

    O gate nasce verde porque o único achado foi consertado antes. Um gate que
    nasce vermelho ensina todo mundo a ignorá-lo.

    ── INCONCLUSIVO é ramo próprio ─────────────────────────────────────────

    Sem o mapa vivo a metade TENANT não tem contra o que ser julgada, e cair na
    semente publicaria como não-declaradas tags que o portão aceita. Verde por
    ausência de amostra é a família *teste que não pode reprovar*.

    ⚠️ **Dinâmicas são CONTADAS e nomeadas, nunca fatais.** Um nome composto em
    runtime escapa deste censo E do gate de publish, então zero é a premissa da
    D9.2 — mas reprovar exigiria saber a ORIGEM de cada dinâmica, e o extrator não
    a carrega hoje. Contar sem poder julgar é honesto; julgar sem poder distinguir
    seria o instrumento medindo a proposição vizinha outra vez.
    """
    print()
    if dinamicas:
        print('  (!) %d nome(s) DINAMICO(s) — fora do alcance deste censo e do gate '
              'de publish: %s' % (len(dinamicas), ', '.join(map(str, dinamicas[:6]))))

    if plat_nao or ten_nao:
        print('REPROVADO — plataforma %d, tenant %s nao declarada(s)'
              % (len(plat_nao), 'INCONCLUSIVO' if ten_nao is None else len(ten_nao)))
        return 1
    if ten_nao is None:
        print('INCONCLUSIVO — metade TENANT nao julgada (%s); plataforma limpa' % vivo_erro)
        return 2
    print('VERDE — nenhuma tag escrita fora do cadastro, nas duas metades')
    return 0


if __name__ == "__main__":
    sys.exit(main())
