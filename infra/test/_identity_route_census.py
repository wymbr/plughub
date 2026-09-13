# -*- coding: utf-8 -*-
"""Censo AST da IDN-06: toda rota de identidade/pendencia do channel-gateway decide credencial.

Uso: python3 _identity_route_census.py [caminho/main.py]

A POPULACAO e toda funcao decorada com `@app.<metodo>("<caminho>")` cujo caminho comeca
por `/v1/channels/webhook/identity/` ou `/v1/channels/webhook/pending/`. Decidir e chamar,
no corpo, `_identity_caller` (o portao das duas portas) ou `verify_user_jwt` (as duas
rotas so de usuario: import e operator/register, que tem portao proprio).

AST e nao `grep`: o docstring de metade dessas rotas CITA o portao, e contar texto
contaria o comentario que documenta o fechamento.

Saida JSON: {rotas: [...], sem_portao: [...], populacao: N}.
"""
import ast
import json
import sys

PREFIXOS = ("/v1/channels/webhook/identity/", "/v1/channels/webhook/pending/")
DECISORES = {"_identity_caller", "verify_user_jwt"}

caminho = sys.argv[1] if len(sys.argv) > 1 else "packages/channel-gateway/src/plughub_channel_gateway/main.py"
arvore = ast.parse(open(caminho, encoding="utf-8").read())

rotas, sem = [], []
for no in ast.walk(arvore):
    if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for dec in no.decorator_list:
        if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app"
                and dec.args and isinstance(dec.args[0], ast.Constant)
                and isinstance(dec.args[0].value, str)):
            continue
        path = dec.args[0].value
        if not path.startswith(PREFIXOS):
            continue
        chamadas = {c.func.id for c in ast.walk(no)
                    if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        rota = "%s %s (%s)" % (dec.func.attr.upper(), path, no.name)
        rotas.append(rota)
        if not chamadas & DECISORES:
            sem.append(rota)

print(json.dumps({"populacao": len(rotas), "rotas": rotas, "sem_portao": sem}))
