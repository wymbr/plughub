"""
account_selector.py
Selects the least-loaded, non-throttled LLM account for a given provider.
Health state persisted in Redis (< 1ms per pick — 3 MGET calls).

Redis keys:
  ai_gw:{provider}:{key_id}:throttled  → "1" with TTL = retry_after_seconds
  ai_gw:{provider}:{key_id}:rpm        → counter, INCR + EXPIRE 60s (rolling window)
  ai_gw:{provider}:{key_id}:tpm        → counter, INCR + EXPIRE 60s (rolling window)

Credential outcome (2026-08-23) — o funil único de erro grava AQUI o que antes só
existia no log do processo:
  ai_gw:{provider}:{key_id}:last_ok    → unix seconds da última chamada bem-sucedida
  ai_gw:{provider}:{key_id}:last_err   → "{unix}|{error_code}|{trecho da mensagem}"
  ai_gw:{provider}:{key_id}:ok:{w}     → contador de sucessos    (TESTEMUNHA)
  ai_gw:{provider}:{key_id}:err:{code}:{w} → contador por código de erro
    onde {w} é o índice da janela rolante (OUTCOME_WINDOW_S). O contador de
    AUSÊNCIA (erros) nunca anda sozinho: sem o de presença ao lado, "0 erros" é
    indistinguível de "0 chamadas".

`last_ok`/`last_err` NÃO têm TTL de propósito: são o estado da credencial, e um
estado que expira sozinho reintroduz o `unknown` que este mecanismo existe para
eliminar. Os CONTADORES expiram, porque contagem sem janela declarada não
significa nada.

key_id is the first 16 chars of SHA-256(api_key) — never stores the actual key.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from .providers.base import key_id_for

logger = logging.getLogger("plughub.ai_gateway.account_selector")

# Janela das contagens de desfecho. 24 h: longa o bastante para sobreviver a uma
# noite ociosa, curta o bastante para que o número descreva o dia de hoje. Sempre
# publicada junto do valor — contador sem janela é número sem unidade.
OUTCOME_WINDOW_S = 86_400

# Códigos que significam "esta credencial não serve", e que nenhuma rotação de
# conta nem espera resolve. Separados dos transitórios (rate_limit, connection_error,
# 5xx) porque só ESTES justificam reprovar o healthcheck: os outros já têm
# tratamento próprio (throttle + fallback) e reprovar por eles faria o container
# piscar vermelho a cada soluço de rede.
FATAL_CREDENTIAL_CODES = frozenset({
    "status_401", "status_403", "authentication_error", "permission_error",
})


def _as_text(raw) -> str:
    """
    Normaliza um valor lido do Redis para texto.

    Existe por um defeito real, pego pelo teste em 2026-08-23: `str(b"1750|x|y")`
    devolve `"b'1750|x|y'"` — não levanta, só corrompe. O `split("|")` seguinte
    ainda funciona, o primeiro campo deixa de ser dígito, o timestamp da falha vira
    0 e a conta recusada aparece como `unknown`. O cliente atual roda com
    `decode_responses=True`, então o caminho de produção nunca exercitou isso; um
    cliente configurado sem ele reintroduziria o bug em SILÊNCIO.
    """
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", "replace")
    return str(raw)


@dataclass
class LLMAccount:
    """Configuration for a single LLM provider account."""
    provider:   str
    api_key:    str
    weight:     int = 1
    rpm_limit:  int = 60          # requests per minute
    tpm_limit:  int = 100_000     # tokens per minute
    # GatewayConfig ID from agent-registry (empty string = untagged account).
    # Used by AccountSelector.pick(preferred_config_ids=[...]) to restrict
    # selection to specific accounts — e.g. evaluation campaigns pinning to
    # dedicated API keys to avoid competing with realtime traffic.
    config_id:  str = ""

    @property
    def key_id(self) -> str:
        """Short hash of the API key — safe to store in Redis keys."""
        return key_id_for(self.api_key)

    @property
    def provider_key(self) -> str:
        """Registry key used in the providers dict: {provider}:{key_id}"""
        return f"{self.provider}:{self.key_id}"


class AccountSelector:
    """
    Stateless selector — all state lives in Redis.
    Thread-safe: each pick() is an atomic read of Redis keys.

    Usage:
        selector = AccountSelector(redis, accounts)
        provider_key = await selector.pick("anthropic")
        if provider_key is None:
            # all accounts throttled — caller should try fallback provider
            ...
        provider = providers[provider_key]
        try:
            result = await provider.call(...)
            await selector.record_usage(provider_key, tokens=result.input_tokens + result.output_tokens)
        except ProviderError as e:
            if e.error_code == "rate_limit":
                await selector.mark_throttled(provider_key, retry_after_seconds=60)
    """

    def config_id_for(self, provider_key: str | None) -> str | None:
        """
        T2/D2 — `provider_key` ({provider}:{key_id}) → `config_id` do catálogo.

        Existe como método do selector, e não como lookup no chamador, porque são
        DUAS identidades para dois usos e o mapa entre elas mora aqui: o `key_id` é
        prefixo do SHA-256 da chave (bom para depurar rate-limit, muda na rotação);
        o `config_id` é o id do catálogo `llm_accounts` (tem `display_name` e
        SOBREVIVE à rotação) — é ele que a tela de custo por conta precisa.

        Guardar só o hash faria uma rotação de chave parecer "surgiu uma conta
        nova". Devolve None quando não há correspondência: ausência nomeada, nunca
        uma conta escolhida por conveniência.
        """
        if not provider_key:
            return None
        for accounts in self._accounts.values():
            for acc in accounts:
                if acc.provider_key == provider_key:
                    return acc.config_id or None
        return None

    def __init__(self, redis, accounts: list[LLMAccount]) -> None:
        self._redis = redis
        # Group accounts by provider
        self._accounts: dict[str, list[LLMAccount]] = {}
        for account in accounts:
            self._accounts.setdefault(account.provider, []).append(account)

    async def pick(
        self,
        provider: str,
        preferred_config_ids: list[str] | None = None,
    ) -> Optional[str]:
        """
        Returns the provider_key of the best available account for the given provider.
        Returns None if all accounts are throttled or none registered.

        Args:
            provider:             LLM provider name ("anthropic", "openai", …)
            preferred_config_ids: When non-empty, first attempt restricts candidates
                                  to accounts whose config_id is in this list (used by
                                  evaluation campaigns to pin to dedicated API keys).
                                  If no preferred account is available, falls through
                                  gracefully to the full account pool so the call never
                                  fails solely due to the preference filter.
        """
        accounts = self._accounts.get(provider, [])
        if not accounts:
            return None

        # ── Preferred-config-id filter (first pass) ────────────────────────
        if preferred_config_ids:
            preferred_set = set(preferred_config_ids)
            preferred_accounts = [a for a in accounts if a.config_id in preferred_set]
            if preferred_accounts:
                result = await self._pick_least_loaded(preferred_accounts, provider)
                if result is not None:
                    return result
                logger.warning(
                    "AccountSelector: preferred accounts (%s) all unavailable for provider=%s"
                    " — falling back to full pool",
                    preferred_config_ids, provider,
                )
            else:
                logger.debug(
                    "AccountSelector: no accounts match preferred_config_ids=%s for provider=%s"
                    " — using full pool",
                    preferred_config_ids, provider,
                )

        # ── Normal selection — full account pool ───────────────────────────
        return await self._pick_least_loaded(accounts, provider)

    async def _pick_least_loaded(
        self,
        accounts: list[LLMAccount],
        provider: str,
    ) -> Optional[str]:
        """
        From the given candidate list, returns the provider_key of the
        non-throttled account with the lowest utilization score.
        Returns None if all candidates are unavailable.
        """
        # Fast path — single account
        if len(accounts) == 1:
            acc = accounts[0]
            if await self._is_available(acc):
                return acc.provider_key
            logger.warning(
                "AccountSelector: single account throttled for provider=%s", provider,
            )
            return None

        best_key: Optional[str] = None
        best_util = float("inf")

        for acc in accounts:
            if not await self._is_available(acc):
                continue
            util = await self._utilization(acc)
            if util < best_util:
                best_util = util
                best_key = acc.provider_key

        if best_key is None:
            logger.warning(
                "AccountSelector: all %d candidates throttled for provider=%s",
                len(accounts), provider,
            )
        return best_key

    async def mark_throttled(
        self,
        provider_key: str,
        retry_after_seconds: int = 60,
    ) -> None:
        """
        Mark account as throttled for retry_after_seconds.
        Called when provider returns 429/529.
        """
        provider, key_id = provider_key.split(":", 1)
        redis_key = f"ai_gw:{provider}:{key_id}:throttled"
        await self._redis.set(redis_key, "1", ex=retry_after_seconds)
        logger.warning(
            "Account throttled: provider_key=%s for %ds",
            provider_key, retry_after_seconds,
        )

    async def record_usage(self, provider_key: str, tokens: int = 0) -> None:
        """
        Increment RPM counter (always) and TPM counter (when tokens > 0).
        Uses pipelined INCR + EXPIRE for atomicity and speed.
        """
        provider, key_id = provider_key.split(":", 1)
        rpm_key = f"ai_gw:{provider}:{key_id}:rpm"
        tpm_key = f"ai_gw:{provider}:{key_id}:tpm"

        pipe = self._redis.pipeline(transaction=False)
        pipe.incr(rpm_key)
        pipe.expire(rpm_key, 60)
        if tokens > 0:
            pipe.incrby(tpm_key, tokens)
            pipe.expire(tpm_key, 60)
        await pipe.execute()

        # `record_usage` só é chamado DEPOIS de um `provider.call()` que retornou —
        # ou seja, é o ponto em que sabemos que a credencial funcionou. Registrar o
        # sucesso aqui evita um segundo call site que pudesse divergir do primeiro.
        await self.record_outcome(provider_key, ok=True)

    async def record_outcome(
        self,
        provider_key: str,
        ok:           bool,
        error_code:   str = "",
        message:      str = "",
    ) -> None:
        """
        Registra o desfecho de UMA chamada ao provedor, por conta.

        Existe porque o fato nascia e morria no log: `provider_error_handler` logava
        `upstream_model_error` e devolvia 502, e o `/v1/health` respondia `ok` com a
        credencial recusada — 124 recusas seguidas sem nada ficar vermelho. Log não
        é estado: some no recreate do container e ninguém o consulta.

        Nunca levanta: instrumentação que derruba o caminho que ela mede é pior que
        instrumentação nenhuma. Mas TAMBÉM não engole calada — degradação sem motivo
        registrado é o defeito que este arco existe para não repetir.
        """
        try:
            provider, key_id = provider_key.split(":", 1)
            now    = int(time.time())
            window = now // OUTCOME_WINDOW_S
            base   = f"ai_gw:{provider}:{key_id}"

            pipe = self._redis.pipeline(transaction=False)
            if ok:
                pipe.set(f"{base}:last_ok", str(now))
                counter = f"{base}:ok:{window}"
            else:
                head = (message or "").replace("\n", " ")[:180]
                pipe.set(f"{base}:last_err", f"{now}|{error_code}|{head}")
                counter = f"{base}:err:{error_code}:{window}"
            pipe.incr(counter)
            # 2× a janela: o contador da janela anterior ainda existe enquanto a
            # atual está no começo, então "24 h" não vira "3 minutos" às 00h01.
            pipe.expire(counter, OUTCOME_WINDOW_S * 2)
            await pipe.execute()
        except Exception as exc:
            logger.warning(
                "record_outcome falhou provider_key=%s ok=%s code=%s — %s",
                provider_key, ok, error_code, exc,
            )

    async def credential_summary(self) -> list[dict]:
        """
        Estado da CREDENCIAL de cada conta registrada, para o `/v1/health`.

        Três estados, e `unknown` jamais é dobrado em `ok`: uma conta que nunca foi
        exercitada não é uma conta saudável, é uma conta sobre a qual não há
        evidência. Foi exatamente lê-la como `ok` que sustentou meses de 200 verde.

          ok       — houve chamada bem-sucedida, e ela é mais recente que a falha
          invalid  — a última falha é de credencial (FATAL_CREDENTIAL_CODES)
          error    — a última falha é transitória (rate limit, rede, 5xx)
          unknown  — nenhum desfecho registrado desde que o mecanismo existe

        `throttled` vem JUNTO porque credencial e disponibilidade são fatos
        diferentes e o health precisa dos dois: uma conta pode ter credencial `ok`
        e estar fora de circulação. Sem este campo, TODAS as contas throttled
        produziriam `ok`/200 enquanto o `pick()` devolve None e toda chamada cai no
        alias legado — verde sobre capacidade zero, que é a mesma família de
        defeito que esta função existe para fechar.
        """
        out: list[dict] = []
        for provider, accounts in self._accounts.items():
            for acc in accounts:
                base = f"ai_gw:{provider}:{acc.key_id}"
                try:
                    last_ok_raw, last_err_raw, throttled_raw = await self._redis.mget(
                        f"{base}:last_ok", f"{base}:last_err", f"{base}:throttled",
                    )
                except Exception as exc:
                    logger.warning("credential_summary: leitura falhou para %s — %s", base, exc)
                    last_ok_raw, last_err_raw, throttled_raw = None, None, None

                last_ok_at = int(_as_text(last_ok_raw)) if last_ok_raw else 0
                err_at, err_code, err_msg = 0, "", ""
                if last_err_raw:
                    parts = _as_text(last_err_raw).split("|", 2)
                    err_at   = int(parts[0]) if parts[0].isdigit() else 0
                    err_code = parts[1] if len(parts) > 1 else ""
                    err_msg  = parts[2] if len(parts) > 2 else ""
                    if err_at == 0:
                        # Registro ilegível não pode virar `unknown` mudo: `unknown`
                        # significa "não medimos", e aqui medimos e não soubemos ler.
                        logger.warning(
                            "credential_summary: last_err ilegível em %s — %r",
                            base, last_err_raw,
                        )

                if last_ok_at == 0 and err_at == 0:
                    state = "unknown"
                elif last_ok_at >= err_at:
                    state = "ok"
                elif err_code in FATAL_CREDENTIAL_CODES:
                    state = "invalid"
                else:
                    state = "error"

                now = int(time.time())
                out.append({
                    "provider":        provider,
                    "key_id":          acc.key_id,
                    "config_id":       acc.config_id,
                    "credentials":     state,
                    "throttled":       bool(throttled_raw),
                    "last_ok_age_s":   (now - last_ok_at) if last_ok_at else None,
                    "last_error_age_s": (now - err_at) if err_at else None,
                    "last_error_code": err_code or None,
                    "last_error":      err_msg or None,
                })
        return out

    async def outcome_counters(self) -> dict:
        """
        Contagens da janela corrente: sucessos (testemunha) e erros por código.

        A janela vai JUNTO do número. Sem ela o valor não é falseável — foi assim
        que "124 status_401" precisou de arqueologia de log para virar um fato.
        """
        window  = int(time.time()) // OUTCOME_WINDOW_S
        ok_total = 0
        errors: dict[str, int] = {}
        try:
            for provider, accounts in self._accounts.items():
                for acc in accounts:
                    base = f"ai_gw:{provider}:{acc.key_id}"
                    raw = await self._redis.get(f"{base}:ok:{window}")
                    ok_total += int(raw or 0)
                    async for key in self._redis.scan_iter(match=f"{base}:err:*:{window}"):
                        name = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
                        code = name[len(base) + 5 : name.rfind(":")]
                        errors[code] = errors.get(code, 0) + int(await self._redis.get(name) or 0)
        except Exception as exc:
            logger.warning("outcome_counters falhou — %s", exc)
            return {"window_seconds": OUTCOME_WINDOW_S, "available": False,
                    "reason": str(exc)[:200]}
        return {
            "window_seconds": OUTCOME_WINDOW_S,
            "available":      True,
            "calls_ok":       ok_total,
            "errors":         errors,
        }

    def providers_for(self, provider: str) -> list[str]:
        """Returns all registered provider_keys for a provider (for diagnostics)."""
        return [acc.provider_key for acc in self._accounts.get(provider, [])]

    async def health_summary(self) -> dict[str, list[dict]]:
        """Returns health state of all registered accounts (for /v1/health endpoint)."""
        summary: dict[str, list[dict]] = {}
        for provider, accounts in self._accounts.items():
            summary[provider] = []
            for acc in accounts:
                throttled_key = f"ai_gw:{provider}:{acc.key_id}:throttled"
                rpm_key = f"ai_gw:{provider}:{acc.key_id}:rpm"
                tpm_key = f"ai_gw:{provider}:{acc.key_id}:tpm"
                results = await self._redis.mget(throttled_key, rpm_key, tpm_key)
                throttled, rpm_raw, tpm_raw = results
                summary[provider].append({
                    "key_id":       acc.key_id,
                    "provider_key": acc.provider_key,
                    "throttled":    bool(throttled),
                    "rpm_current":  int(rpm_raw or 0),
                    "rpm_limit":    acc.rpm_limit,
                    "tpm_current":  int(tpm_raw or 0),
                    "tpm_limit":    acc.tpm_limit,
                })
        return summary

    # ─── Private helpers ───────────────────────────────────────────────────────

    async def _is_available(self, acc: LLMAccount) -> bool:
        """Returns True if account is not throttled and within rate limits."""
        provider, key_id = acc.provider, acc.key_id
        throttled_key = f"ai_gw:{provider}:{key_id}:throttled"
        rpm_key       = f"ai_gw:{provider}:{key_id}:rpm"
        tpm_key       = f"ai_gw:{provider}:{key_id}:tpm"

        results = await self._redis.mget(throttled_key, rpm_key, tpm_key)
        throttled, rpm_raw, tpm_raw = results

        if throttled:
            return False
        if int(rpm_raw or 0) >= acc.rpm_limit:
            return False
        if int(tpm_raw or 0) >= acc.tpm_limit:
            return False
        return True

    async def _utilization(self, acc: LLMAccount) -> float:
        """Returns utilization ratio for load balancing. Lower = better."""
        provider, key_id = acc.provider, acc.key_id
        rpm_key = f"ai_gw:{provider}:{key_id}:rpm"
        tpm_key = f"ai_gw:{provider}:{key_id}:tpm"
        results = await self._redis.mget(rpm_key, tpm_key)
        rpm_raw, tpm_raw = results
        rpm_util = int(rpm_raw or 0) / max(acc.rpm_limit, 1)
        tpm_util = int(tpm_raw or 0) / max(acc.tpm_limit, 1)
        # RPM weighted 70% — more commonly the binding constraint for short calls
        return rpm_util * 0.7 + tpm_util * 0.3


def resolve_provider_key(registry: dict, provider: object) -> str | None:
    """
    T2/D2 — descobre sob qual `{provider}:{key_id}` uma INSTÂNCIA de provider está
    registrada.

    Existe porque nem todo caminho de consumo passa pelo AccountSelector: o
    sentimento e o copiloto pegam o provider pelo ALIAS legado
    (`registry.get("anthropic")`), e o alias não diz qual conta é. Sem esta
    resolução, 42% das chamadas ficariam com conta desconhecida — o que
    transformaria o relatório por conta num balde "outros" grande demais para ter
    uso.

    A mesma instância aparece no dicionário sob DUAS chaves (o alias e a canônica);
    esta função devolve a canônica — a única que carrega o `key_id`. Identidade de
    objeto (`is`), não igualdade: dois providers com a mesma configuração são
    contas diferentes.

    Devolve None quando não há chave canônica. Ausência nomeada; o chamador NÃO
    inventa uma conta.
    """
    if provider is None or not registry:
        return None
    for key, instance in registry.items():
        if instance is provider and ":" in key:
            return key
    return None
