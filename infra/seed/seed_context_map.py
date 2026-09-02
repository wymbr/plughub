#!/usr/bin/env python3
"""
seed_context_map.py — provisiona os DOMÍNIOS DE NEGÓCIO do tenant no mapa do
ContextStore (`masking.context_map` do config-api), VIA API OFICIAL — invariante
"provisioning only via official API": nada de UPDATE direto em `platform_config`.

POR QUE ESTE SEED EXISTE (ALW-12, 2026-09-02)
---------------------------------------------
A ALW-04 encolheu a SEMENTE da plataforma para só-plataforma, e isso está certo —
`cartao`, `portabilidade` e `reembolso` são vocabulário de quem usa. O que não
existia era a contraparte: há `infra/dialog/*.json` e `infra/registry/*.yaml`, e
não havia `infra/context-map/`. Os 20 campos de domínio de tenant do demo viviam
no store apenas porque alguém os escreveu pela API, uma vez.

Medido: contra a semente — que É o estado pós-`--wipe` — **17 das 52** escritas de
skill ficavam NÃO DECLARADAS. Com o portão de publish da V4 ligado o
`RegistrySyncer` as recusaria no boot, e ele **não bloqueia o startup**
(`registry_syncer.py:623`): a stack sobe, os skills ficam sem publicar, e o único
sinal é o contador `skills_errors`. É a regra do `CLAUDE.md` sobre ambiente que só
sobe porque já subiu antes.

O ESCOPO DA ESCRITA É GLOBAL, E ISSO É FORÇADO — NÃO PREFERÊNCIA
-----------------------------------------------------------------
Medido em 2026-09-02: `masking.context_map` tem UMA linha, `__global__`. Não há
override de `tenant_demo`, e criar um seria o defeito da ALW-06 de propósito —
**tenant vence global POR INTEIRO** (`LIMIT 1`), então a linha do tenant teria de
carregar o mapa completo e toda atualização futura da PLATAFORMA ficaria invisível
para ele, em silêncio. O preço aceito é o inverso e é menor: os domínios do demo
ficam no default global, visíveis a qualquer tenant. Quem conserta isso de verdade
é a ALW-06 (override por NÓ em vez de por chave), não este seed.

SÃO DUAS CONFERÊNCIAS, E ELAS NÃO SE SUBSTITUEM
------------------------------------------------
**Antes de escrever**: o escopo global e a leitura efetiva do tenant têm de ser
IGUAIS. Se divergirem existe override de tenant — que a API recusa desde a CNS-08
(422), logo veio de fora dela — e semear gravaria o conteúdo do tenant por cima da
plataforma. Medido: com um override injetado direto no banco, a versão sem esta
guarda derrubou o mapa global de 97 canônicas para 20.

**Depois de escrever**: os domínios têm de aparecer na leitura efetiva do tenant.
Um `PUT` global sobre chave com override de tenant responde **200 e não faz nada** —
medido em `masking.types` (14 × 13) no mesmo dia. Testar a pré-condição ("existe
override?") exigiria adivinhar a resposta pela borda; este seed **escreve e RELÊ**,
e reprova se os domínios não aparecerem na leitura efetiva do tenant. Verificar o
efeito cobre também as causas que ninguém previu.

PRECEDÊNCIA (seed-if-absent por DOMÍNIO, igual ao resto da casa)
-----------------------------------------------------------------
  domínio já presente  → NÃO TOCA (o store vence; edição pela UI sobrevive a rebuild)
  domínio ausente      → acrescenta
  RECONCILE=true       → o arquivo vence, domínio a domínio (nunca a chave inteira)

O grão é o DOMÍNIO e não a chave: `masking.context_map` é um valor só para o mapa
todo, então seed-if-absent no grão da chave nunca acrescentaria nada — a chave
sempre existe, semeada pela plataforma. Seria um seed que passa sem semear.

NADA É REMOVIDO, em nenhum modo. Campo que só existe no store fica; descartá-lo
seria mudança de política silenciosa, exatamente o que a D7 recusou no `--overwrite`.

O CATÁLOGO VEM ANTES DO MAPA (D8.3), e aqui isso é mecanismo
-------------------------------------------------------------
Todo `tipo` referenciado é conferido contra `masking.types` do config-api ANTES de
escrever. Tipo desconhecido faz o seed RECUSAR o arquivo inteiro — semear um tipo
que não existe grava política que ninguém decidiu, e a máscara resolveria `full`
por ausência, escondendo o campo sem que nada fique vermelho.

Uso:
  CONFIG_API_URL=http://config-api:3600 TENANT_ID=tenant_demo \
    python seed_context_map.py
  CONTEXT_MAP_SEED_RECONCILE=true python seed_context_map.py   # o arquivo vence
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CONFIG_API = os.environ.get("CONFIG_API_URL", "http://config-api:3600").rstrip("/")
MAPS_DIR = Path(os.environ.get("CONTEXT_MAP_DIR", "/context-map"))
TENANT = os.environ.get("TENANT_ID", "tenant_demo")
ADMIN_TOKEN = os.environ.get("CONFIG_ADMIN_TOKEN", "")
RECONCILE = os.environ.get("CONTEXT_MAP_SEED_RECONCILE", "").lower() in ("1", "true", "yes")
MAX_WAIT = int(os.environ.get("SEED_MAX_WAIT", "120"))

NS, KEY = "masking", "context_map"


# ── transporte ───────────────────────────────────────────────────────────────

def _req(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(CONFIG_API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if ADMIN_TOKEN:
        req.add_header("X-Admin-Token", ADMIN_TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"detail": raw[:300]}


def esperar_config_api() -> None:
    """O `depends_on: service_healthy` do compose já cobre isto; a espera é para
    execução MANUAL, onde não há orquestrador nenhum."""
    limite = time.time() + MAX_WAIT
    while time.time() < limite:
        try:
            with urllib.request.urlopen(CONFIG_API + "/v1/health", timeout=5) as r:
                if r.status == 200:
                    return
        except Exception:                                    # noqa: BLE001
            pass
        time.sleep(2)
    sys.exit("config-api não respondeu em %ss (%s) — nada foi semeado" % (MAX_WAIT, CONFIG_API))


# ── leitura ──────────────────────────────────────────────────────────────────

#: Tenant que NAO EXISTE, usado so para ler o escopo GLOBAL.
#:
#: O `GET /config/{ns}/{key}` resolve tenant -> global e nao aceita pedir um escopo;
#: um tenant sem linha propria devolve, por construcao, o valor global. E o unico
#: jeito de ler o global pela API OFICIAL, e ler o global e OBRIGATORIO — ver
#: `ler_mapa`.
SENTINELA_GLOBAL = "__context_map_seed_probe__"


def ler_mapa(tenant: str) -> dict:
    st, body = _req("GET", "/config/%s/%s?tenant_id=%s" % (NS, KEY, tenant))
    if st == 404:
        sys.exit("config-api não tem `%s.%s` nem global — a semente da PLATAFORMA "
                 "não rodou. Este seed ACRESCENTA a ela; sozinho ele publicaria um "
                 "mapa sem os domínios de plataforma." % (NS, KEY))
    if st != 200:
        sys.exit("GET %s.%s devolveu %s: %s" % (NS, KEY, st, body))
    v = (body or {}).get("value")
    if not isinstance(v, dict) or not isinstance(v.get("contexto"), dict):
        sys.exit("`%s.%s` sem nó `contexto` — forma inesperada, recusando" % (NS, KEY))
    return v


def ler_catalogo_tipos() -> set[str]:
    st, body = _req("GET", "/config/%s/types?tenant_id=%s" % (NS, TENANT))
    if st != 200:
        sys.exit("catálogo de tipos ilegível (%s) — a ordem é catálogo ANTES do mapa "
                 "(D8.3), então recusando em vez de semear às cegas" % st)
    tipos = ((body or {}).get("value") or {}).get("types") or []
    ids = {t.get("id") for t in tipos if isinstance(t, dict) and t.get("id")}
    if not ids:
        sys.exit("catálogo de tipos VAZIO — recusando")
    return ids


# ── aplicação ────────────────────────────────────────────────────────────────

def aplicar(arquivo: Path, vivo: dict, catalogo: set[str]) -> tuple[dict, list[str]]:
    """Devolve (mapa novo, linhas de log). Não escreve nada."""
    doc = json.loads(arquivo.read_text(encoding="utf-8"))
    novo_ctx = doc.get("contexto")
    if not isinstance(novo_ctx, dict):
        sys.exit("%s: sem nó `contexto`" % arquivo.name)

    # Catálogo antes do mapa — recusa o arquivo INTEIRO, não campo a campo: semear
    # metade de um domínio deixaria o skill escrevendo num nome declarado e noutro não.
    desconhecidos = sorted({
        folha.get("tipo")
        for doms in novo_ctx.values() if isinstance(doms, dict)
        for campos in doms.values() if isinstance(campos, dict)
        for folha in campos.values() if isinstance(folha, dict)
        if folha.get("tipo") not in catalogo
    } - {None})
    if desconhecidos:
        sys.exit("%s: tipo(s) fora do catálogo: %s — a ordem é catálogo ANTES do mapa "
                 "(D8.3). Crie o tipo primeiro." % (arquivo.name, ", ".join(desconhecidos)))

    resultado = json.loads(json.dumps(vivo))          # cópia profunda
    ctx = resultado["contexto"]
    log: list[str] = []

    for escopo, doms in sorted(novo_ctx.items()):
        if not isinstance(doms, dict):
            continue
        alvo = ctx.setdefault(escopo, {})
        for dom, campos in sorted(doms.items()):
            nome = "%s.%s" % (escopo, dom)
            if dom in alvo and not RECONCILE:
                log.append("  = %-28s JÁ PRESENTE — não tocado (o store vence)" % nome)
                continue
            if dom in alvo:
                log.append("  ~ %-28s RECONCILE — o arquivo vence (%d campos)"
                           % (nome, len(campos)))
            else:
                log.append("  + %-28s acrescentado (%d campos)" % (nome, len(campos)))
            alvo[dom] = campos
    return resultado, log


def conferir_efeito(esperados: list[str]) -> list[str]:
    """Relê o mapa EFETIVO do tenant e devolve os domínios que não chegaram lá.

    Esta é a conferência que importa: um PUT global sobre chave com override de
    tenant responde 200 e não faz nada. Ver o cabeçalho.
    """
    vivo = ler_mapa(TENANT)["contexto"]
    faltando = []
    for nome in esperados:
        escopo, _, dom = nome.partition(".")
        if dom not in (vivo.get(escopo) or {}):
            faltando.append(nome)
    return faltando


def main() -> int:
    esperar_config_api()

    arquivos = sorted(MAPS_DIR.glob("*.json")) if MAPS_DIR.is_dir() else []
    if not arquivos:
        print("nenhum arquivo em %s — nada a semear" % MAPS_DIR)
        return 0

    catalogo = ler_catalogo_tipos()

    # ── A BASE DA MESCLA É O GLOBAL, e isto foi um DEFEITO REAL ──────────────
    #
    # A primeira versão mesclava no mapa EFETIVO (resolvido por tenant) e escrevia
    # o resultado no GLOBAL. Enquanto os dois coincidem — que é o caso normal, e a
    # CNS-08 o garante pela API — nada acontece. Quando divergem, o `PUT` global
    # grava o conteúdo do TENANT por cima da plataforma.
    #
    # Não é hipótese: medido em 2026-09-02 com um override injetado direto no banco,
    # o mapa global caiu de 97 canônicas para 20 e a metade `core.*` desapareceu. A
    # conferência do efeito pegou o sintoma e reprovou — DEPOIS de já ter gravado.
    # Conferir o efeito não substitui escolher a base certa.
    global_ = ler_mapa(SENTINELA_GLOBAL)
    efetivo = ler_mapa(TENANT)
    if json.dumps(global_, sort_keys=True) != json.dumps(efetivo, sort_keys=True):
        sys.exit(
            "RECUSANDO: a leitura efetiva de %s DIVERGE do escopo global.\n"
            "  Existe override de `%s.%s` para este tenant — e a API NÃO permite criá-lo\n"
            "  (CNS-08 devolve 422), então ele veio de fora dela: escrita direta no banco,\n"
            "  migração, ou restauração de dump.\n"
            "  Semear agora gravaria o conteúdo do tenant por cima da plataforma. Remova o\n"
            "  override antes de continuar." % (TENANT, NS, KEY))

    print("context-map seed — tenant=%s reconcile=%s catálogo=%d tipos"
          % (TENANT, RECONCILE, len(catalogo)))

    acrescentados: list[str] = []
    for arq in arquivos:
        print("\n%s:" % arq.name)
        global_, log = aplicar(arq, global_, catalogo)
        for ln in log:
            print(ln)
            if ln.lstrip().startswith(("+", "~")):
                acrescentados.append(ln.split()[1])

    if not acrescentados:
        print("\nnada a acrescentar — o store já declara todos os domínios dos arquivos")
        return 0

    st, body = _req("PUT", "/config/%s/%s" % (NS, KEY), {
        "tenant_id": None,                     # GLOBAL — ver o cabeçalho, é forçado
        "value": global_,
        "description": ("Mapa do ContextStore. Domínios de plataforma da semente "
                        "(@plughub/schemas) + domínios de negócio de infra/context-map/."),
    })
    if st != 200:
        print("\nPUT recusado (%s): %s" % (st, body), file=sys.stderr)
        return 1

    faltando = conferir_efeito(acrescentados)
    if faltando:
        print("\nFALHOU A CONFERÊNCIA — o PUT respondeu 200 e os domínios NÃO chegaram "
              "à leitura efetiva de %s:" % TENANT, file=sys.stderr)
        for n in faltando:
            print("    x %s" % n, file=sys.stderr)
        print("  Causa provável: existe override de `%s.%s` para este tenant, e tenant "
              "vence global POR INTEIRO. Ver ALW-06." % (NS, KEY), file=sys.stderr)
        return 1

    print("\nOK — %d domínio(s) acrescentado(s) e CONFERIDO(s) na leitura efetiva de %s"
          % (len(acrescentados), TENANT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
