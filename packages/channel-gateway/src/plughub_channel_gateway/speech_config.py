"""
speech_config.py — os parâmetros de segmentação da fala, por tenant, do config-api (VOZ-21).

Primeiro passo da recalibragem de STT por instalação (`TODO.md` § *Recalibragem de STT*, ADR
`adr-voice-media-plane.md` V13): limiar de energia, silêncio que fecha a fala, lacuna sem quadro,
fala mínima, fala máxima e o VAD do serviço eram constantes no construtor do provedor — não havia
o que recalibrar, e config de tuning em código é o que a casa proíbe.

Namespace `webrtc` (o canal é o recorte: a perna SIP terá os números dela, G.711 a 8 kHz). Cada
chave tem faixa; o config-api não valida valor, então quem lê valida:

  ausente          → default de código, AVISO nomeando a chave
  tipo/faixa ruim  → default de código, ERRO nomeando a chave e o valor
  config-api fora  → último valor bom se houver, senão o default — AVISO dizendo que a config do
                     tenant NÃO está valendo

A procedência de cada valor (`tenant`, `global`, `default: <motivo>`) acompanha o resultado e vai
ao log quando a chamada abre o fluxo de STT. `collect.voice.end_silence_ms`/`max_speech_s` de um
menu continuam vencendo enquanto a coleta espera (VOZ-18, `SpeechTuning`).

**Perfil de fala por ponto de entrada (VOZ-25).** A acústica varia pelo CAMINHO da mídia e pela
língua, não pelo tenant: um perfil nomeado no namespace `speech_profiles` (uma chave por perfil,
valor = objeto) pode sobrepor qualquer `stt_*` acima e escolher `stt_model`, `stt_language`,
`tts_model` e `tts_voice` DENTRO do mesmo serviço de fala (a URL é topologia, env). O endpoint da
chamada aponta o perfil em `settings.speech_profile_id`. Ordem: menu → perfil → tenant → global →
default. Perfil referenciado que não existe, campo inválido ou chave desconhecida são DITOS no log
— nunca aplicados em silêncio.

**Modelo, língua e voz SEM perfil vêm do tenant, e só então do env (VOZ-17).** As quatro chaves de
voz vivem no MESMO namespace `webrtc` e com os MESMOS nomes do perfil — quem não declara segue no
env, e a procedência (`profile:<id>` · `tenant`/`global` · `env`) diz qual camada respondeu. O env
não some: ele é o que faz a imagem subir falando sem config-api, e é a última camada, nunca a
primeira. A conferência contra o que o serviço TEM instalado mora em `speech_catalog.py` e acontece
na ESCRITA (`PUT /v1/speech-profiles/{id}`, `PUT /v1/speech-defaults`); aqui, na leitura, um modelo
que o serviço não tem continua sendo 404 por frase — a porta de escrita é que deixou de aceitá-lo.
"""
from __future__ import annotations

import dataclasses
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from .adapters.voice_provider import SpeechSegmentation

logger = logging.getLogger("plughub.channel-gateway.speech_config")

NAMESPACE = "webrtc"


@dataclass(frozen=True)
class _Param:
    key:   str
    field: str
    lo:    float | None = None
    hi:    float | None = None


# a ordem e os nomes são o contrato com o seed do config-api (`seed.py`, namespace `webrtc`)
PARAMS: tuple[_Param, ...] = (
    _Param("stt_energy_threshold", "energy_threshold", 50, 5000),
    _Param("stt_end_silence_ms",   "end_silence_ms",   100, 5000),
    _Param("stt_gap_ms",           "gap_ms",           100, 5000),
    _Param("stt_min_speech_ms",    "min_speech_ms",    50, 2000),
    _Param("stt_max_speech_ms",    "max_speech_ms",    1000, 60000),
    _Param("stt_vad_filter",       "vad_filter"),
)


def _valida(p: _Param, valor: object) -> object | None:
    """O valor convertido, ou None se não serve."""
    if p.field == "vad_filter":
        return valor if isinstance(valor, bool) else None
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    if valor != valor or not (p.lo <= valor <= p.hi):          # NaN ou fora da faixa
        return None
    return float(valor) if p.field == "energy_threshold" else int(valor)


def resolve(entries: dict, scopes: dict[str, str] | None, tenant_id: str) -> SpeechSegmentation:
    """Monta a segmentação a partir do namespace resolvido (`GET /config/webrtc`) e dos escopos
    (`GET /config/webrtc/_provenance`); cada chave ausente ou inválida cai no default, dito."""
    base = SpeechSegmentation()
    valores: dict[str, object] = {}
    proc: dict[str, str] = {}
    ausentes: list[str] = []
    for p in PARAMS:
        if p.key not in entries:
            ausentes.append(p.key)
            proc[p.field] = "default: ausente no config-api"
            continue
        v = _valida(p, entries[p.key])
        if v is None:
            logger.error(
                "speech_config: %s.%s=%r invalido para tenant=%s (%s) — vale o default %r",
                NAMESPACE, p.key, entries[p.key], tenant_id,
                "booleano" if p.field == "vad_filter" else f"numero entre {p.lo:g} e {p.hi:g}",
                getattr(base, p.field),
            )
            proc[p.field] = "default: valor invalido"
            continue
        valores[p.field] = v
        proc[p.field] = (scopes or {}).get(p.key, "config")
    if ausentes:
        logger.warning("speech_config: %s nao configurado(s) em %s para tenant=%s — vale o default de codigo",
                       ", ".join(ausentes), NAMESPACE, tenant_id)
    return SpeechSegmentation(**valores, provenance=proc)


class SpeechSegmentationConfig:
    """`SpeechSegmentation` por tenant, com cache. Leitura boa fica até o `config.changed` do
    namespace; falha é re-tentada depois de `retry_s`, valendo enquanto isso o último valor bom."""

    def __init__(self, config_api_url: str, retry_s: float = 30.0) -> None:
        self._url = config_api_url.rstrip("/")
        self._retry_s = retry_s
        # tenant → (segmentação, voz do tenant, procedência da voz, definitiva, instante,
        #           veio de uma leitura BOA)
        self._cache: dict[str, tuple[SpeechSegmentation, dict, dict, bool, float, bool]] = {}

    def invalidate(self, tenant_id: str | None = None) -> None:
        """Vence a entrada sem apagá-la: se a releitura falhar, o último valor BOM continua valendo
        — apagar faria uma mudança na tela seguida de config-api fora cair no default de código.
        Mudança do default GLOBAL chega com tenant_id="__global__" e vale para todos."""
        alvos = [tenant_id] if tenant_id and tenant_id != "__global__" else list(self._cache)
        vencidas = 0
        for t in alvos:
            if t in self._cache:
                seg, voz, proc, _, _, bom = self._cache[t]
                self._cache[t] = (seg, voz, proc, False, float("-inf"), bom)
                vencidas += 1
        # o log mora AQUI, não no chamador: um log no consumidor do evento afirmaria a invalidação
        # mesmo sem ela (medido 2026-09-17, mutação LM8)
        logger.info("speech_config: segmentacao da fala invalidada (tenant=%s, %d entrada(s) em cache)",
                    tenant_id or "__global__", vencidas)

    async def __call__(self, tenant_id: str) -> SpeechSegmentation:
        return (await self._entrada(tenant_id))[0]

    async def voice(self, tenant_id: str) -> tuple[dict[str, str], dict[str, str]]:
        """Modelo, língua e voz DECLARADOS pelo tenant, e a procedência de cada um (VOZ-17). Sai da
        MESMA leitura da segmentação — um namespace, uma ida ao config-api, um cache: duas leituras
        do mesmo namespace poderiam discordar entre si dentro da mesma chamada."""
        ent = await self._entrada(tenant_id)
        return ent[1], ent[2]

    async def _entrada(self, tenant_id: str):
        ent = self._cache.get(tenant_id)
        agora = time.monotonic()
        if ent and (ent[3] or agora - ent[4] < self._retry_s):
            return ent
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._url}/config/{NAMESPACE}", params={"tenant_id": tenant_id})
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                entries = resp.json().get("entries") or {}
                scopes: dict[str, str] | None = None
                try:
                    pr = await client.get(f"{self._url}/config/{NAMESPACE}/_provenance",
                                          params={"tenant_id": tenant_id})
                    if pr.status_code == 200:
                        scopes = {k: v.get("effective_scope", "config")
                                  for k, v in (pr.json().get("keys") or {}).items()}
                except Exception as exc:  # noqa: BLE001 — só o rótulo da procedência se perde, e é dito
                    logger.info("speech_config: procedencia de %s indisponivel (%s) — valores valem, "
                                "rotulados 'config'", NAMESPACE, exc)
        except Exception as exc:  # noqa: BLE001 — degradação dita abaixo
            anterior = ent if ent and ent[5] else None
            logger.warning(
                "speech_config: nao consegui ler %s do config-api (%s) — %s", NAMESPACE, exc,
                "segue o ultimo valor lido" if anterior
                else "a segmentacao E a voz configuradas para o tenant NAO valem; vale o default de "
                     "codigo e o env do gateway",
            )
            seg = anterior[0] if anterior else SpeechSegmentation(
                provenance={p.field: "default: config-api indisponivel" for p in PARAMS})
            voz, proc = (anterior[1], anterior[2]) if anterior else ({}, {})
            nova = (seg, voz, proc, False, agora, anterior is not None)
            self._cache[tenant_id] = nova
            return nova
        seg = resolve(entries, scopes, tenant_id)
        voz, proc = resolve_voice(entries, scopes, tenant_id)
        nova = (seg, voz, proc, True, agora, True)
        self._cache[tenant_id] = nova
        return nova


# ─── Perfil de fala (VOZ-25) ─────────────────────────────────────────────────────

PROFILES_NAMESPACE = "speech_profiles"
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# modelo e voz: o que o serviço aceita como nome; língua: `pt`, `pt-BR`
_MODELO = re.compile(r"^[A-Za-z0-9._/:-]{1,200}$")
VOICE_PARAMS: dict[str, re.Pattern[str]] = {
    "stt_model":    _MODELO,
    "stt_language": re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$"),
    "tts_model":    _MODELO,
    "tts_voice":    re.compile(r"^[A-Za-z0-9._-]{1,100}$"),
}
PROFILE_KEYS = frozenset({p.key for p in PARAMS} | set(VOICE_PARAMS) | {"description"})


def resolve_voice(entries: dict, scopes: dict[str, str] | None,
                  tenant_id: str) -> tuple[dict[str, str], dict[str, str]]:
    """O que o TENANT diz sobre modelo, língua e voz (namespace `webrtc`, VOZ-17), e a procedência
    de cada campo. **Chave ausente não é valor**: quem não declara segue no env, e é por isso que o
    retorno é o que foi DECLARADO, nunca o conjunto completo — devolver os quatro campos aqui faria
    o env virar "config do tenant" no rótulo. Valor fora do padrão é ERRO no log e NÃO vale."""
    valores: dict[str, str] = {}
    proc: dict[str, str] = {}
    for k, padrao in VOICE_PARAMS.items():
        if k not in entries:
            continue
        v = entries[k]
        if not isinstance(v, str) or not padrao.fullmatch(v):
            logger.error("speech_config: %s.%s=%r invalido para tenant=%s — vale o do env",
                         NAMESPACE, k, v, tenant_id)
            continue
        valores[k] = v
        proc[k] = (scopes or {}).get(k, "config")
    return valores, proc


def conferir_forma(perfil: dict) -> list[str]:
    """Recusas de FORMA de um perfil de fala — campo desconhecido, faixa dos `stt_*` e padrão de
    modelo/língua/voz —, na MESMA casa que os aplica na leitura (VOZ-17).

    A porta de escrita chama isto antes de perguntar ao serviço de fala: valor fora da faixa é
    recusa que não precisa de rede, e repetir as faixas na rota seria a segunda casa do fato que
    esta já guarda. Lista vazia = a forma serve; o que o SERVIÇO tem é outra pergunta
    (`speech_catalog.conferir`)."""
    recusas: list[str] = []
    for k in sorted(set(perfil) - PROFILE_KEYS):
        recusas.append(f"`{k}` nao e campo de perfil de fala — campos: "
                       f"{', '.join(sorted(PROFILE_KEYS))}")
    for p in PARAMS:
        if p.key in perfil and _valida(p, perfil[p.key]) is None:
            esperado = "booleano" if p.field == "vad_filter" else f"numero entre {p.lo:g} e {p.hi:g}"
            recusas.append(f"`{p.key}`={perfil[p.key]!r} invalido — esperado {esperado}")
    for k, padrao in VOICE_PARAMS.items():
        if k not in perfil:
            continue
        v = perfil[k]
        if v in (None, ""):          # campo em branco = herda a camada de baixo, não é recusa
            continue
        if not isinstance(v, str) or not padrao.fullmatch(v):
            recusas.append(f"`{k}`={v!r} nao casa o padrao {padrao.pattern}")
    if not isinstance(perfil.get("description", ""), str):
        recusas.append("`description` tem de ser texto")
    return recusas

_AUSENTE = object()       # perfil referenciado que não existe
_INDISPONIVEL = object()  # config-api fora e nenhum valor lido antes


@dataclass(frozen=True)
class SpeechSettings:
    """O que vale na fala de UMA chamada: a segmentação (com procedência por campo) e o modelo,
    a língua e a voz (com procedência própria). `profile_id` é o perfil EM VIGOR — None quando a
    chamada não aponta perfil, ou aponta um que não pôde valer (dito no log)."""
    segmentation: SpeechSegmentation
    stt_model:    str
    stt_language: str
    tts_model:    str
    tts_voice:    str
    profile_id:   str | None = None
    provenance:   dict = field(default_factory=dict, compare=False)

    def describe_voice(self) -> str:
        return " ".join(f"{k}={getattr(self, k) or '-'} ({self.provenance.get(k, 'default')})"
                        for k in VOICE_PARAMS)


def apply_profile(seg: SpeechSegmentation, voice_defaults: dict[str, str], profile_id: str | None,
                  profile: object, tenant_id: str, tenant_voice: dict[str, str] | None = None,
                  tenant_proc: dict[str, str] | None = None) -> SpeechSettings:
    """Sobrepõe o perfil à segmentação e à voz do tenant, que por sua vez sobrepõem o env (VOZ-17).
    `profile` é o valor do namespace, ou os sentinelas de ausente/indisponível.

    As três camadas ficam VISÍVEIS no resultado: a procedência de cada campo de voz é `env`,
    `tenant`/`global`/`config` ou `profile:<id>`, e é ela que o log da chamada imprime. Sem isso,
    "o modelo veio de onde?" só se responde relendo config — que é a pergunta que a VOZ-17 abriu."""
    voz = {k: voice_defaults.get(k, "") for k in VOICE_PARAMS}
    proc_voz = {k: "env" for k in VOICE_PARAMS}
    for k, v in (tenant_voice or {}).items():
        if k in voz:
            voz[k] = v
            proc_voz[k] = (tenant_proc or {}).get(k, "config")
    if not profile_id:
        return SpeechSettings(seg, **voz, profile_id=None, provenance=proc_voz)
    if profile is _INDISPONIVEL:
        logger.warning("speech_config: perfil de fala %r da chamada NAO vale — %s ilegivel no config-api "
                       "(tenant=%s); vale a config do tenant", profile_id, PROFILES_NAMESPACE, tenant_id)
        return SpeechSettings(seg, **voz, profile_id=None, provenance=proc_voz)
    if profile is _AUSENTE:
        logger.warning("speech_config: perfil de fala %r referenciado pelo endpoint nao existe em %s "
                       "(tenant=%s) — vale a config do tenant", profile_id, PROFILES_NAMESPACE, tenant_id)
        return SpeechSettings(seg, **voz, profile_id=None, provenance=proc_voz)
    if not isinstance(profile, dict):
        logger.error("speech_config: perfil de fala %r nao e um objeto (%s) em %s (tenant=%s) — ignorado; "
                     "vale a config do tenant", profile_id, type(profile).__name__, PROFILES_NAMESPACE, tenant_id)
        return SpeechSettings(seg, **voz, profile_id=None, provenance=proc_voz)

    rotulo = f"profile:{profile_id}"
    estranhas = sorted(set(profile) - PROFILE_KEYS)
    if estranhas:
        logger.warning("speech_config: perfil %r tem chave(s) desconhecida(s) %s — ignorada(s) (tenant=%s)",
                       profile_id, ", ".join(estranhas), tenant_id)
    valores: dict[str, object] = {}
    proc_seg: dict[str, str] = {}
    for p in PARAMS:
        if p.key not in profile:
            continue
        v = _valida(p, profile[p.key])
        if v is None:
            logger.error("speech_config: perfil %r: %s=%r invalido (%s) — vale o da config do tenant, %r "
                         "(tenant=%s)", profile_id, p.key, profile[p.key],
                         "booleano" if p.field == "vad_filter" else f"numero entre {p.lo:g} e {p.hi:g}",
                         getattr(seg, p.field), tenant_id)
            continue
        valores[p.field] = v
        proc_seg[p.field] = rotulo
    for k, padrao in VOICE_PARAMS.items():
        if k not in profile:
            continue
        v = profile[k]
        if not isinstance(v, str) or not padrao.fullmatch(v):
            logger.error("speech_config: perfil %r: %s=%r invalido — vale o do env, %r (tenant=%s)",
                         profile_id, k, v, voz[k], tenant_id)
            continue
        voz[k] = v
        proc_voz[k] = rotulo
    seg_final = dataclasses.replace(seg, **valores, provenance={**seg.provenance, **proc_seg})
    return SpeechSettings(seg_final, **voz, profile_id=profile_id, provenance=proc_voz)


class SpeechProfiles:
    """Os perfis de fala de um tenant (`GET /config/speech_profiles`), com o mesmo cache da
    segmentação: leitura boa fica até o `config.changed` do namespace; falha é re-tentada depois de
    `retry_s`, valendo enquanto isso a última leitura boa."""

    def __init__(self, config_api_url: str, retry_s: float = 30.0) -> None:
        self._url = config_api_url.rstrip("/")
        self._retry_s = retry_s
        # tenant → (perfis | None, definitiva, instante)
        self._cache: dict[str, tuple[dict | None, bool, float]] = {}

    def invalidate(self, tenant_id: str | None = None) -> None:
        alvos = [tenant_id] if tenant_id and tenant_id != "__global__" else list(self._cache)
        vencidas = 0
        for t in alvos:
            if t in self._cache:
                perfis, _, _ = self._cache[t]
                self._cache[t] = (perfis, False, float("-inf"))
                vencidas += 1
        logger.info("speech_config: perfis de fala invalidados (tenant=%s, %d entrada(s) em cache)",
                    tenant_id or "__global__", vencidas)

    async def __call__(self, tenant_id: str) -> dict | None:
        """Os perfis, ou None se o config-api está fora e nunca foi lido."""
        ent = self._cache.get(tenant_id)
        agora = time.monotonic()
        if ent and (ent[1] or agora - ent[2] < self._retry_s):
            return ent[0]
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._url}/config/{PROFILES_NAMESPACE}", params={"tenant_id": tenant_id})
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                perfis = resp.json().get("entries") or {}
        except Exception as exc:  # noqa: BLE001 — degradação dita abaixo
            anterior = ent[0] if ent else None
            logger.warning("speech_config: nao consegui ler %s do config-api (%s) — %s", PROFILES_NAMESPACE, exc,
                           "segue a ultima leitura" if anterior is not None
                           else "perfil de fala NAO vale nas chamadas; vale a config do tenant")
            self._cache[tenant_id] = (anterior, False, agora)
            return anterior
        self._cache[tenant_id] = (perfis, True, agora)
        return perfis


async def resolve_session(seg_config: SpeechSegmentationConfig, profiles: SpeechProfiles, tenant_id: str,
                          profile_id: str | None, voice_defaults: dict[str, str]) -> SpeechSettings:
    """Menu → perfil → tenant → global → default, para uma chamada."""
    seg = await seg_config(tenant_id)
    voz_tenant, proc_tenant = await seg_config.voice(tenant_id)
    if profile_id and not PROFILE_ID_RE.fullmatch(profile_id):
        logger.error("speech_config: speech_profile_id=%r do endpoint nao e um id de perfil valido "
                     "(tenant=%s) — ignorado; vale a config do tenant", profile_id, tenant_id)
        profile_id = None
    if not profile_id:
        return apply_profile(seg, voice_defaults, None, None, tenant_id, voz_tenant, proc_tenant)
    perfis = await profiles(tenant_id)
    if perfis is None:
        return apply_profile(seg, voice_defaults, profile_id, _INDISPONIVEL, tenant_id,
                             voz_tenant, proc_tenant)
    return apply_profile(seg, voice_defaults, profile_id, perfis.get(profile_id, _AUSENTE), tenant_id,
                         voz_tenant, proc_tenant)
