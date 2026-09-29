"""
_route_anon_sweep.py — AUT-58: o que cada rota de cada serviço responde a um chamador ANÔNIMO.

O censo estático (`_route_principal_census.py`) só enxerga guard na assinatura ou no corpo do
handler; não vê dependência de router, middleware nem o app inteiro sem portão. Esta varredura
mede a proposição de verdade — *"esta rota decide sem credencial?"* — e sem efeito colateral:

  GET                  → chamado com ids e tenant INEXISTENTES (`probe_aut58`);
  escrita COM corpo    → corpo de tipo inválido (`"x"`): um guard de dependência responde
                         401/403 ANTES da validação; rota aberta responde 422 sem executar nada;
  escrita SEM corpo    → com id no caminho, disparada com id INEXISTENTE (nada a apagar);
                         sem id no caminho, NÃO disparada (`nao_medida`): seria executar a ação.

Classes por rota:
  fechada     401/403 (ou 503 de guard sem segredo configurado)
  aberta      qualquer outra resposta HTTP a um anônimo (2xx, 404, 422, 405, 500…)
  nao_medida  escrita sem corpo, ou serviço inalcançável

⚠️ `aberta` num corpo inválido pode ser guard que decide DENTRO do handler (depois da
validação) — o censo estático é quem desempata. Esta varredura erra para o lado de ACUSAR.

Roda DENTRO da rede do compose (serviços por nome). Saída: JSON por serviço.
Uso: python3 _route_anon_sweep.py nome=http://host:porta [...]
"""
from __future__ import annotations

import json
import re
import sys

import httpx

PROBE = "probe_aut58"
WRITE = {"post", "put", "patch", "delete"}
# Escritas sem corpo COM id no caminho são disparadas com id inexistente (nada a apagar ou
# mudar). Exceção: as que criam estado mesmo para id inventado.
NUNCA_DISPARAR = ("/reserve/",)


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", PROBE, path)


def sweep(name: str, base: str) -> dict:
    out: dict = {"service": name, "base": base, "routes": []}
    try:
        spec = httpx.get(f"{base}/openapi.json", timeout=5).json()
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"openapi inalcançável: {exc}"
        return out
    with httpx.Client(timeout=6) as c:
        for path, ops in sorted((spec.get("paths") or {}).items()):
            for method, op in sorted(ops.items()):
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                url = f"{base}{_fill(path)}"
                params = {"tenant_id": PROBE}
                has_body = bool(op.get("requestBody"))
                com_id = "{" in path
                if method in WRITE and not has_body and (not com_id or any(d in path for d in NUNCA_DISPARAR)):
                    out["routes"].append({"route": f"{method.upper()} {path}", "class": "nao_medida",
                                          "why": "escrita sem corpo e sem id no caminho — dispará-la seria executar a ação"})
                    continue
                try:
                    if method == "get":
                        r = c.get(url, params=params, headers={"accept": "application/json"})
                    elif not has_body:
                        # id INEXISTENTE no caminho: guardada → 401/403; aberta → 404, sem nada a apagar
                        r = c.request(method.upper(), url, params=params)
                    else:
                        r = c.request(method.upper(), url, params=params, content='"x"',
                                      headers={"content-type": "application/json"})
                    code = r.status_code
                except httpx.ReadTimeout:
                    code = "timeout"
                except Exception as exc:  # noqa: BLE001
                    code = f"erro:{type(exc).__name__}"
                if code in (401, 403) or code == 503 and "token" in (r.text or "").lower():
                    cls = "fechada"
                elif isinstance(code, int):
                    cls = "aberta"
                else:
                    cls = "nao_medida"
                out["routes"].append({"route": f"{method.upper()} {path}", "class": cls, "status": code})
    return out


if __name__ == "__main__":
    res = [sweep(*a.split("=", 1)) for a in sys.argv[1:]]
    print(json.dumps(res, ensure_ascii=False))
