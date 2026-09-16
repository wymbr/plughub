#!/usr/bin/env python3
"""
scan_diff.py — varre SÓ as linhas ADICIONADAS de um diff atrás das regras objetivas do PlugHub.

Por que só o adicionado: o repositório carrega dívida antiga medida (175 `except: pass` em duas
linhas, 520 hex inline em .tsx em 2026-09-16). Um scan do repositório inteiro reportaria o legado
como achado novo e ensinaria a ignorá-lo. Aqui a pergunta é outra: *este diff acrescenta mais?*

Uso:
  python3 scan_diff.py                    # mudanças da árvore (staged + unstaged) contra HEAD
  python3 scan_diff.py --base main        # commits e árvore contra um ref (ex.: revisão de branch)
  python3 scan_diff.py --paths a.py b.ts  # restringe a caminhos (sessões paralelas na árvore)
  git diff X | python3 scan_diff.py --stdin
  python3 scan_diff.py --selftest         # planta cada violação e exige que seja pega

Níveis:
  ERRO     regra objetiva do CLAUDE.md violada; o diff não deveria entrar assim.
  ATENÇÃO  padrão que já produziu defeito aqui, mas exige julgamento (pode ser legítimo).

Saída: 0 nenhum ERRO · 1 algum ERRO · 2 INCONCLUSIVO (git ausente, ref inválido, diff vazio).
Arquivos NOVOS não rastreados entram por inteiro (sem `git add`, o `git diff` não os vê).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Added:
    path: str
    line_no: int
    text: str
    prev_added: str | None  # linha adicionada imediatamente anterior no mesmo hunk (ou None)


@dataclass
class Finding:
    level: str
    rule: str
    path: str
    line_no: int
    text: str
    why: str


# ── regras ────────────────────────────────────────────────────────────────────
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")
# `(?![\w{])`: `AS handle_time_ms{_A}` é alias com sufixo de f-string, não sombra (falso positivo
# medido em contacts_series.py, 2026-09-16).
CH_ALIAS = re.compile(r"\b(any|anyLast|min|max|sum|avg|argMax|argMin|uniqExact|groupArray)\(\s*([a-z_][a-z0-9_]*)\s*[,)].*?\bAS\s+\2(?![\w{])", re.I)
_CH_CACHE: dict[str, bool] = {}


def talks_clickhouse(p: str, repo: Path) -> bool:
    """A regra do alias é do ClickHouse; o Postgres aceita a mesma forma (dialog-api/db.py usa
    `max(deleted_at) AS deleted_at` legitimamente — falso positivo medido em 2026-09-16)."""
    if "analytics-api/" in p or "quality-export/" in p:
        return True
    if p not in _CH_CACHE:
        try:
            _CH_CACHE[p] = "clickhouse" in (repo / p).read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            _CH_CACHE[p] = False
    return _CH_CACHE[p]
EXCEPT_OPEN = re.compile(r"^\s*except\b[^:]*:\s*(#.*)?$")
EXCEPT_PASS_ONE = re.compile(r"^\s*except\b[^:]*:\s*pass\s*(#.*)?$")
PASS = re.compile(r"^\s*pass\s*(#.*)?$")
CATCH_EMPTY = re.compile(r"catch\s*(\([^)]*\))?\s*\{\s*\}")
CATCH_OPEN = re.compile(r"catch\s*(\([^)]*\))?\s*\{\s*$")
CLOSE_BRACE = re.compile(r"^\s*\}\s*$")
EXPORT_STAR = re.compile(r"^\s*export\s+\*\s+from\b")
XADD = re.compile(r"\.xadd\s*\(")
ZERO_DEFAULT = re.compile(r"(\?\?\s*0\b|\bor\s+0\b)")
IS_NONE = re.compile(r"\bis\s+None\b")
SCOPE_EMPTY = re.compile(r"\bif\s+not\s+(accessible_)?pools\b|\bif\s*\(\s*!\s*(accessible)?[pP]ools\b")
KAFKA_SEND = re.compile(r"\b(send_and_wait|send)\s*\(")
JSX_TEXT = re.compile(r">\s*[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ ,.!?'’-]{3,}\s*<")
COMMENT = re.compile(r"^\s*(#|//|\*|/\*)")
# Fonte que DECODIFICA ausência para "": Redis, config, env, JSON. Calibrado em 2026-09-16 contra
# commits reais: sem esta condição, `is None` disparou 23 vezes em dois commits, todas em
# `self._x is None` de ciclo de vida ou `assert … is None` de teste — nenhuma no caso da regra.
DECODE_SOURCE = re.compile(r"\b(hget|hgetall|hmget|get|getenv|smembers|lindex|_decode|decode|loads)\s*\(|environ\b|\braw\b")


def is_comment(t: str) -> bool:
    return bool(COMMENT.match(t))


def is_test_path(p: str) -> bool:
    """Teste e script de exercício: `except: pass` em `finally` de teardown é legítimo ali
    (8 de 8 ocorrências medidas em 2026-09-16 eram desconectar sala / desligar chamada)."""
    return ("/tests/" in p or "/__tests__/" in p or p.startswith("infra/test/")
            or ".test." in p or p.startswith("packages/e2e-tests/") or Path(p).name.startswith("test_"))


def check(a: Added, repo: Path) -> list[Finding]:
    f: list[Finding] = []
    p, t = a.path, a.text
    if p.startswith(".claude/"):
        return f  # skills e config do agente: este próprio arquivo carrega as violações como fixture
    ext = Path(p).suffix
    add = lambda lvl, rule, why: f.append(Finding(lvl, rule, p, a.line_no, t.strip(), why))

    test = is_test_path(p)
    if ext == ".py" and not is_comment(t):
        if not test and (EXCEPT_PASS_ONE.match(t) or (PASS.match(t) and a.prev_added and EXCEPT_OPEN.match(a.prev_added))):
            if "#" in t:
                add("ATENÇÃO", "except-pass", "silêncio declarado em comentário: confira se é mesmo estado final e não degradação")
            else:
                add("ERRO", "except-pass", "degradação muda: logue POR QUE degradou (CLAUDE.md § Postura)")
        if not test and IS_NONE.search(t) and (DECODE_SOURCE.search(t) or (a.prev_added and DECODE_SOURCE.search(a.prev_added))):
            add("ATENÇÃO", "is-none", "valor vindo de decoder (Redis, config, env): a fonte produz \"\" e não None — use `if not x`")
        if not test and ZERO_DEFAULT.search(t):
            add("ATENÇÃO", "zero-default", "ausência virando medição plausível? None = não medido")
        if SCOPE_EMPTY.search(t):
            add("ATENÇÃO", "scope-empty", "`[]` = NENHUM pool; `if not pools` como 'sem filtro' vira liberação geral")
        if KAFKA_SEND.search(t) and "key" not in t and ("producer" in t or "kafka" in t.lower()):
            add("ATENÇÃO", "kafka-no-key", "evento de objeto com ciclo de vida precisa de key= (ordem é por partição); confira se a chamada continua na linha seguinte")

    if ext in (".ts", ".tsx") and not is_comment(t):
        if not test and (CATCH_EMPTY.search(t) or (CLOSE_BRACE.match(t) and a.prev_added and CATCH_OPEN.search(a.prev_added))):
            add("ERRO", "catch-empty", "degradação muda; num caminho de segurança é pior que a falha")
        if EXPORT_STAR.match(t):
            add("ERRO", "export-star", "CLAUDE.md § What Never: sempre export nomeado")
        if XADD.search(t) and p.startswith("packages/mcp-server-plughub/") and not p.endswith("write-stream-entry.ts"):
            add("ERRO", "xadd-direct", "escrita no stream canônico só via writeStreamEntry()")
        if not test and ZERO_DEFAULT.search(t):
            add("ATENÇÃO", "zero-default", "`?? 0` converte ausência em medição e desarma a guarda do consumidor")
        if SCOPE_EMPTY.search(t):
            add("ATENÇÃO", "scope-empty", "`[]` = NENHUM pool; não trate lista vazia como 'sem filtro'")

    if ext == ".tsx" and p.startswith("packages/platform-ui/") and not is_comment(t):
        if HEX.search(t):
            add("ERRO", "inline-hex", "platform-ui usa tokens Tailwind, nunca hex inline")
        if JSX_TEXT.search(t) and "t(" not in t:
            add("ATENÇÃO", "jsx-literal", "texto visível sem t(); chave nas DUAS locales (en e pt-BR)")

    if ext in (".py", ".ts") and not test and not is_comment(t) and CH_ALIAS.search(t) and talks_clickhouse(p, repo):
        add("ERRO", "ch-alias-shadow", "alias de agregado com nome de coluna real: derruba a query (code 184) quando a coluna é usada no WHERE ou noutro agregado; sufixe _ref")

    return f


def structural(changed: set[str], repo: Path) -> list[Finding]:
    """Regras sobre o CONJUNTO de arquivos, não sobre linhas."""
    f: list[Finding] = []
    manifest = repo / "infra/test/gates.manifest"
    names = manifest.read_text(encoding="utf-8") if manifest.exists() else ""
    for p in sorted(changed):
        # Só o nível de cima de infra/test/, como o censo do probe_gates_manifest_coverage; e o nome
        # pode vir precedido de variáveis de ambiente (`ADMIN_EMAIL=… probe_x.sh`).
        if re.fullmatch(r"infra/test/[^/]+\.sh", p) and (repo / p).exists():
            base = Path(p).name
            if not re.search(rf"^(?:\S+=\S+\s+)*[!=?]?{re.escape(base)}\b", names, re.M):
                f.append(Finding("ERRO", "gate-sem-classe", p, 0, base,
                                 "todo .sh de infra/test/ precisa de classe no gates.manifest"))
    locales = [p for p in changed if "/locales/" in p and p.endswith(".json")]
    langs = {("en" if "/en/" in p else "pt-BR" if "/pt-BR/" in p else "?") for p in locales}
    if locales and langs != {"en", "pt-BR"}:
        f.append(Finding("ATENÇÃO", "i18n-uma-locale", locales[0], 0, ", ".join(sorted(langs)),
                         "chave nova vai nas DUAS locales; rode probe_i18n_duplicate_keys.sh"))
    if any(p.startswith("packages/schemas/src/") for p in changed):
        f.append(Finding("ATENÇÃO", "schema-mudou", "packages/schemas/src", 0, "",
                         "rebuild de TODO consumidor que valida o schema (skill deployment § 1)"))
    return f


# ── leitura do diff ───────────────────────────────────────────────────────────
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_diff(diff: str) -> tuple[list[Added], set[str]]:
    added: list[Added] = []
    changed: set[str] = set()
    path = None
    new_no = 0
    prev: str | None = None
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            tgt = raw[4:].strip()
            path = None if tgt == "/dev/null" else re.sub(r"^b/", "", tgt)
            if path:
                changed.add(path)
            prev = None
            continue
        if raw.startswith("--- ") or raw.startswith("diff --git"):
            continue
        m = HUNK.match(raw)
        if m:
            new_no = int(m.group(1))
            prev = None
            continue
        if path is None:
            continue
        if raw.startswith("+"):
            added.append(Added(path, new_no, raw[1:], prev))
            prev = raw[1:]
            new_no += 1
        elif raw.startswith("-"):
            continue
        else:
            prev = None
            new_no += 1
    return added, changed


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def collect(repo: Path, base: str, paths: list[str]) -> str:
    spec = ["--", *paths] if paths else []
    diff = git(repo, "diff", "-U1", base, *spec)
    others = git(repo, "ls-files", "--others", "--exclude-standard", *spec).split()
    for p in others:
        fp = repo / p
        try:
            body = fp.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lines = body.splitlines()
        diff += f"\n+++ b/{p}\n@@ -0,0 +1,{len(lines)} @@\n" + "\n".join("+" + l for l in lines) + "\n"
    return diff


def report(findings: list[Finding]) -> int:
    erros = [x for x in findings if x.level == "ERRO"]
    atenc = [x for x in findings if x.level != "ERRO"]
    for grupo, titulo in ((erros, "ERRO"), (atenc, "ATENÇÃO")):
        if not grupo:
            continue
        print(f"== {titulo} ({len(grupo)})")
        for x in grupo:
            loc = f"{x.path}:{x.line_no}" if x.line_no else x.path
            print(f"  [{x.rule}] {loc}\n      {x.text[:140]}\n      → {x.why}")
    print(f"\nRESULTADO: {len(erros)} ERRO · {len(atenc)} ATENÇÃO")
    return 1 if erros else 0


# ── autoteste: cada regra tem de disparar, e o diff limpo não pode disparar nada ──
SELFTEST = {
    "except-pass": ("packages/x/src/a.py", ["try:", "    f()", "except Exception:", "    pass"]),
    "catch-empty": ("packages/x/src/a.ts", ["try { f() } catch (e) {}"]),
    "export-star": ("packages/x/src/index.ts", ["export * from './a'"]),
    "xadd-direct": ("packages/mcp-server-plughub/src/tools/t.ts", ["await redis.xadd(key, '*', 'a', 'b')"]),
    "inline-hex": ("packages/platform-ui/src/m/C.tsx", ["<div style={{ color: '#1B4F8A' }} />"]),
    "ch-alias-shadow": ("packages/analytics-api/src/q.py", ["    SELECT any(pool_id) AS pool_id FROM segments FINAL"]),
    "is-none": ("packages/x/src/b.py", ["v = await redis.hget(key, 'first_queued_ms')", "if v is None:"]),
    "zero-default": ("packages/x/src/b.ts", ["const n = value ?? 0"]),
    "scope-empty": ("packages/x/src/c.py", ["if not pools:"]),
    "jsx-literal": ("packages/platform-ui/src/m/D.tsx", ["<span>Salvo com sucesso</span>"]),
}
# Controles NEGATIVOS: formas legítimas que a calibração de 2026-09-16 achou em commits reais.
CLEAN = [
    ("packages/x/src/ok.py", ["try:", "    f()", "except Exception as exc:",
                              "    logger.warning('f falhou: %s', exc)",
                              "if not x:", "    return None"]),
    ("packages/x/src/ciclo.py", ["if self._http is None:", "    self._http = make()"]),
    ("infra/test/_x_exercise.py", ["try:", "    await room.disconnect()", "except Exception:", "    pass"]),
    ("packages/x/src/plughub_x/tests/test_a.py", ["assert build() is None", "n = value or 0"]),
    ("packages/analytics-api/src/s.py", ["    avg(handle_time_ms) AS handle_time_ms{_A},",
                                         "    #: `avg(peak) AS peak` faz o alias SOMBREAR a coluna"]),
    ("packages/dialog-api/src/plughub_dialog_api/db.py", ["    max(deleted_at) AS deleted_at"]),
]


def selftest() -> int:
    falhas = 0
    for rule, (path, lines) in SELFTEST.items():
        diff = f"+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n" + "\n".join("+" + l for l in lines)
        added, _ = parse_diff(diff)
        got = {x.rule for a in added for x in check(a, Path("."))}
        ok = rule in got
        falhas += not ok
        print(f"  {'ok ' if ok else 'FALHA'} {rule:16s} disparou={sorted(got)}")
    for path, lines in CLEAN:
        diff = f"+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n" + "\n".join("+" + l for l in lines)
        added, _ = parse_diff(diff)
        got = [x.rule for a in added for x in check(a, Path("."))]
        ok = not got
        falhas += not ok
        print(f"  {'ok ' if ok else 'FALHA'} limpo {path:40s} disparou={got}")
    print(f"\nSELFTEST: {'VERDE' if falhas == 0 else f'VERMELHO ({falhas})'}")
    return 0 if falhas == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--paths", nargs="*", default=[])
    ap.add_argument("--stdin", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    try:
        repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
        diff = sys.stdin.read() if args.stdin else collect(repo, args.base, args.paths)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"INCONCLUSIVO: git falhou — {exc}")
        return 2
    added, changed = parse_diff(diff)
    if not changed:
        print("INCONCLUSIVO: diff vazio — nada a revisar (base certa? arquivos staged?)")
        return 2
    findings = [x for a in added for x in check(a, repo)] + structural(changed, repo)
    print(f"arquivos: {len(changed)} · linhas adicionadas: {len(added)} · base: {'stdin' if args.stdin else args.base}\n")
    return report(findings)


if __name__ == "__main__":
    sys.exit(main())
