# -*- coding: utf-8 -*-
"""Metade Python do probe_reason_required_has_reader.sh (SFE-08).

PROPOSIÇÃO: todo campo que um step `reason` EXIGE do modelo tem LEITOR no fluxo.

Medido em 2026-09-25: o `agente_avaliacao_v1` exigia `dimension_threads` e `overall_score`
no `output_schema` — nenhum dos dois chegava ao modelo (o tool-use leva o `json_schema_ref`)
e nenhum era lido por step algum: o `submit_result` não os repassa, porque a nota é
recomputada do formulário e os threads nascem de `criterion_responses`. Exigir número que
ninguém lê é o "valor plausível" na forma de contrato: parece rigor e não protege nada.

Onde a exigência mora:
  · `json_schema` inline presente → o `required` do topo dele (é o que o modelo recebe);
  · senão → as chaves `required: true` do `output_schema` (com `json_schema_ref`, é o
    fallback; sem ele, é o contrato do caminho flat).

Leitor = uma referência `$.pipeline_state.<output_as>.<chave>` em qualquer step do fluxo.
Objeto repassado INTEIRO (`$.pipeline_state.<output_as>`) conta como leitor de todas as
chaves — quem lê decide, e não há como saber daqui quais usa.

Uso: python3 _reason_required_reader.py <dir de skills>
Saída: uma linha por step reason (`STEP` ou `SEM_LEITOR`), e `TOTAL <n>`.
"""
import glob
import json
import os
import re
import sys

import yaml


def main(base: str) -> int:
    total = 0
    for f in sorted(glob.glob(os.path.join(base, "*.yaml"))):
        with open(f, encoding="utf-8") as fh:
            d = yaml.safe_load(fh) or {}
        steps = d.get("steps") or []
        texto = json.dumps(steps, ensure_ascii=False)
        skill = os.path.basename(f)[:-5]
        for s in steps:
            if not isinstance(s, dict) or s.get("type") != "reason":
                continue
            total += 1
            oa = re.escape(s.get("output_as") or "\x00")
            js = s.get("json_schema")
            if isinstance(js, dict):
                exigidos = list(js.get("required") or [])
            else:
                exigidos = [k for k, v in (s.get("output_schema") or {}).items()
                            if isinstance(v, dict) and v.get("required")]
            if re.search(r'\$\.pipeline_state\.%s"' % oa, texto):
                print(f"STEP {skill} {s.get('id')} inteiro")
                continue
            sem = [k for k in exigidos
                   if not re.search(r'\$\.pipeline_state\.%s\.%s\b' % (oa, re.escape(k)), texto)]
            if sem:
                print(f"SEM_LEITOR {skill} {s.get('id')} {','.join(sem)}")
            else:
                print(f"STEP {skill} {s.get('id')} {len(exigidos)}")
    print(f"TOTAL {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
