"""
speech_catalog.py — o que o serviço de fala TEM instalado, e a conferência contra o que a config
pede (VOZ-17).

O estado que originou: modelo, língua e voz de um perfil eram conferidos só contra um PADRÃO
(`speech_config.VOICE_PARAMS`), que diz a forma do nome e nada sobre existir. Um perfil com
`stt_model: "Systran/faster-whisper-medium"` — plausível, e não instalado — era aceito na gravação
e só falhava na CHAMADA: 404 do serviço a cada frase, `fala PERDIDA` no log do gateway, e nada
vermelho em lugar nenhum. É o "valor plausível" da § Postura de Engenharia no eixo que decide se o
cliente é ouvido.

Duas listas, e a diferença é o defeito inteiro:

  * `GET /v1/models`   — o que está INSTALADO (4 no demo). É esta que responde "a chamada vai
                         funcionar?".
  * `GET /v1/registry` — o que o serviço sabe BAIXAR (726 no demo). Modelo daqui que não esteja
                         instalado falha exatamente igual — conferir contra o registro seria
                         trocar uma recusa por um verde que não sustenta a chamada.

O catálogo traz `task`, `language[]` e, nos de TTS, `voices[{name, language}]` — então a
conferência é por TAREFA (modelo de TTS no campo de STT é erro que a forma não pega), por VOZ
(nome que aquele modelo oferece) e por LÍNGUA (subtag primária declarada pelo modelo).

**Serviço fora = recusa, nunca "vale por enquanto"** (decisão do dono, 2026-09-17): gravar sem
conferir criaria no store um perfil indistinguível dos conferidos na leitura seguinte, e a
conferência existe justamente porque a leitura não tem como saber. Com o serviço fora não há fala
nenhuma acontecendo — a janela em que a recusa incomoda é a janela em que o canal já está parado.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("plughub.channel-gateway.speech_catalog")

STT, TTS = "automatic-speech-recognition", "text-to-speech"
TAREFA_DO_CAMPO = {"stt_model": STT, "tts_model": TTS}


class CatalogoIndisponivel(RuntimeError):
    """O serviço de fala não respondeu o catálogo — a config NÃO foi conferida."""


async def catalogo(url: str, *, timeout: float = 10.0,
                   client: httpx.AsyncClient | None = None) -> list[dict]:
    """Os modelos instalados no serviço. Levanta `CatalogoIndisponivel` nomeando a causa — nunca
    devolve lista vazia por falha, que seria "nenhum modelo existe" e recusaria TUDO pela razão
    errada."""
    if not url:
        raise CatalogoIndisponivel(
            "este gateway nao tem servico de fala configurado (PLUGHUB_WEBRTC_SPEACHES_URL vazia)")
    alvo = f"{url.rstrip('/')}/v1/models"
    try:
        if client is not None:
            r = await client.get(alvo, timeout=timeout)
        else:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(alvo)
    except Exception as exc:  # noqa: BLE001 — a causa vai no texto da recusa
        raise CatalogoIndisponivel(f"servico de fala inalcancavel em {alvo}: {type(exc).__name__}: {exc}")
    if r.status_code != 200:
        raise CatalogoIndisponivel(f"servico de fala respondeu HTTP {r.status_code} em {alvo}")
    try:
        dados = r.json().get("data")
    except Exception:  # noqa: BLE001
        raise CatalogoIndisponivel(f"resposta nao-JSON do servico de fala em {alvo}")
    if not isinstance(dados, list):
        raise CatalogoIndisponivel(f"catalogo do servico de fala sem lista `data` em {alvo}")
    return [m for m in dados if isinstance(m, dict) and m.get("id")]


def por_tarefa(modelos: list[dict], tarefa: str) -> list[dict]:
    return [m for m in modelos if m.get("task") == tarefa]


def vozes(modelo: dict) -> list[str]:
    """Os nomes de voz que o modelo oferece; `[]` quando ele não declara nenhuma."""
    return [v.get("name") for v in (modelo.get("voices") or []) if isinstance(v, dict) and v.get("name")]


def _linguas(modelo: dict) -> list[str]:
    return [str(x).lower() for x in (modelo.get("language") or [])]


def conferir(modelos: list[dict], efetivo: dict[str, str]) -> list[str]:
    """As recusas sobre o que a chamada REALMENTE usaria — lista vazia = o serviço serve.

    `efetivo` é a resolução completa (env ⊕ tenant ⊕ perfil), não o que o corpo mandou: um perfil
    que troca só a voz tem de ser conferido contra o modelo que VAI valer, que pode vir de outra
    camada. Conferir o corpo isolado deixaria passar o par (modelo de baixo, voz de cima), que é
    justamente o que quebra na chamada."""
    recusas: list[str] = []
    por_id = {m["id"]: m for m in modelos}

    for campo, tarefa in TAREFA_DO_CAMPO.items():
        nome = (efetivo.get(campo) or "").strip()
        if not nome:
            continue
        m = por_id.get(nome)
        if m is None:
            disponiveis = sorted(x["id"] for x in por_tarefa(modelos, tarefa))
            recusas.append(
                f"`{campo}`={nome!r} nao esta instalado no servico de fala — instalados para "
                f"{tarefa}: {', '.join(disponiveis) or '(nenhum)'}")
            continue
        if m.get("task") != tarefa:
            recusas.append(
                f"`{campo}`={nome!r} existe no servico, mas serve a tarefa {m.get('task')!r} e nao "
                f"{tarefa!r} — trocar os dois campos de lugar e o erro que a forma do nome nao pega")

    tts = por_id.get((efetivo.get("tts_model") or "").strip())
    voz = (efetivo.get("tts_voice") or "").strip()
    if voz and tts is not None and tts.get("task") == TTS:
        nomes = vozes(tts)
        if nomes and voz not in nomes:
            amostra = ", ".join(nomes[:12]) + (f" (+{len(nomes) - 12})" if len(nomes) > 12 else "")
            recusas.append(f"`tts_voice`={voz!r} nao e voz de {tts['id']!r} — vozes: {amostra}")

    stt = por_id.get((efetivo.get("stt_model") or "").strip())
    lingua = (efetivo.get("stt_language") or "").strip()
    if lingua and stt is not None and stt.get("task") == STT:
        decl = _linguas(stt)
        base = lingua.split("-")[0].lower()
        if decl and "multilingual" not in decl and base not in decl:
            recusas.append(
                f"`stt_language`={lingua!r} nao esta entre as linguas de {stt['id']!r} "
                f"({len(decl)} declaradas; a comparacao e pela subtag {base!r})")
    return recusas
