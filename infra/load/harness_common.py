"""
harness_common.py — peças compartilhadas pelos drivers de carga (PRD-02).

Sem dependência além da biblioteca padrão: o JWT é HS256 montado à mão, como o
gateway o valida (`adapters/webchat.py::_decode_token`), e as estatísticas são
percentis sobre amostras em memória — um gerador de carga com 3.000 sessões guarda
dezenas de milhares de números, o que cabe folgado.

Regra deste harness: AUSÊNCIA NÃO É ZERO. Uma métrica sem amostra sai `null` com
`n=0`, nunca `0 ms` — senão uma rodada em que nenhuma sessão recebeu resposta
reportaria "tempo de resposta 0" e pareceria excelente.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import time
from collections import Counter, defaultdict
from typing import Any


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def jwt_hs256(claims: dict, secret: str) -> str:
    """JWT HS256 — o único algoritmo que o webchat aceita."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url(sig)}"


def meta_signature(body: bytes, app_secret: str) -> str:
    """Valor do header `X-Hub-Signature-256` que a Meta manda (HMAC-SHA256 do corpo cru)."""
    return "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()


def percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


class Metrics:
    """Tempos (ms), contadores e um gauge de concorrência, com resumo em JSON."""

    def __init__(self) -> None:
        self.timings: dict[str, list[float]] = defaultdict(list)
        self.counters: Counter = Counter()
        self.active = 0
        self.peak_active = 0
        self.started_at = time.time()

    def timing(self, name: str, ms: float) -> None:
        self.timings[name].append(ms)

    def count(self, name: str, n: int = 1) -> None:
        self.counters[name] += n

    def enter(self) -> None:
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)

    def leave(self) -> None:
        self.active -= 1

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "duration_s": round(time.time() - self.started_at, 1),
            "peak_active_sessions": self.peak_active,
            "counters": dict(sorted(self.counters.items())),
            "timings_ms": {},
        }
        for name, vals in sorted(self.timings.items()):
            s = sorted(vals)
            out["timings_ms"][name] = {
                "n": len(s),
                "p50": _r(percentile(s, 0.50)),
                "p90": _r(percentile(s, 0.90)),
                "p95": _r(percentile(s, 0.95)),
                "p99": _r(percentile(s, 0.99)),
                "max": _r(s[-1] if s else None),
            }
        return out


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


def write_report(path: str | None, report: dict) -> None:
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")


def ramp_delay(index: int, total: int, ramp_s: float) -> float:
    """Atraso de partida da sessão `index` para uma rampa linear de `ramp_s` segundos."""
    if total <= 1 or ramp_s <= 0:
        return 0.0
    return ramp_s * index / (total - 1)
