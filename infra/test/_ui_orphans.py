#!/usr/bin/env python3
"""
_ui_orphans.py — alcançabilidade real de módulos do platform-ui.

Chamado pela seção B de `probe_report_surface.sh`.

POR QUE NÃO É `grep`
--------------------
A primeira versão desta checagem casava o BASENAME do arquivo. Medido em 2026-08-28,
durante a F0b, isso tem duas classes de erro — e as duas foram observadas na árvore:

  · FALSO NEGATIVO por colisão de basename. `modules/campaigns/CampaignsPage.tsx`
    estava órfão de verdade e passou despercebido porque `modules/evaluation/
    CampaignsPage.tsx` (viva, roteada) faz o nome aparecer no `routes.tsx`.
  · FALSO POSITIVO ao tentar consertar com caminho. `index.tsx` é alcançado por
    import de DIRETÓRIO (`@/modules/config-recursos`), e irmãos se importam por
    `./Base` — nenhum dos dois casa com `dir/Base`.

Trocar uma classe de erro pela outra não é conserto. O que decide alcançabilidade é
**resolver o especificador de import**, que é o que este arquivo faz: para cada
`from '…'`/`import('…')` encontrado, resolve alias `@/` → `src/`, relativo → caminho,
e tenta as extensões e o `/index` na ordem do bundler.

LIMITE DECLARADO: import dinâmico com caminho montado em runtime não é resolvível
estaticamente e não existe hoje nesta árvore. Se passar a existir, este helper vai
acusar órfão onde não há — e o lugar de registrar isso é a lista de exceção do probe,
nunca afrouxar a checagem.

Uso:  python3 _ui_orphans.py <src_dir>
Saída: um caminho relativo por linha (órfãos), ordenado. Exit 0 sempre — quem julga
       é o probe, comparando com a linha de base declarada.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# `from '…'` · `import '…'` · `import('…')` · `require('…')`
_SPEC = re.compile(
    r"""(?:from|import|require)\s*\(?\s*['"]([^'"]+)['"]""",
    re.MULTILINE,
)
_EXTS = (".tsx", ".ts", ".jsx", ".js")


def resolve(spec: str, importer: Path, src: Path) -> Path | None:
    """Especificador → arquivo, na ordem em que o bundler tentaria."""
    if spec.startswith("@/"):
        base = src / spec[2:]
    elif spec.startswith("."):
        base = (importer.parent / spec).resolve()
    else:
        return None  # pacote de node_modules — fora do grafo local

    for ext in _EXTS:
        cand = base.with_suffix(base.suffix + ext) if base.suffix == "" else Path(str(base) + ext)
        if cand.is_file():
            return cand
    if base.is_file():
        return base
    for ext in _EXTS:
        idx = base / f"index{ext}"
        if idx.is_file():
            return idx
    return None


def main() -> int:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "packages/platform-ui/src").resolve()
    if not src.is_dir():
        print(f"src inexistente: {src}", file=sys.stderr)
        return 2

    files = [p for p in src.rglob("*") if p.suffix in _EXTS and p.is_file()]

    imported: set[Path] = set()
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for spec in _SPEC.findall(text):
            target = resolve(spec, f, src)
            if target is not None and target != f:
                imported.add(target)

    orphans = sorted(
        str(p.relative_to(src)).replace("\\", "/")
        for p in files
        if p.parts[len(src.parts)] == "modules" and p not in imported
    )
    for o in orphans:
        print(o)
    return 0


if __name__ == "__main__":
    sys.exit(main())
