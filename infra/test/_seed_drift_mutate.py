"""Injeta as TRES formas de divergencia em `agent_activity.pause_reasons`.

Auxiliar de `probe_seed_drift_named.sh` (D7 do arco ALLOWLIST). Arquivo proprio e
nao `python3 -c` inline porque o gate roda sob shells diferentes e aspas aninhadas
dentro de `$( ... | python3 -c '...' )` quebram em alguns deles — quebra de
FERRAMENTA que se parece com gate vermelho.

Le a lista declarada no stdin, devolve no stdout a versao divergente:
  · remove `almoco`      -> item so no DECLARADO   (a reaplicacao acrescentaria)
  · troca o label de `intervalo` -> campo DIFERENTE
  · acrescenta `probe_drift`     -> item so no GRAVADO (a reaplicacao DESCARTARIA)

As tres juntas porque o ramo C afirma que as tres direcoes sao nomeadas
SEPARADAMENTE; uma so nao distinguiria "diverge" de "diverge e eu sei como".
"""
import json
import sys

value = json.load(sys.stdin)

out = [dict(item) for item in value if item.get("id") != "almoco"]
for item in out:
    if item.get("id") == "intervalo":
        item["label"] = "INJETADO PELO PROBE"
out.append({"id": "probe_drift", "label": "so no banco", "requires_note": False})

json.dump(out, sys.stdout)
