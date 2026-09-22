"""
_ui_color_scale_census.py — censo das classes de cor que NÃO GERAM CSS no platform-ui (PUI-03).

O `tailwind.config.ts` redefine famílias da paleta padrão do Tailwind (hoje `gray`, `red`,
`green`) como cor ÚNICA. Isso apaga a ESCALA da família: `bg-gray-900`, `text-red-600`,
`bg-green-400` deixam de existir no CSS construído, sem erro em lugar nenhum — o elemento
apenas fica sem a cor. Medido duas vezes: o *Save draft* do editor de DialogForms (2026-08-28)
e os controles de mídia do Console (2026-09-22, WCH-07), ambos branco no branco.

As famílias proibidas são DERIVADAS do config, não listadas aqui: se alguém redefinir `blue`
amanhã, `text-blue-500` passa a ser acusado sem ninguém lembrar de atualizar este arquivo.

Saída: 0 VERDE · 1 VERMELHO (lista arquivo:linha) · 2 INCONCLUSIVO (não mediu).
Entradas de mentira por env, para a bateria de mutação: UI_SRC, TW_CONFIG.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = pathlib.Path(os.environ.get("UI_SRC") or ROOT / "packages/platform-ui/src")
CFG = pathlib.Path(os.environ.get("TW_CONFIG") or ROOT / "packages/platform-ui/tailwind.config.ts")

# A paleta padrão do Tailwind 3 — as famílias que TÊM escala numerada para perder.
DEFAULT_FAMILIES = {
    "slate", "gray", "zinc", "neutral", "stone", "red", "orange", "amber", "yellow", "lime",
    "green", "emerald", "teal", "cyan", "sky", "blue", "indigo", "violet", "purple", "fuchsia",
    "pink", "rose",
}
PREFIXES = ("bg|text|border|ring|from|to|via|divide|outline|fill|stroke|placeholder|"
            "shadow|accent|decoration|caret|ring-offset|border-[trblxy]")


def overridden_families(cfg_text: str) -> set[str] | None:
    """Famílias da paleta padrão redefinidas como STRING (cor única) em `colors`. `None` se o
    bloco `colors` não foi achado — aí o censo não sabe o que procurar e não pode dizer verde."""
    m = re.search(r"\bcolors\s*:\s*\{", cfg_text)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(cfg_text) and depth:
        depth += {"{": 1, "}": -1}.get(cfg_text[i], 0)
        i += 1
    block = cfg_text[m.end():i - 1]
    found = set()
    for key, value in re.findall(r"""^\s*['"]?([A-Za-z-]+)['"]?\s*:\s*(['"{])""", block, re.M):
        if key in DEFAULT_FAMILIES and value in ("'", '"'):
            found.add(key)
    return found


def main() -> int:
    try:
        cfg_text = CFG.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"  INCONCL config ilegivel: {CFG} ({exc})")
        return 2
    fams = overridden_families(cfg_text)
    if fams is None:
        print(f"  INCONCL bloco `colors` nao achado em {CFG}")
        return 2
    files = [p for ext in ("*.ts", "*.tsx", "*.css") for p in SRC.rglob(ext)
             if "node_modules" not in p.parts]
    if not files:
        print(f"  INCONCL nenhum arquivo em {SRC} — SEM AMOSTRA")
        return 2
    print(f"  familias redefinidas como cor unica no config: {sorted(fams) or '(nenhuma)'}")
    print(f"  arquivos varridos: {len(files)}")
    if not fams:
        print("  VERDE — nenhuma familia da paleta padrao foi redefinida; nao ha escala perdida")
        return 0
    pat = re.compile(rf"(?<![\w-])(?:[a-z-]+:)*(?:{PREFIXES})-(?:{'|'.join(sorted(fams))})-\d{{2,3}}(?:/\d+)?(?![\w-])")
    hits = []
    for p in sorted(files):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in pat.finditer(line):
                hits.append(f"{p.relative_to(SRC).as_posix()}:{n}  {m.group(0)}")
    if hits:
        print(f"  VERMELHO — {len(hits)} classe(s) que NAO GERAM CSS (a familia virou cor unica no config):")
        for h in hits[:60]:
            print("    " + h)
        if len(hits) > 60:
            print(f"    … e mais {len(hits) - 60}")
        print("  Troque por token do tema (muted, muted-light, surface-alt, border, dark, red, red-light,")
        print("  red-text, green, green-light, green-text) — ver CHANGELOG 2026-09-22 (3).")
        return 1
    print("  VERDE — nenhuma classe numerada das familias redefinidas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
