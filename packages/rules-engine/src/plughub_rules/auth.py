"""
auth.py — o portão da API do rules-engine (AUT-65, 2026-09-30).

Até aqui as 7 rotas de regra respondiam a qualquer um que alcançasse a porta — e a porta era
publicada em todas as interfaces do host. Desde a RUL-02 isso deixou de ser inofensivo: ativar
uma regra com `target_pool` TIRA CONTATOS DA IA de verdade.

Decisões do dono (2026-09-30):
  - capacidade = campo novo **`config.rules`** (catálogo `infra/modules.yaml`): `read_only` lê
    regras e relatório; `read_write` cria, muda status (ativa), roda dry-run e evaluate. Preset
    só para `admin` — ninguém mais nasce podendo tirar contato da IA.
  - chamador interno por `X-Service-Token` (`PLUGHUB_RULES_SERVICE_TOKEN`): a tool
    `rule_dry_run` do mcp-server e o e2e-runner. Token vazio FECHA a porta, nunca a abre.

Portas, nesta ordem:
  1. `X-Service-Token` válido → passa; o tenant é o da requisição (serviço escolhe o tenant).
  2. `Bearer` do auth-api → o tenant é o do TOKEN; `tenant_id` divergente na requisição é
     403 `tenant_mismatch` (TNT-01). Sem o campo no grau pedido, 403 nomeando o campo.
  Segredo de JWT ausente ⇒ 503 (não consigo conferir), nunca "aceito sem conferir".
"""
from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException, Request
from plughub_authz import abac_can, bearer_from_header, verify_user_jwt

from .config import get_settings

logger = logging.getLogger("plughub.rules.auth")

MODULE, FIELD = "config", "rules"


def _service_ok(request: Request) -> bool:
    svc = request.headers.get("x-service-token")
    token = get_settings().rules_service_token
    return bool(svc and token and hmac.compare_digest(svc, token))


def _claims(request: Request) -> dict:
    token = bearer_from_header(request.headers.get("authorization"))
    if not token:
        raise HTTPException(status_code=401, detail="rules-engine exige credencial")
    secret = get_settings().auth_jwt_secret
    if not secret:
        raise HTTPException(status_code=503,
                            detail="rules-engine sem PLUGHUB_AUTH_JWT_SECRET — nao consigo verificar credencial")
    claims = verify_user_jwt(token, secret)
    if not claims:
        raise HTTPException(status_code=401, detail="credencial invalida ou expirada")
    return claims


def require_credential(request: Request) -> None:
    """Dependência do ROUTER: credencial presente e válida ANTES do corpo — senão o anônimo
    numa escrita via 422 (validação) em vez de 401. Capacidade e tenant ficam no `authorize`."""
    if _service_ok(request):
        return
    _claims(request)


def authorize(request: Request, tenant_id: str, *, write: bool) -> str:
    """Decide se o chamador pode, e devolve o tenant que vale para a chamada."""
    if _service_ok(request):
        if not tenant_id:
            raise HTTPException(status_code=400, detail="tenant_id obrigatorio")
        return tenant_id
    claims = _claims(request)
    grau = "read_write" if write else "read_only"
    if not abac_can(claims, MODULE, FIELD, grau):
        logger.warning("rules NEGADO: sub=%s sem %s.%s em %s", claims.get("sub"), MODULE, FIELD, grau)
        raise HTTPException(status_code=403, detail=f"regras exigem {MODULE}.{FIELD} ({grau})")
    tenant = str(claims.get("tenant_id") or "")
    if not tenant:
        raise HTTPException(status_code=401, detail="credencial sem tenant")
    if tenant_id and tenant_id != tenant:
        logger.warning("rules RECUSA: sub=%s pediu tenant=%s com credencial de %s",
                       claims.get("sub"), tenant_id, tenant)
        raise HTTPException(status_code=403, detail="tenant_mismatch")
    return tenant
