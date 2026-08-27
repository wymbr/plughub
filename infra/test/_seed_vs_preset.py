#!/usr/bin/env python3
"""
_seed_vs_preset.py — o seed do demo e o `role_defaults` do catalogo declaram o mesmo?

Duas declaracoes da mesma coisa (o que cada papel recebe) que ninguem compara viram
tres em seis meses. Este comparador e ESTATICO — le os dois ARQUIVOS — de proposito:
comparar com o usuario vivo ficaria vermelho a cada edicao legitima pela tela de
Acesso, e um gate que reprova por uso normal ensina a ignorar o vermelho.

Uso:  _seed_vs_preset.py <seed_auth.py> <modules.yaml>
Saida: 0 = iguais (uma linha de resumo) · 1 = divergem (uma linha por diferenca)
       2 = nao consegui comparar
"""
import ast
import sys

RANK = {"none": 0, "read_only": 1, "write_only": 1, "read_write": 2}


def demo_users(caminho: str) -> list[dict]:
    """Extrai o literal `DEMO_USERS` sem importar o modulo (que faria I/O de rede)."""
    arvore = ast.parse(open(caminho, encoding="utf-8").read())
    for no in arvore.body:
        if isinstance(no, ast.Assign):
            for alvo in no.targets:
                if isinstance(alvo, ast.Name) and alvo.id == "DEMO_USERS":
                    return ast.literal_eval(no.value)
    raise LookupError("DEMO_USERS nao encontrado em %s" % caminho)


def preset(doc: dict, papeis: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for mod in doc["modules"]:
        for campo, d in mod["permission_schema"].items():
            rd = d.get("role_defaults") or {}
            melhor = "none"
            for p in papeis:
                a = rd.get(p, "none")
                if RANK.get(a, 0) > RANK.get(melhor, 0):
                    melhor = a
            if melhor != "none":
                out["%s.%s" % (mod["module_id"], campo)] = melhor
    return out


def main() -> int:
    if len(sys.argv) != 3:
        print("uso: _seed_vs_preset.py <seed_auth.py> <modules.yaml>", file=sys.stderr)
        return 2
    try:
        import yaml
        usuarios = demo_users(sys.argv[1])
        doc = yaml.safe_load(open(sys.argv[2], encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print("%s" % exc)
        return 2

    if not usuarios:
        print("DEMO_USERS vazio — nada a comparar, e verde aqui seria vacuo")
        return 2

    linhas: list[str] = []
    conferidos = 0
    for u in usuarios:
        papeis = u.get("roles") or []
        seed = {"%s.%s" % (m, c): (v or {}).get("access")
                for m, fs in (u.get("module_config") or {}).items()
                for c, v in fs.items()}
        cat = preset(doc, papeis)
        conferidos += 1
        email = u.get("email", "?")
        for k in sorted(set(seed) - set(cat)):
            linhas.append("%s: seed da '%s' e o preset do papel %s NAO da"
                          % (email, k, papeis))
        for k in sorted(set(cat) - set(seed)):
            linhas.append("%s: preset do papel %s da '%s' e o seed NAO da"
                          % (email, papeis, k))
        for k in sorted(set(seed) & set(cat)):
            if seed[k] != cat[k]:
                linhas.append("%s: '%s' seed=%s preset=%s" % (email, k, seed[k], cat[k]))

    if linhas:
        for l in linhas:
            print(l)
        return 1
    print("os %d usuarios do seed batem exatamente com o preset dos seus papeis"
          % conferidos)
    return 0


if __name__ == "__main__":
    sys.exit(main())
