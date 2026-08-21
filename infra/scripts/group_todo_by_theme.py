#!/usr/bin/env python3
"""
group_todo_by_theme.py — reagrupa as seções do TODO.md por TEMA, sem perder nenhuma.

POR QUE EXISTE. O TODO cresceu para ~78 seções em ordem cronológica de descoberta, que é a
ordem em que os itens NASCEM e não a ordem em que alguém os LÊ. Quem procura "o que falta em
qualidade" varre o arquivo inteiro. Reagrupar por tema é o mesmo movimento que a poda: o
arquivo tem de refletir o trabalho, não o histórico de quando cada coisa apareceu.

NÃO REESCREVE CONTEÚDO. Cada seção é movida BYTE A BYTE. O script só reordena e insere
cabeçalhos de tema. Isso é deliberado: uma reorganização que também edita texto não é
auditável no diff, e o custo de errar num arquivo de 4 mil linhas é perder um item sem que
ninguém note — o mesmo modo de falha que a poda de 2026-08-26 quase produziu (8 de 10 seções
tinham resíduo vivo enterrado no corpo).

TRÊS ALARMES, todos ALTOS. A regra da casa é que degradação nunca é silenciosa:
  1. seção do arquivo que NENHUM padrão reivindica  → vai para "Sem tema atribuído" E é listada
  2. padrão declarado que não casa com seção alguma → listado ("título mudou ou foi podado?")
  3. padrão que casa com MAIS DE UMA seção          → ERRO, aborta sem gravar
O nº 3 aborta porque é o único que causa perda: duas seções reivindicadas pelo mesmo padrão
seriam emitidas duas vezes ou nenhuma, dependendo da ordem. Alarme que não impede o dano é
decoração.

IDEMPOTENTE. Cabeçalhos de tema gerados por uma execução anterior são reconhecidos pelo
marcador e DESCARTADOS antes do reagrupamento — rodar duas vezes dá o mesmo arquivo, não um
arquivo com temas aninhados.

INTERAÇÃO COM `prune_todo_closed.py`. Os cabeçalhos de tema são `## `, então o podador os
enxerga como seções. Isso é inofensivo: ele só remove o que casa com a lista `CLOSED`, e
nenhum tema casa. Um tema que fique sem itens depois de uma poda vira um cabeçalho órfão —
rodar este script de novo o remove.

Uso:
    python3 infra/scripts/group_todo_by_theme.py            # dry-run
    python3 infra/scripts/group_todo_by_theme.py --apply
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

TODO = Path("TODO.md")

# Marcador que identifica cabeçalho GERADO por este script (para poder descartá-lo e reagrupar).
THEME_MARK = "📂 TEMA · "

# Seções fixadas no topo, NESTA ordem, fora dos temas. São as que se leem ANTES de escolher
# trabalho: a direção vigente, o veredicto do último reexame, o que foi resgatado da poda e as
# armadilhas de método. Não são "um tema" — são o preâmbulo operacional do arquivo.
PINNED = [
    "⚠️ Direção REVERTIDA em 2026-08-18",
    "Reexame dos 9 em `Escopo reduzido`",
    "Resíduos resgatados da poda de 2026-08-26",
    "⚠️ Erros de método que se repetem",
]

# (título do tema, [substrings que identificam o cabeçalho `## ` de cada seção])
# A ordem dos temas é a ordem de leitura; a ordem DENTRO do tema é a do arquivo original.
THEMES: list[tuple[str, list[str]]] = [
    ("Direção e frentes abertas", [
        "Frentes abertas pela reversão",
        "Itens do levantamento de n8n que SOBREVIVEM",
    ]),
    ("Qualidade e Avaliação", [
        "Frente 2 — Avaliação campaign-driven",
        "G-PROBE — Auth ABAC/serviço",
        "Cenário e2e 28 falha por config",
        "Isolamento do substrato por `origin`",
        "`sequence_index` apagado pelo `participant_left`",
    ]),
    ("Sessão, Segmento e Journey", [
        "`session:{id}:meta` — o problema não é",
        "~~Segmento que nunca fecha~~ — CONSERTADO",
        "Segmento que nunca fecha — `participant_left` não publicado",
        "Ler um processo = ver seus CONTATOS",
        "Modelo journey/session/segment",
        "Journey (retorno) — modelo de 3 níveis",
        "Workflow trace é assimétrico na proveniência",
        "15 `session_id` existem em `segments`",
        "Volume de sessões inexplicado — +167 contatos",
        "G7 — Decoupling segment-end × contact-close",
    ]),
    ("Wrap-up, fila humana e trabalho", [
        "I5 — encerramento de trabalho author-bound",
        "Wrap-up unificado — resíduos após a Phase 2",
        "Wrap-up como fonte de dados",
        "Visibilidade seletiva da sessão de wrap-up",
        "Detach de hooks de finalização",
        "Fila de trabalho humano / dispatch pull",
    ]),
    ("Roteamento e Capacidade", [
        "Capacidade, licenças e isolamento entre pools",
        "Costura única de aquisição",
        "Webhook pools — throttle de downstream",
        "`fila_humano` está declarado `agent_kind: ai`",
        "Posição na fila — resíduos",
    ]),
    ("Borda, Webhook e Workflow", [
        "Autenticação de endpoint webhook",
        "Remoção física do legado de workflow por token",
        "`source` do resume é asserido pelo CLIENTE",
        "Porta externa de resume × posse do item de pull",
        "Deploy de skills — cleanup de campos órfãos",
    ]),
    ("Telefonia e Voz", [
        "Telefonia — DOIS arcos, não um",
        "`voice.py` chama ~~dois~~ **CINCO** métodos",
        "Masking — Bloco 3: Channel Gateway TTS",
    ]),
    ("Segurança, LGPD e Masking", [
        "Masking — 5 mecanismos distintos",
        "As chamadas de domínio do agente NATIVO",
        "Auditoria MCP sem STORE",
        "Audit LGPD — Fases Pendentes",
        "Arco de Segurança — Pool-scoping em relatórios",
        "Hardening de Auth — postura de sessão do Console",
        "Agent Principal — identidade de máquina",
    ]),
    ("Identidade, Outbound e Surveys", [
        "Resolvedor de Identidade — próximos passos",
        "OTP produção + primitivo de diálogo genérico",
        "Customer Surveys — estado as-built",
        "Scheduler / Outbound — resíduos",
        "Histórico de contatos do cliente — backlog pós-H5",
        "Business in Any Media",
    ]),
    ("Analytics e UI", [
        "Relatórios analíticos — Agentes e Pools",
        "Eventos — três superfícies para duas ideias",
        "Dashboards — cobertura de catálogo",
        "Tabela construída como duas grids irmãs",
        "Analytics — revisar workarounds pré-`row_version`",
        "O adapter de whatsapp publica o `phone_number_id`",
    ]),
    ("Config, Registry e provisionamento", [
        "Frente 3 — Revisão de config / eliminar seeds",
        "Agent-registry — unificar binding skill↔pool",
        "Seeds escrevem substrato de produção sem carimbar `origin`",
        "Prontidão de provisionamento",
        "Tópicos Kafka órfãos",
    ]),
    ("Infra de teste, build e observabilidade", [
        "`npx vitest` no host mede `@plughub/schemas`",
        "`bpm.test.ts` assere o comportamento PRÉ-endurecimento",
        "Seis serviços rodam SEM logging configurado",
        "`docker compose build` não pega arquivo NOVO",
        "Fixtures do e2e ainda falam AgentType",
        "Dois pacotes fósseis",
        "Subida automática falhou uma vez",
        "Arc 19 — cleanup residual de infra",
        "Record/Replay Harness",
    ]),
    ("Metering e Pricing", [
        "Usage Metering — Channel Gateway Adapters",
        "Pricing Module — Integração metering × pricing",
    ]),
    ("Defeitos e follow-ups avulsos", [
        "Resíduos do conserto da exibição de sentimento",
        "Auditar `duration_ms` × `handle_time_ms`",
        "Delegate v2 — itens restantes",
    ]),
    ("Referência — não é trabalho a fazer", [
        "Histórico da investigação — 4 hipóteses eliminadas",
    ]),
]

UNASSIGNED_TITLE = "Sem tema atribuído — classificar"


def split_sections(lines: list[str]) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """(preâmbulo, [(cabeçalho, linhas)]) — preserva o texto antes do 1º `## `."""
    preamble: list[str] = []
    out: list[tuple[str, list[str]]] = []
    cur_head: str | None = None
    cur: list[str] = []
    for ln in lines:
        if ln.startswith("## "):
            if cur_head is None:
                preamble = cur
            else:
                out.append((cur_head, cur))
            cur_head, cur = ln.rstrip("\n"), [ln]
        else:
            cur.append(ln)
    if cur_head is None:
        return cur, []
    out.append((cur_head, cur))
    return preamble, out


def main() -> int:
    apply = "--apply" in sys.argv
    if not TODO.exists():
        print(f"❌ {TODO} não encontrado — rode da raiz do repositório.")
        return 2

    lines = TODO.read_text(encoding="utf-8").splitlines(keepends=True)
    preamble, sections = split_sections(lines)

    # Idempotência: descarta cabeçalhos de tema de execuções anteriores.
    sections = [(h, b) for h, b in sections if THEME_MARK not in h]

    print(f"── TODO.md: {len(lines)} linhas, {len(sections)} seções (fora cabeçalhos de tema) ──\n")

    # ── Alarme 3 (aborta): padrão ambíguo ────────────────────────────────────────────────
    all_pats = PINNED + [p for _, pats in THEMES for p in pats]
    fatal = False
    for pat in all_pats:
        hits = [h for h, _ in sections if pat in h]
        if len(hits) > 1:
            fatal = True
            print(f"  ❌ AMBÍGUO: {pat!r} casa com {len(hits)} seções:")
            for h in hits:
                print(f"        {h[:88]}")
    if fatal:
        print("\n  Padrão ambíguo causaria PERDA de seção. Nada foi gravado. "
              "Torne o padrão mais específico.")
        return 1

    claimed: set[str] = set()
    ordered: list[tuple[str, list[tuple[str, list[str]]]]] = []

    pinned_secs: list[tuple[str, list[str]]] = []
    for pat in PINNED:
        for h, b in sections:
            if pat in h and h not in claimed:
                pinned_secs.append((h, b))
                claimed.add(h)

    for title, pats in THEMES:
        bucket: list[tuple[str, list[str]]] = []
        for h, b in sections:                     # ordem do ARQUIVO dentro do tema
            if h in claimed:
                continue
            if any(p in h for p in pats):
                bucket.append((h, b))
                claimed.add(h)
        ordered.append((title, bucket))

    # ── Alarme 1: seção que ninguém reivindicou ──────────────────────────────────────────
    orphans = [(h, b) for h, b in sections if h not in claimed]

    # ── Alarme 2: padrão que não casou ───────────────────────────────────────────────────
    for pat in all_pats:
        if not any(pat in h for h, _ in sections):
            print(f"  ⚠️  PADRÃO SEM SEÇÃO: {pat!r} — título mudou ou foi podado?")

    print(f"\n  fixadas no topo: {len(pinned_secs)}")
    for title, bucket in ordered:
        print(f"  {len(bucket):3d} seções  {title}")
    if orphans:
        print(f"\n  ⚠️  {len(orphans)} SEÇÃO(ÕES) SEM TEMA — vão para '{UNASSIGNED_TITLE}':")
        for h, _ in orphans:
            print(f"        {h[:88]}")

    # ── Montagem ────────────────────────────────────────────────────────────────────────
    out: list[str] = list(preamble)
    for _, body in pinned_secs:
        out.extend(body)
    for title, bucket in ordered:
        if not bucket:
            continue                              # tema vazio não vira cabeçalho órfão
        # Duas linhas em DOIS elementos, não um: `len(out)` conta elementos, e um elemento com
        # "\n\n" faria o relatório dizer "+15" para 30 linhas físicas. Contador que subconta é a
        # mesma família de defeito que este arquivo existe para não produzir.
        out.append(f"## {THEME_MARK}{title}\n")
        out.append("\n")
        for _, body in bucket:
            out.extend(body)
    if orphans:
        out.append(f"## {THEME_MARK}{UNASSIGNED_TITLE}\n")
        out.append("\n")
        for _, body in orphans:
            out.extend(body)

    total_in = sum(len(b) for _, b in sections) + len(preamble)
    total_out = len(out)
    added = total_out - total_in                  # só cabeçalhos de tema
    print(f"\n  linhas antes: {total_in}   depois: {total_out}   "
          f"(+{added} de cabeçalho de tema)")
    if sum(len(b) for _, b in sections) != sum(
            len(b) for _, b in pinned_secs) + sum(
            len(b) for _, bk in ordered for _, b in bk) + sum(len(b) for _, b in orphans):
        print("  ❌ CONFERÊNCIA DE LINHAS FALHOU — nada gravado.")
        return 1
    print("  ✅ conferência: toda linha de seção foi reemitida exatamente uma vez.")

    if not apply:
        print("\n  DRY-RUN — nada foi gravado. Use --apply para aplicar.")
        return 0

    shutil.copy(TODO, TODO.with_suffix(".md.bak"))
    TODO.write_text("".join(out), encoding="utf-8")
    print(f"\n  ✅ aplicado. Backup em {TODO.with_suffix('.md.bak')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
