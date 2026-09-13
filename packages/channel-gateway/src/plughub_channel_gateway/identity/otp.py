"""
identity/otp.py — OtpService: prova de POSSE de canal (step-up componível).

Serviço agnóstico de identidade: prova que quem está na sessão controla um canal
`(kind, value)` (telefone/e-mail) recebendo um código enviado a ele. Quem liga
"posse provada → âncora `possessed`" é o WebhookAdapter — o OtpService não conhece
`customer_id`. Assim o OTP generaliza para qualquer step-up (identidade, pagamento,
revelar dado mascarado), acionado a critério do fluxo (opcional, nunca implícito).

⚠️ **O mecanismo recusa sobre si mesmo, de forma explícita (PID-10, 2026-09-13 —
ADR adr-identity-door-evidence D8).** Até aqui ele aceitava qualquer `kind` e
devolvia `sent: true` sempre: desafiava CPF (que não tem para onde mandar código) e,
fora do modo dev, respondia `sent: true` com a entrega marcada `TODO(prod)`. Hoje:

  - só âncora ENTREGÁVEL (`DELIVERABLE_KINDS`: phone, email) admite desafio;
  - sem canal de entrega de verdade, `sent: false, reason: delivery_unavailable` —
    nunca `sent: true` sem entrega. O modo dev É um canal, e diz que é
    (`delivery: "dev_log"`);
  - o desafio é AMARRADO a um `subject` opaco (quem pediu), e o verify só confere
    contra o mesmo `subject`. Sem isso, a prova emitida para um cliente podia ser
    anexada a outro na conferência.

Segurança:
  - Nenhuma PII em claro nas chaves (hash_anchor com salt por tenant).
  - Código guardado só como hash (sha256(salt+code)); nunca em claro no Redis.
  - Rate-limit por âncora (anti-enumeração): challenge não revela se a âncora
    pertence a alguém; verify não vaza info de cliente.
  - Entrega mockada no demo: o código só vai para o log (WARNING) e para a
    resposta (`dev_code`) quando PLUGHUB_OTP_DEV_RETURN_CODE está ligado — e o
    default dele é DESLIGADO desde a PID-10 (era ligado, e o esquecimento da env
    publicava o código na resposta).
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from .normalize import DELIVERABLE_KINDS, hash_anchor

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

    # ── recusa prévia — UMA casa para "este desafio pode existir?" ──────────────

    def _canal_de_entrega(self, kind: str) -> str | None:
        """O canal que de fato entrega o código a esta âncora, ou None.

        Não há entrega real no repositório (SMS/e-mail outbound para OTP não foi
        construído). O modo dev é o único canal, e é declarado como tal.
        """
        if kind not in DELIVERABLE_KINDS:
            return None
        return "dev_log" if self._dev else None

    def refusal(self, kind: str) -> dict[str, Any] | None:
        """Recusa que independe da âncora e do cliente — `None` se o desafio é possível.

        Pública porque o adaptador a consulta ANTES de ler o cadastro: sem ela, um CPF
        voltaria `anchor_not_authoritative` (resposta sobre o cliente) em vez de
        `undeliverable_kind` (resposta sobre o mecanismo). `challenge` a aplica de novo
        — mesma função, nunca uma segunda regra.
        """
        if kind not in DELIVERABLE_KINDS:
            return {"sent": False, "reason": "undeliverable_kind"}
        if self._canal_de_entrega(kind) is None:
            return {"sent": False, "reason": "delivery_unavailable"}
        return None

    # ── challenge ──────────────────────────────────────────────────────────────

    async def challenge(
        self, tenant_id: str, kind: str, value: str, *, subject: str,
    ) -> dict[str, Any]:
        """
        Emite um código de posse para a âncora, amarrado a `subject`. Retorna
        {sent: true, delivery, challenge_ttl_s} só quando há entrega. Nunca revela se
        a âncora pertence a alguém. Recusas: invalid_anchor · undeliverable_kind ·
        delivery_unavailable · subject_required · rate_limited.
        """
        recusa = self.refusal(kind)
        if recusa is not None:
            logger.warning("[OTP] desafio RECUSADO tenant=%s kind=%s reason=%s (PID-10)",
                           tenant_id, kind, recusa["reason"])
            return recusa
        if not subject:
            return {"sent": False, "reason": "subject_required"}
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
                "subject":      subject,
                "attempts":     0,
                "max_attempts": self._max_attempts,
                "created_at":   _now_iso(),
            }),
            ex=self._ttl_s,
        )
        delivery = self._deliver(tenant_id, kind, vh, code)

        out: dict[str, Any] = {"sent": True, "delivery": delivery, "challenge_ttl_s": self._ttl_s}
        if self._dev:
            out["dev_code"] = code   # demo only (flag-gated)
        return out

    def _deliver(self, tenant_id: str, kind: str, vh: str, code: str) -> str:
        """
        Entrega do código pelo canal de `_canal_de_entrega` — só é chamada depois de
        `refusal` confirmar que ele existe. Hoje o único é o log do modo dev.
        Canal real (SMS/e-mail) entra como outro ramo aqui E em `_canal_de_entrega`,
        e NUNCA loga o código.
        """
        logger.warning(
            "[OTP-DEV] tenant=%s kind=%s value_hash=%s code=%s (entrega mockada — "
            "digite este código no fluxo de destino)",
            tenant_id, kind, vh, code,
        )
        return "dev_log"

    # ── verify ─────────────────────────────────────────────────────────────────

    async def verify(
        self, tenant_id: str, kind: str, value: str, code: str, *, subject: str,
    ) -> dict[str, Any]:
        """
        Confere o código, e só contra o `subject` para quem o desafio foi emitido.
        Sucesso → apaga o desafio e zera o rate-limit. Falha → conta tentativa;
        estouro apaga o desafio. Retorna {verified, reason?, attempts_left?}.

        `subject` divergente conta como tentativa e responde `wrong_code`: dizer
        "subject errado" confirmaria a quem tenta que o código estava certo.
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

        mesmo_subject = bool(subject) and secrets.compare_digest(str(d.get("subject", "")), subject)
        if not mesmo_subject and d.get("subject"):
            logger.warning("[OTP] verify com subject DIFERENTE do desafio tenant=%s kind=%s (PID-10)",
                           tenant_id, kind)
        if mesmo_subject and secrets.compare_digest(self._hash_code(code), str(d.get("code_hash", ""))):
            await self._redis.delete(chal_key)
            await self._redis.delete(self._rl_key(tenant_id, kind, vh))
            return {"verified": True}

        # código errado (ou subject errado) — persiste a tentativa preservando o TTL
        d["attempts"] = attempts
        await self._redis.set(chal_key, json.dumps(d), keepttl=True)
        return {"verified": False, "reason": "wrong_code", "attempts_left": max_attempts - attempts}
