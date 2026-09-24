"""
text_menu.py — menu de ESCOLHA em canal de texto (SMS, WhatsApp): uma casa (NIV-14/17).

Desde a NIV-13 o motor só aceita, num menu `button`/`list`/`checklist`, o ID de uma opção — o
resto ele recusa e reenvia o menu. Canal que desenha o menu como texto numerado recebe de volta
"2", "segunda via" ou "1, 3", e cabe ao CANAL traduzir o que ele mesmo numerou. Cada adaptador
fazia do seu jeito (o SMS gravava o `value`, o WhatsApp mandava o texto cru ou embrulhava a
escolha num formulário), e nenhum validava.

O que mora aqui:
  · `render`  — o texto: prompt, "1. rótulo" por opção e como responder;
  · `resolve` — a resposta digitada → id (escolha) ou lista de ids (checklist), ou None;
  · `PendingMenu` — o menu de escolha em aberto na sessão, no Redis, para o canal saber o que
    ele mesmo numerou quando a resposta chegar.

⚠️ Resposta que NÃO se traduz segue CRUA ao motor: é ele que recusa, reenvia o menu (com o
reprompt do autor) e conta as tentativas até `on_invalid`/`on_failure`. Um segundo contador no
canal seria a mesma regra em duas casas — e as duas discordariam.

A numeração e o casamento são os do `collect_core` (a mesma tecla do telefone): "2" e "dois"
valem a opção 2; o rótulo vale inteiro; "quero um boleto" NÃO é a opção 1.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .collect_core import Option, match_options, options_from_menu

logger = logging.getLogger("plughub.channel-gateway.text_menu")

CHOICE_INTERACTIONS = ("button", "list", "checklist")
# separadores de uma resposta de checklist: vírgula, ponto e vírgula, barra. Dentro de um pedaço,
# "1 e 3" / "1 3" só se dividem quando o pedaço INTEIRO não é uma opção ("Boleto e PIX" é uma).
_MULTI_SEP = re.compile(r"\s*[,;/]\s*")
_WORD_SEP = re.compile(r"\s+(?:e\s+)?", re.IGNORECASE)


def is_choice_menu(payload: dict) -> bool:
    return (payload.get("interaction") in CHOICE_INTERACTIONS
            and bool(options_from_menu(payload.get("options"))))


def render(prompt: str, options: tuple[Option, ...], interaction: str) -> str:
    linhas = [prompt.strip()] if prompt and prompt.strip() else []
    if linhas:
        linhas.append("")
    linhas += [f"{o.key}. {o.label}" for o in options]
    linhas.append("")
    linhas.append("Responda com os números das opções, separados por vírgula."
                  if interaction == "checklist" else "Responda com o número da opção.")
    return "\n".join(linhas)


def resolve(text: str, options: tuple[Option, ...], interaction: str) -> str | list[str] | None:
    """O id que a resposta nomeia (escolha) ou a lista de ids (checklist). None quando não nomeia
    exatamente uma opção — nenhuma, ou mais de uma numa escolha única."""
    if not text or not text.strip():
        return None
    if interaction != "checklist":
        achadas = match_options(options, text)
        return achadas[0] if len(achadas) == 1 else None
    ids: list[str] = []
    for parte in (p for p in _MULTI_SEP.split(text.strip()) if p.strip()):
        achadas = match_options(options, parte)
        if len(achadas) == 1:
            ids.append(achadas[0])
            continue
        for pedaco in (w for w in _WORD_SEP.split(parte.strip()) if w):
            sub = match_options(options, pedaco)
            if len(sub) != 1:
                return None           # um pedaço que não é opção invalida a resposta inteira
            ids.append(sub[0])
    return list(dict.fromkeys(ids)) or None


class PendingMenu:
    """O menu de escolha em aberto numa sessão de canal de texto. TTL do menu mais longo: depois
    disso, "2" é só texto."""

    def __init__(self, redis: Any, channel: str, ttl_s: int) -> None:
        self._redis = redis
        self._channel = channel
        self._ttl = ttl_s

    def _key(self, session_id: str) -> str:
        return f"channel:{self._channel}:{session_id}:menu_choice"

    async def remember(self, session_id: str, payload: dict) -> None:
        opts = options_from_menu(payload.get("options"))
        await self._redis.setex(self._key(session_id), self._ttl, json.dumps({
            "menu_id": str(payload.get("menu_id") or ""),
            "interaction": payload.get("interaction") or "",
            "options": [{"id": o.id, "label": o.label} for o in opts],
        }))

    async def forget(self, session_id: str) -> None:
        await self._redis.delete(self._key(session_id))

    async def answer(self, session_id: str, text: str) -> dict | None:
        """O `payload` do `menu_result` quando `text` responde o menu em aberto — e o menu sai de
        aberto. None quando não há menu em aberto ou quando a resposta não nomeia opção: aí o texto
        segue cru, e o motor recusa e reenvia."""
        raw = await self._redis.get(self._key(session_id))
        if not raw:
            return None
        try:
            st = json.loads(raw)
            opts = options_from_menu(st.get("options"))
            interaction = st.get("interaction") or ""
        except (TypeError, ValueError, AttributeError) as exc:
            logger.error("%s: menu em aberto ILEGIVEL (session=%s): %s — a resposta segue como texto",
                         self._channel, session_id, exc)
            return None
        valor = resolve(text, opts, interaction)
        if valor is None:
            # o valor digitado não vai ao log (pode ser qualquer coisa que o cliente escreveu)
            logger.info("%s: resposta ao menu %s nao nomeia opcao (%d car.) — segue como texto, o motor "
                        "reenvia (session=%s)", self._channel, st.get("menu_id"), len(text), session_id)
            return None
        await self.forget(session_id)
        return {"menu_id": st.get("menu_id") or "", "interaction": interaction, "result": valor}
