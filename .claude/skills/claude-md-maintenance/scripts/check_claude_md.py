#!/usr/bin/env python3
"""
Confere o CLAUDE.md raiz e os CLAUDE.md de pacote contra as regras de manutenção.

Uso (de dentro do WSL, na raiz do repositório):
    python3 .claude/skills/claude-md-maintenance/scripts/check_claude_md.py
    python3 .claude/skills/claude-md-maintenance/scripts/check_claude_md.py --selftest

Só stdlib. Saída 0 = nenhum ERRO (ATENÇÃO não reprova); 1 = há ERRO; 2 = não mediu.

ERRO     orçamento     raiz acima do alvo de linhas
ERRO     link-quebrado link relativo para arquivo que não existe
ATENÇÃO  arco-longo    seção `## Arc …` acima de 20 linhas (o formato pede 15–20 + link)
ATENÇÃO  status-check  ✅ fora de bloco de código (status mora no CHANGELOG/done.md)
ATENÇÃO  historico     `task #N`, `testes X/Y`, `build N kB`
ATENÇÃO  contagem      número em prosa sobre coleção viva (`11 módulos`) sem "medido em"

O verificador mede FORMA. Se uma afirmação é VERDADEIRA ele não sabe dizer, e é
exatamente aí que o CLAUDE.md mais errou. Verde aqui não prova que o arquivo está certo.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

TARGET_LINES = 1750
ARC_MAX = 20
COLLECTIONS = (
    r"módulos|pacotes|skills|namespaces|tabelas|tópicos|serviços|pools|campos|"
    r"gates|probes|rotas|steps|tools|consumidores"
)
RE_COUNT = re.compile(rf"\b\d+\*{{0,2}} (?:{COLLECTIONS})\b")
RE_HIST = re.compile(r"task #\d+|\btestes? \d+/\d+|\bbuild \d+ ?kB", re.I)
RE_LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")


def check_text(text: str, path: Path, repo: Path, is_root: bool) -> list[tuple[str, str, int, str]]:
    out: list[tuple[str, str, int, str]] = []
    lines = text.splitlines()
    rel = path.relative_to(repo) if path.is_absolute() else path

    if is_root and len(lines) > TARGET_LINES:
        out.append(("ERRO", "orcamento", len(lines), f"{len(lines)} linhas > alvo {TARGET_LINES}"))

    in_code = False
    section, section_start, section_len = None, 0, 0

    def close_section() -> None:
        if section and section.startswith("Arc") and section_len > ARC_MAX:
            out.append(("ATENÇÃO", "arco-longo", section_start,
                        f"'{section[:50]}' tem {section_len} linhas (> {ARC_MAX})"))

    for i, line in enumerate(lines, 1):
        if line.startswith("## "):
            close_section()
            section, section_start, section_len = line[3:].strip(), i, 0
            continue
        section_len += 1
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        for m in RE_LINK.finditer(line):
            target = m.group(1)
            if target.startswith(("http:", "https:", "mailto:")):
                continue
            if not (path.parent / target).exists():
                out.append(("ERRO", "link-quebrado", i, target))
        if "✅" in line and not line.lstrip().startswith("|"):
            out.append(("ATENÇÃO", "status-check", i, line.strip()[:90]))
        if RE_HIST.search(line):
            out.append(("ATENÇÃO", "historico", i, line.strip()[:90]))
        m = RE_COUNT.search(line)
        if m and not re.search(r"[Mm]edid[oa]", line):
            out.append(("ATENÇÃO", "contagem", i, m.group(0)))
    close_section()
    return [(sev, rule, ln, f"{rel}: {msg}") for sev, rule, ln, msg in out]


def run(repo: Path) -> int:
    root = repo / "CLAUDE.md"
    if not root.exists():
        print(f"INCONCLUSIVO: {root} não existe — rode da raiz do repositório")
        return 2
    files = [root] + sorted(p for p in (repo / "packages").glob("*/CLAUDE.md"))
    findings = []
    for f in files:
        findings += check_text(f.read_text(encoding="utf-8"), f, repo, f == root)
    n_lines = len(root.read_text(encoding="utf-8").splitlines())
    print(f"arquivos: {len(files)} (raiz + {len(files) - 1} de pacote) · raiz: {n_lines}/{TARGET_LINES} linhas")
    for sev, rule, ln, msg in findings:
        print(f"  {sev:8s} {rule:14s} :{ln:<5d} {msg}")
    erros = sum(1 for f in findings if f[0] == "ERRO")
    print(f"ERRO={erros} ATENÇÃO={len(findings) - erros}")
    return 1 if erros else 0


def selftest() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        (repo / "docs").mkdir()
        (repo / "docs" / "ok.md").write_text("x")
        p = repo / "CLAUDE.md"
        arc = "\n".join(f"linha {n}" for n in range(ARC_MAX + 2))
        cases = {
            "link-quebrado": "[a](docs/nao-existe.md)",
            "status-check": "Fase A ✅ entregue",
            "historico": "fechou com testes 12/12",
            "contagem": "o catálogo tem 11 módulos",
            "arco-longo": f"## Arc 99 — teste\n{arc}\n## Fim",
        }
        clean = {
            "link-ok": "[a](docs/ok.md)",
            "check-em-tabela": "| Itens com ✅ | CHANGELOG |",
            "check-em-codigo": "```\n✅  route: /x\n```",
            "contagem-medida": "medido em 2026-09-16: 11 módulos",
            "arco-curto": "## Arc 98 — curto\numa linha\n## Fim",
        }
        fails = 0
        for rule, body in cases.items():
            got = {f[1] for f in check_text(body, p, repo, True)}
            ok = rule in got
            fails += not ok
            print(f"  {'ok ' if ok else 'FALHOU'} dispara {rule}")
        for name, body in clean.items():
            got = check_text(body, p, repo, True)
            ok = not got
            fails += not ok
            print(f"  {'ok ' if ok else 'FALHOU'} limpo   {name}{'' if ok else ' → ' + str(got)}")
        big = "\n".join("x" for _ in range(TARGET_LINES + 1))
        ok = any(f[1] == "orcamento" for f in check_text(big, p, repo, True))
        fails += not ok
        print(f"  {'ok ' if ok else 'FALHOU'} dispara orcamento")
        print("SELFTEST", "VERDE" if not fails else f"VERMELHO ({fails})")
        return 1 if fails else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(run(Path.cwd()))
