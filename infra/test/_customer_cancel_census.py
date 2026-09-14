"""_customer_cancel_census.py — PID-15. Censo estático da dispensa de Bearer no cancelamento.

Uso: python3 _customer_cancel_census.py <arquivo main.py do channel-gateway>
Imprime UMA linha JSON; o probe julga. O arquivo é argumento para a mutação rodar sobre CÓPIA.
"""
import json
import re
import sys

src = open(sys.argv[1], encoding="utf-8").read()
f = {}

m = re.search(r"async def _customer_cancel_allowed\(.*?\n    return True\n", src, re.S)
corpo = m.group(0) if m else ""
f["helper_encontrado"] = bool(corpo)
# as quatro condições, cada uma recusando por conta própria
f["exige_capacidade_e_atestado"] = 'required_abac is None or clearance != "session_evidence"' in corpo
f["bearer_segue_caminho_humano"] = "bearer_from_header(" in corpo
f["so_rejected"] = '!= "rejected"' in corpo
f["exige_exigencia_no_token"] = "resume_requirement(" in corpo

rota = src.split("async def webhook_resume(", 1)
rota = rota[1].split("\n@app.", 1)[0] if len(rota) == 2 else ""
i_cc = rota.find("_customer_cancel_allowed(")
i_ap = rota.find("_resolve_approver_principal(")
f["rota_consulta_antes_do_portao_humano"] = 0 <= i_cc < i_ap
# a dispensa só existe na rota INTERNA: a externa nunca pode chamá-la
ext = src.split("async def external_webhook_resume(", 1)
f["externa_sem_dispensa"] = len(ext) == 2 and "_customer_cancel_allowed" not in ext[1].split("\n@app.", 1)[0]

print(json.dumps(f))
