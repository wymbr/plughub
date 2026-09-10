# -*- coding: utf-8 -*-
"""Roda `masked_field_echo` de UM modulo Python contra a tabela compartilhada.

Uso:  python _masked_field_echo_py.py <modulo.importavel> < casos.json

Imprime uma linha por caso: `<indice>\t<saida>`. Sai 2 se o modulo nao expuser a
funcao — ausencia tem de ser INCONCLUSIVO, nunca "as duas casas concordam".
"""
import importlib
import json
import sys


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("uso: _masked_field_echo_py.py <modulo>\n")
        return 2
    nome = sys.argv[1]
    try:
        mod = importlib.import_module(nome)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("IMPORT_FALHOU %s: %s\n" % (nome, e))
        return 2
    fn = getattr(mod, "masked_field_echo", None)
    if not callable(fn):
        sys.stderr.write("SEM_FUNCAO %s.masked_field_echo\n" % nome)
        return 2
    try:
        casos = json.load(sys.stdin)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("CASOS_INVALIDOS %s\n" % e)
        return 2
    for i, caso in enumerate(casos):
        sys.stdout.write("%d\t%s\n" % (i, fn(caso)))
    return 0


sys.exit(main())
