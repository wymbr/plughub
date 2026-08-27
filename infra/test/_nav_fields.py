#!/usr/bin/env python3
"""
_nav_fields.py — extrai do `Sidebar.tsx` o campo ABAC de cada entrada de menu.

Usado por `probe_nav_backend_field_agreement.sh`. Existe como arquivo proprio (e nao
como heredoc dentro do probe) porque a regra de associacao label->abac ja custou duas
correcoes; ter uma casa unica evita que a terceira nasca numa copia.

Uso:  _nav_fields.py <caminho do Sidebar.tsx> <nav.key> [nav.key ...]
Saida (uma linha por chave pedida):
        <campo>:<nav.key>          quando ha regra `abac:` com module 'config'
        SEMREGRA:<nav.key>         quando nao ha, ou nao e do modulo 'config'
"""
import re
import sys


def campos_por_label(src: str) -> dict[str, str]:
    """label -> campo, para regras `abac:` do modulo `config`.

    ⚠️ A associacao e do `abac:` para o label ANTERIOR mais proximo, nunca do label
    para o `abac:` seguinte. Para a frente, um `.*?` com re.S atravessa a fronteira da
    entrada; e restringir a "sem outro label no meio" ainda falha em GRUPO com filhos,
    onde o label do grupo vem antes e absorve o `abac:` do primeiro filho.
    """
    out: dict[str, str] = {}
    for m in re.finditer(r"abac:\s*\{([^}]*)\}", src):
        corpo = m.group(1)
        antes = src[: m.start()]
        lab = None
        for lm in re.finditer(r"label:\s*t\('([^']+)'\)", antes):
            lab = lm.group(1)
        mod = re.search(r"module:\s*'([^']+)'", corpo)
        fld = re.search(r"field:\s*'([^']+)'", corpo)
        if lab and mod and fld and mod.group(1) == "config":
            out[lab] = fld.group(1)
    return out


def main() -> int:
    if len(sys.argv) < 3:
        print("uso: _nav_fields.py <Sidebar.tsx> <nav.key> [...]", file=sys.stderr)
        return 2
    try:
        src = open(sys.argv[1], encoding="utf-8").read()
    except OSError as exc:
        print("INCONCLUSIVO: %s" % exc, file=sys.stderr)
        return 2
    mapa = campos_por_label(src)
    if not mapa:
        # Zero regras extraidas quase certamente significa que o regex parou de casar,
        # nao que o menu ficou sem ABAC. Sair vazio faria o probe medir nada e passar.
        print("INCONCLUSIVO: nenhuma regra abac de modulo 'config' extraida do Sidebar",
              file=sys.stderr)
        return 2
    for chave in sys.argv[2:]:
        campo = mapa.get(chave)
        print("%s:%s" % (campo, chave) if campo else "SEMREGRA:%s" % chave)
    return 0


if __name__ == "__main__":
    sys.exit(main())
