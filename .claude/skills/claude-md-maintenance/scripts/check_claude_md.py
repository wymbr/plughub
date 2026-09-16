#!/usr/bin/env python3
"""
Confere o CLAUDE.md raiz, os CLAUDE.md de pacote e as skills de .claude/skills/ contra as
regras de manutenção (as regras das skills estão no bloco "skills", mais abaixo).

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


# ── skills ────────────────────────────────────────────────────────────────────
#
# ERRO     skill-nome      frontmatter sem `name`, ou `name` diferente da pasta
# ERRO     skill-descricao frontmatter sem `description` (é ela que faz a skill carregar)
# ERRO     skill-indice    skill que não está no índice do CLAUDE.md, ou índice citando skill inexistente
# ATENÇÃO  caminho-sumiu   caminho citado (`packages/…`, `infra/…`, …) que não existe mais
# ATENÇÃO  linha-alem      `arquivo:N` com N maior que o arquivo tem
#
# Caminho só é conferido se começa numa raiz conhecida; atalho declarado no próprio
# arquivo (linha com "`pui/` = `packages/platform-ui/`") é expandido. Placeholder
# (`<id>`, `*`, `{`, `…`) é ignorado. Nome sem diretório (`routes.tsx:12`) não é
# conferido: ambíguo, e adivinhar o arquivo seria medir outra coisa.

ROOTS = ("packages/", "infra/", "docs/", "scripts/", ".claude/")
RE_ALIAS = re.compile(r"`([\w.-]+/)`\s*=\s*`([\w./-]+/)`")
RE_PATH = re.compile(r"`([\w.][\w./@-]*?)(?::(\d+)(?:-\d+)?)?`")
CHECKED = {"caminhos": 0, "linhas": 0}
RE_INDEX_NAME = re.compile(r"`([a-z0-9]+(?:-[a-z0-9]+)*)`")   # `deployment` não tem hífen


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    fm: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def skill_index(claude_md: str) -> set[str]:
    """Nomes do parágrafo **Skills do projeto** do CLAUDE.md raiz."""
    start = claude_md.find("**Skills do projeto**")
    if start < 0:
        return set()
    end = claude_md.find("\n\n", start)
    para = claude_md[start:end if end > 0 else None]
    # Só a LISTA `a` · `b` · `c` conta. Qualquer outro `termo` do parágrafo (ex.: `name`)
    # não é nome de skill; parêntese dentro da lista (`(com scripts/…)`) é descartado antes.
    flat = re.sub(r"\([^()]*\)", "", para.replace("\n", " "))
    name = r"`[a-z0-9]+(?:-[a-z0-9]+)*`"
    names: set[str] = set()
    for chain in re.finditer(rf"{name}(?:\s*·\s*{name})+", flat):
        names |= {m.group(1) for m in RE_INDEX_NAME.finditer(chain.group(0))}
    return names


def check_skill_paths(text: str, rel: str, repo: Path) -> list[tuple[str, str, int, str]]:
    out = []
    aliases = {a: b for a, b in RE_ALIAS.findall(text)}
    in_code = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or RE_ALIAS.search(line):
            continue
        for m in RE_PATH.finditer(line):
            path, ln = m.group(1), m.group(2)
            for a, b in aliases.items():
                if path.startswith(a):
                    path = b + path[len(a):]
            if not path.startswith(ROOTS) or any(c in path for c in "<>*{}…"):
                continue
            CHECKED["caminhos"] += 1
            f = repo / path.rstrip("/")
            if not f.exists() and any(repo.glob(f"packages/*/src/{path}")) or \
               not f.exists() and any(repo.glob(f"packages/*/{path}")):
                continue   # atalho relativo a pacote (`infra/tool-guard.ts`), não caminho da raiz
            if not f.exists():
                out.append(("ATENÇÃO", "caminho-sumiu", i, f"{rel}: {path}"))
            elif ln and f.is_file():
                CHECKED["linhas"] += 1
                n = len(f.read_text(encoding="utf-8", errors="replace").splitlines())
                if int(ln) > n:
                    out.append(("ATENÇÃO", "linha-alem", i, f"{rel}: {path}:{ln} (arquivo tem {n})"))
    return out


def check_skills(repo: Path, claude_md: str) -> tuple[list, int]:
    base = repo / ".claude" / "skills"
    dirs = sorted(d for d in base.iterdir() if d.is_dir()) if base.exists() else []
    index = skill_index(claude_md)
    out = []
    for d in dirs:
        sk = d / "SKILL.md"
        rel = str(sk.relative_to(repo))
        if not sk.exists():
            out.append(("ERRO", "skill-nome", 0, f"{d.name}: pasta sem SKILL.md"))
            continue
        fm = _frontmatter(sk.read_text(encoding="utf-8"))
        if fm.get("name") != d.name:
            out.append(("ERRO", "skill-nome", 1, f"{rel}: name={fm.get('name')!r} ≠ pasta {d.name!r}"))
        if not fm.get("description"):
            out.append(("ERRO", "skill-descricao", 1, f"{rel}: sem description"))
        if d.name not in index:
            out.append(("ERRO", "skill-indice", 0, f"{d.name}: fora do índice **Skills do projeto** do CLAUDE.md"))
        for md in sorted(d.rglob("*.md")):
            out += check_skill_paths(md.read_text(encoding="utf-8"), str(md.relative_to(repo)), repo)
    for name in sorted(index - {d.name for d in dirs}):
        out.append(("ERRO", "skill-indice", 0, f"CLAUDE.md: índice cita `{name}`, que não existe em .claude/skills/"))
    return out, len(dirs)


def run(repo: Path) -> int:
    root = repo / "CLAUDE.md"
    if not root.exists():
        print(f"INCONCLUSIVO: {root} não existe — rode da raiz do repositório")
        return 2
    files = [root] + sorted(p for p in (repo / "packages").glob("*/CLAUDE.md"))
    findings = []
    for f in files:
        findings += check_text(f.read_text(encoding="utf-8"), f, repo, f == root)
    root_text = root.read_text(encoding="utf-8")
    n_lines = len(root_text.splitlines())
    print(f"arquivos: {len(files)} (raiz + {len(files) - 1} de pacote) · raiz: {n_lines}/{TARGET_LINES} linhas")
    skill_findings, n_skills = check_skills(repo, root_text)
    print(f"skills: {n_skills} · índice do CLAUDE.md: {len(skill_index(root_text))} · "
          f"caminhos conferidos: {CHECKED['caminhos']} (com :linha: {CHECKED['linhas']})")
    if n_skills == 0 or CHECKED["caminhos"] == 0:
        print("INCONCLUSIVO: nenhuma skill, ou nenhum caminho citado foi conferido")
        return 2
    findings += skill_findings
    for sev, rule, ln, msg in findings:
        print(f"  {sev:8s} {rule:15s} :{ln:<5d} {msg}")
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

        # skills: um repositório sintético com uma skill boa e casos ruins
        (repo / "infra").mkdir()
        (repo / "infra" / "tres.txt").write_text("a\nb\nc\n")
        (repo / "packages" / "p" / "src" / "infra").mkdir(parents=True)
        (repo / "packages" / "p" / "src" / "infra" / "guard.ts").write_text("x")
        sk = repo / ".claude" / "skills"

        def mk(name: str, fm_name: str, desc: str, body: str) -> None:
            (sk / name).mkdir(parents=True)
            (sk / name / "SKILL.md").write_text(
                f"---\nname: {fm_name}\ndescription: {desc}\n---\n{body}\n", encoding="utf-8")

        mk("simples", "simples", "nome sem hífen, como `deployment`", "")
        mk("boa-skill", "boa-skill", "faz x",
           "`infra/tres.txt:3` · `ab/tres.txt` com `ab/` = `infra/` · `packages/<id>/x` · `routes.tsx:99`\n"
           "`infra/guard.ts` (atalho relativo a pacote)\n"
           "```\n`infra/sumiu-no-bloco.txt`\n```")
        mk("nome-errado", "outro-nome", "faz y", "")
        mk("sem-descricao", "sem-descricao", "", "")
        mk("caminho-velho", "caminho-velho", "faz z", "`infra/sumiu.txt` · `infra/tres.txt:9`")
        idx = ("**Skills do projeto** (`.claude/skills/`) — o MÉTODO. `simples` · `boa-skill` (com "
               "`scripts/x.py`) · `nome-errado` · `sem-descricao` ·\n`caminho-velho` · `fantasma-skill`. "
               "Critério: `name` igual à pasta.\n\nfim")
        got, _ = check_skills(repo, idx)
        esperado = {
            "skill-nome (name ≠ pasta)": any(f[1] == "skill-nome" and "nome-errado" in f[3] for f in got),
            "skill-descricao": any(f[1] == "skill-descricao" and "sem-descricao" in f[3] for f in got),
            "skill-indice (índice cita inexistente)": any(f[1] == "skill-indice" and "fantasma-skill" in f[3] for f in got),
            "caminho-sumiu": any(f[1] == "caminho-sumiu" and "infra/sumiu.txt" in f[3] for f in got),
            "linha-alem": any(f[1] == "linha-alem" and "tres.txt:9" in f[3] for f in got),
            "limpo: skill boa (atalho declarado, atalho de pacote, placeholder, nome solto, bloco)":
                not any("boa-skill" in f[3] for f in got),
            "limpo: nome sem hífen no índice": not any("simples" in f[3] for f in got),
            "limpo: `termo` fora da lista (`name`) e parêntese na lista":
                not any("`name`" in f[3] or "scripts" in f[3] for f in got),
        }
        got2, _ = check_skills(repo, "**Skills do projeto** — `boa-skill`.\n")
        esperado["skill-indice (skill fora do índice)"] = any(
            f[1] == "skill-indice" and "caminho-velho" in f[3] for f in got2)
        for nome, ok in esperado.items():
            fails += not ok
            print(f"  {'ok ' if ok else 'FALHOU'} {nome}")
        print("SELFTEST", "VERDE" if not fails else f"VERMELHO ({fails})")
        return 1 if fails else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(run(Path.cwd()))
