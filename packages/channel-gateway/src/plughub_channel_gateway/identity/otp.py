"""
identity/otp.py — OtpService: prova de POSSE de canal (step-up componível).

Serviço agnóstico de identidade: prova que quem está na sessão controla um canal
`(kind, value)` (telefone/e-mail/…) recebendo um código enviado a ele. Quem liga
"posse provada → âncora `possessed`" é o WebhookAdapter — o OtpService não conhece
`customer_id`. Assim o OTP generaliza para qualquer step-up (identidade, pagamento,
revelar dado mascarado), acionado a critério do fluxo (opcional, nunca implícito).

Segurança:
  - Nenhuma PII em claro nas chaves (hash_anchor com salt por tenant).
  - Código guardado só como hash (sha256(salt+code)); nunca em claro no Redis.
  - Rate-limit por âncora (anti-enumeração): challenge não revela se a âncora
    pertence a alguém; verify não vaza info de cliente.
  - Entrega mockada no demo: o código só vai para o log (WARNING) e para a
    resposta (`dev_code`) quando PLUGHUB_OTP_DEV_RETURN_CODE está ligado. Em
    produção (flag off), entrega real via canal e o código NUNCA é logado.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from .normalize import hash_anchor

logger = logging.getLogger("plughub.channel-gateway.identity.otp")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OtpService:
    def __init__(
        self,
        redis:           aioredis.Redis,
        salt:            str,
        ttl_s:           int = 300,     # validade do desafio
        max_attempts:    int = 5,       # tentativas de verify por desafio
        rl_window_s:     int = 900,     # janela do rate-limit de challenges
        rl_max:          int = 3,       # máx. de challenges por âncora na janela
        code_digits:     int = 6,
        dev_return_code: bool = False,  # demo: loga+retorna o código
    ) -> None:
        self._redis = redis
        self._salt  = salt
        self._ttl_s = ttl_s
        self._max_attempts = max_attempts
        self._rl_window_s  = rl_window_s
        self._rl_max       = rl_max
        self._code_digits  = code_digits
        self._dev          = dev_return_code

    # ── keys ──────────────────────────────────────────────────────────────────

    def _chal_key(self, tenant_id: str, kind: str, vh: str) -> str:
        return f"{tenant_id}:otp:chal:{kind}:{vh}"

    def _rl_key(self, tenant_id: str, kind: str, vh: str) -> str:
        return f"{tenant_id}:otp:rl:{kind}:{vh}"

    def _hash_code(self, code: str) -> str:
        return hashlib.sha256((self._salt + code).encode("utf-8")).hexdigest()

    # ── challenge ──────────────────────────────────────────────────────────────

    async def challenge(self, tenant_id: str, kind: str, value: str) -> dict[str, Any]:
        """
        Emite um código de posse para a âncora. Retorna {sent, ...}. Nunca revela
        se a âncora pertence a alguém. Sob rate-limit → {sent:false, reason}.
        """
        try:
            vh = hash_anchor(self._salt, kind, value)
        except ValueError:
            return {"sent": False, "reason": "invalid_anchor"}

        # rate-limit anti-enumeração
        rl_key = self._rl_key(tenant_id, kind, vh)
        n = await self._redis.incr(rl_key)
        if n == 1:
            await self._redis.expire(rl_key, self._rl_window_s)
        if n > self._rl_max:
            return {"sent": False, "reason": "rate_limited"}

        code = "".join(secrets.choice("0123456789") for _ in range(self._code_digits))
        await self._redis.set(
            self._chal_key(tenant_id, kind, vh),
            json.dumps({
                "code_hash":    self._hash_code(code),
                "attempts":     0,
                "max_attempts": self._max_attempts,
                "created_at":   _now_iso(),
            }),
            ex=self._ttl_s,
        )
        self._deliver(tenant_id, kind, vh, value, code)

        out: dict[str, Any] = {"sent": True, "challenge_ttl_s": self._ttl_s}
        if self._dev:
            out["dev_code"] = code   # demo only (flag-gated)
        return out

    def _deliver(self, tenant_id: str, kind: str, vh: str, value: str, code: str) -> None:
        """
        Entrega do código. Demo = mockada (log WARNING com o código, só sob flag
        DEV, para o testador digitar no fluxo). Produção = enviar pelo canal da
        âncora (hook a wirar; NUNCA loga o código).
        """
        if self._dev:
            logger.warning(
                "[OTP-DEV] tenant=%s kind=%s value_hash=%s code=%s (entrega mockada — "
                "digite este código no fluxo de destino)",
                tenant_id, kind, vh, code,
            )
        else:
            # TODO(prod): enviar via channel-gateway outbound para a âncora
            # (idealmente um canal DIFERENTE do da sessão, para provar posse).
            logger.info("[OTP] challenge emitido tenant=%s kind=%s (entrega real pendente)", tenant_id, kind)

    # ── verify ─────────────────────────────────────────────────────────────────

    async def verify(self, tenant_id: str, kind: str, value: str, code: str) -> dict[str, Any]:
        """
        Confere o código. Sucesso → apaga o desafio e zera o rate-limit. Falha →
        conta tentativa; estouro apaga o desafio. Retorna {verified, reason?,
        attempts_left?}.
        """
        try:
            vh = hash_anchor(self._salt, kind, value)
        except ValueError:
            return {"verified": False, "reason": "invalid_anchor"}

        chal_key = self._chal_key(tenant_id, kind, vh)
        raw = await self._redis.get(chal_key)
        if not raw:
            return {"verified": False, "reason": "no_challenge"}
        try:
            d = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        except Exception:
            await self._redis.delete(chal_key)
            return {"verified": False, "reason": "no_challenge"}

        attempts     = int(d.get("attempts", 0)) + 1
        max_attempts = int(d.get("max_attempts", self._max_attempts))
        if attempts > max_attempts:
            await self._redis.delete(chal_key)
            return {"verified": False, "reason": "too_many_attempts"}

        if secrets.compare_digest(self._hash_code(code), str(d.get("code_hash", ""))):
            await self._redis.delete(chal_key)
            await self._redis.delete(self._rl_key(tenant_id, kind, vh))
            return {"verified": True}

        # código errado — persiste a tentativa preservando o TTL do desafio
        d["attempts"] = attempts
        await self._redis.set(chal_key, json.dumps(d), keepttl=True)
        return {"verified": False, "reason": "wrong_code", "attempts_left": max_attempts - attempts}
