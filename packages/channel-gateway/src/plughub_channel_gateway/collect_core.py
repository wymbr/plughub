"""
collect_core.py — a SEMÂNTICA da coleta por teclado e fala, em uma casa (VOZ-05 fatia 5b).

Decisão do dono (`TODO.md` § *VOZ-05 fatia 5*): o canal APRESENTA e CAPTURA; o que é uma
resposta válida, quando a entrada terminou e qual o desfecho é daqui, igual para todo canal. O
defeito medido antes não era de apresentação: no WebRTC qualquer fala respondia qualquer menu, e
nem o bridge nem o motor conferiam as opções.

Sem I/O e sem relógio próprio: o renderizador entrega tecla, fala e o `now` (monotônico), e
recebe AÇÕES — eco, nova tentativa, desfecho. Assim os timers são testáveis sem dormir.

Três desfechos, nunca fundidos (decisão 2):
  value    a entrada casou (opção, dígitos, fala)
  invalid  `max_invalid` entradas que não casaram — antes disso, cada uma é só uma TENTATIVA
  timeout  nenhuma entrada por `first_input_timeout_s`
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

# Interações que o núcleo decompõe hoje. `checklist` e `form` são vários valores numa resposta
# só; a decomposição em passos unitários fica para quando houver canal sem tela (NIV-13/16).
SUPPORTED_INTERACTIONS = ("text", "button", "list")

DIGIT_WORDS: dict[str, str] = {
    "0": "zero", "1": "um", "2": "dois", "3": "três", "4": "quatro", "5": "cinco",
    "6": "seis", "7": "sete", "8": "oito", "9": "nove", "*": "asterisco", "#": "jogo da velha",
}
# fala → dígito; inclui as formas que o STT escreve sem acento ou no feminino
_SPOKEN_DIGITS: dict[str, str] = {
    "zero": "0", "um": "1", "uma": "1", "dois": "2", "duas": "2", "tres": "3", "quatro": "4",
    "cinco": "5", "seis": "6", "meia": "6", "sete": "7", "oito": "8", "nove": "9", "dez": "10",
}
_KEY_PREFIXES = {"opcao", "numero", "tecla", "a", "o", "e"}
_DIGIT_FILLERS = {"e", "numero", "digito", "digitos", "tracinho", "traco", "ponto", "virgula"}


class CollectNotApplicable(ValueError):
    """O menu declara coleta por teclado/fala, mas o núcleo não a executa — o motivo é a mensagem."""


@dataclass(frozen=True)
class Option:
    id:    str
    label: str
    key:   str


@dataclass(frozen=True)
class CollectPlan:
    menu_id:          str
    interaction:      str
    prompt:           str
    options:          tuple[Option, ...]
    inputs:           frozenset[str]
    first_timeout_s:  float
    inter_digit_s:    float
    min_digits:       int
    max_digits:       int | None
    terminator:       str | None
    domain:           str
    echo:             str
    barge_in:         bool
    min_confidence:   float | None
    invalid_message:  str | None
    max_invalid:      int | None
    end_silence_ms:   int | None = None
    max_speech_ms:    int | None = None

    @classmethod
    def from_menu(cls, payload: dict) -> "CollectPlan | None":
        """`None` = o menu não pede coleta por teclado nem fala (o canal coleta como sempre).
        `CollectNotApplicable` = pede, e o núcleo não pode executá-la — o chamador diz por quê."""
        c = payload.get("collect")
        if not isinstance(c, dict):
            return None
        inputs = frozenset(x for x in (c.get("input") or []) if x in ("dtmf", "voice", "text"))
        if not inputs & {"dtmf", "voice"}:
            return None
        interaction = payload.get("interaction") or ""
        if interaction not in SUPPORTED_INTERACTIONS:
            raise CollectNotApplicable(
                f"interaction {interaction!r} nao e decomposta em coleta unitaria "
                f"(so {', '.join(SUPPORTED_INTERACTIONS)})")
        first = c.get("first_input_timeout_s")
        if not isinstance(first, (int, float)) or first <= 0:
            # o schema recusa; chegar aqui é produtor fora do contrato, e esperar para sempre
            # por uma tecla é exatamente o que a recusa existe para impedir
            raise CollectNotApplicable("collect sem first_input_timeout_s positivo")
        options = options_from_menu(payload.get("options"))
        if interaction in ("button", "list") and not options:
            raise CollectNotApplicable(f"menu {interaction!r} sem opcoes")
        voice = c.get("voice") if isinstance(c.get("voice"), dict) else {}
        return cls(
            menu_id         = str(payload.get("menu_id") or ""),
            interaction     = interaction,
            prompt          = str(payload.get("prompt") or ""),
            options         = options,
            inputs          = inputs,
            first_timeout_s = float(first),
            inter_digit_s   = float(c.get("inter_digit_timeout_s") or first),
            min_digits      = int(c.get("min_digits") or 1),
            max_digits      = int(c["max_digits"]) if c.get("max_digits") else None,
            terminator      = c.get("terminator") if c.get("terminator") in ("#", "*") else None,
            domain          = c.get("domain") or ("text" if "voice" in inputs and "dtmf" not in inputs else "digits"),
            echo            = c.get("echo") or "none",
            barge_in        = c.get("barge_in", True) is not False,
            min_confidence  = voice.get("min_confidence"),
            invalid_message = c.get("invalid_message") or None,
            max_invalid     = int(c["max_invalid"]) if c.get("max_invalid") else None,
            end_silence_ms  = int(voice["end_silence_ms"]) if voice.get("end_silence_ms") else None,
            max_speech_ms   = int(float(voice["max_speech_s"]) * 1000) if voice.get("max_speech_s") else None,
        )

    @property
    def speech_params(self) -> tuple[str, ...]:
        """Os parâmetros de segmentação de fala que o menu declarou."""
        return tuple(n for n, v in (("end_silence_ms", self.end_silence_ms),
                                    ("max_speech_s", self.max_speech_ms)) if v)

    @property
    def is_option_menu(self) -> bool:
        return bool(self.options) and self.interaction in ("button", "list")

    @property
    def is_digit_field(self) -> bool:
        """Campo aberto de dígitos — o que a TELA também precisa validar (VOZ-05 fatia 5c)."""
        return self.interaction == "text" and self.domain in ("digits", "digits_star_hash")

    def accepts_digits(self, value: str) -> bool:
        """A mesma regra da coleta por tecla, para um valor inteiro que chegou de uma vez (campo na
        tela). O terminador, se veio no fim, não faz parte do valor."""
        if self.terminator and value.endswith(self.terminator):
            value = value[:-1]
        permitidos = "0123456789" + ("*#" if self.domain == "digits_star_hash" else "")
        return (bool(value) and all(ch in permitidos for ch in value)
                and len(value) >= self.min_digits
                and (self.max_digits is None or len(value) <= self.max_digits))

    def screen_view(self) -> dict:
        """O que a TELA precisa para desenhar o teclado e o campo — nunca mensagens nem prazos."""
        view: dict = {"input": sorted(self.inputs), "domain": self.domain, "min_digits": self.min_digits}
        if self.max_digits is not None:
            view["max_digits"] = self.max_digits
        if self.terminator:
            view["terminator"] = self.terminator
        return view

    def spoken_prompt(self) -> str:
        """O prompt como o autor o escreveu, mais como responder — cada opção com a sua tecla
        e/ou o que dizer. A tela continua mostrando as opções; isto é o que se OUVE."""
        partes = [self.prompt.strip()] if self.prompt.strip() else []
        tecla, fala = "dtmf" in self.inputs, "voice" in self.inputs
        if self.is_option_menu:
            if tecla:
                for o in self.options:
                    tecla_txt = f"tecle {' '.join(DIGIT_WORDS[d] for d in o.key)}"
                    partes.append(f"Para {o.label}, {tecla_txt}{f' ou diga {o.label}' if fala else ''}.")
            else:
                labels = [o.label for o in self.options]
                partes.append("Diga " + (", ".join(labels[:-1]) + " ou " + labels[-1] if len(labels) > 1 else labels[0]) + ".")
        elif tecla and self.terminator:
            partes.append(f"Digite e termine com {DIGIT_WORDS[self.terminator]}.")
        return " ".join(partes)


@dataclass(frozen=True)
class Echo:
    """Uma tecla aceita no buffer — o renderizador aplica `plan.echo` (fala, bipe ou nada)."""
    key: str


@dataclass(frozen=True)
class Retry:
    """Entrada que não casou, ainda sem esgotar `max_invalid`. `message` ausente = sem eco."""
    count:   int
    message: str | None


@dataclass(frozen=True)
class Done:
    # `aborted` (NIV-07) não nasce do núcleo: é o ADAPTER desfazendo uma coleta que não conseguiu
    # proteger (a pausa de mídia falhou, ou alguém entrou na sala durante o bloco mascarado)
    outcome: Literal["value", "invalid", "timeout", "aborted"]
    value:   str | None = None
    via:     Literal["dtmf", "voice", ""] = ""


Action = Echo | Retry | Done


def options_from_menu(raw_opts: object) -> tuple[Option, ...]:
    """As opções do `menu.payload` com a TECLA de cada uma (1, 2, …) — a mesma numeração no
    teclado do telefone e no texto numerado do SMS/WhatsApp (NIV-14/17). Opção sem id não entra:
    ninguém conseguiria responder com ela."""
    return tuple(
        Option(id=str(o.get("id", "")), label=str(o.get("label") or o.get("id") or ""), key=str(i + 1))
        for i, o in enumerate(raw_opts if isinstance(raw_opts, list) else [])
        if isinstance(o, dict) and o.get("id") not in (None, "")
    )


def match_options(options: tuple[Option, ...], text: str) -> list[str]:
    """Os ids das opções que `text` nomeia: a tecla SOZINHA ("2", "dois", "opção dois") ou o
    rótulo/id como sequência de palavras. Uma casa para a fala transcrita e para o texto digitado —
    "quero um boleto" não é a opção 1, em canal nenhum."""
    tokens = _norm(text)
    resto = [t for t in tokens if t not in _KEY_PREFIXES]
    achadas: list[str] = []
    for o in options:
        if len(resto) == 1 and (resto[0] == o.key or _SPOKEN_DIGITS.get(resto[0]) == o.key):
            achadas.append(o.id)
            continue
        for alvo in (_norm(o.label), _norm(o.id.replace("_", " "))):
            n = len(alvo)
            if n and any(tokens[i:i + n] == alvo for i in range(len(tokens) - n + 1)):
                achadas.append(o.id)
                break
    return list(dict.fromkeys(achadas))


def _norm(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text.casefold())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9#*]+", " ", t).split()


@dataclass
class CollectSession:
    """Uma coleta em curso. O timer da primeira entrada só corre depois de `arm` — o renderizador
    arma quando o prompt termina de tocar (ou quando não há o que tocar)."""
    plan:       CollectPlan
    armed_at:   float | None = None
    buffer:     str = ""
    last_key:   float = 0.0
    invalids:   int = 0
    done:       Done | None = field(default=None)
    # VOZ-22 — só contagens, para a telemetria; nada do que foi dito ou teclado
    speech_inputs:          int = 0
    digit_inputs:           int = 0
    invalid_low_confidence: int = 0
    digit_after_speech:     bool = False

    def counters(self) -> dict:
        return {"speech_inputs": self.speech_inputs, "digit_inputs": self.digit_inputs,
                "invalid_attempts": self.invalids, "invalid_low_confidence": self.invalid_low_confidence,
                "digit_after_speech": self.digit_after_speech}

    def arm(self, now: float) -> None:
        if self.armed_at is None and self.done is None:
            self.armed_at = now

    # ── entradas ──────────────────────────────────────────────────────────────

    def digit(self, key: str, now: float) -> list[Action]:
        if self.done is not None or "dtmf" not in self.plan.inputs or key not in DIGIT_WORDS:
            return []
        self.digit_inputs += 1
        if self.speech_inputs:
            self.digit_after_speech = True
        self.arm(now)
        p = self.plan
        if p.is_option_menu:
            self.buffer += key
            self.last_key = now
            candidatas = [o for o in p.options if o.key.startswith(self.buffer)]
            if not candidatas:
                return [Echo(key), *self._invalid(now)]
            exata = next((o for o in candidatas if o.key == self.buffer), None)
            if exata is not None and len(candidatas) == 1:
                return [Echo(key), self._finish("value", exata.id, "dtmf")]
            return [Echo(key)]
        # campo aberto
        if p.terminator and key == p.terminator:
            return self._close_digits(now) if self.buffer else self._invalid(now)
        if key in "*#" and p.domain != "digits_star_hash":
            return [Echo(key), *self._invalid(now)]
        self.buffer += key
        self.last_key = now
        if p.max_digits and len(self.buffer) >= p.max_digits:
            return [Echo(key), *self._close_digits(now)]
        return [Echo(key)]

    def speech(self, transcript: str, confidence: float | None, now: float) -> list[Action]:
        """Fala final do cliente. Fora do modo `voice` não é resposta — o chamador nem chama.
        `confidence` None = não medida: o limite não se aplica (o renderizador diz no log)."""
        if self.done is not None or "voice" not in self.plan.inputs:
            return []
        self.speech_inputs += 1
        self.arm(now)
        p = self.plan
        if p.min_confidence is not None and confidence is not None and confidence < p.min_confidence:
            self.invalid_low_confidence += 1
            return self._invalid(now)
        tokens = _norm(transcript)
        if not tokens:
            return []
        if p.is_option_menu:
            achadas = self._match_options(tokens)
            if len(achadas) != 1:
                return self._invalid(now)
            return [self._finish("value", achadas[0], "voice")]
        if p.domain in ("digits", "digits_star_hash"):
            digitos = self._spoken_digits(tokens)
            if digitos is None or not self._digits_ok(digitos):
                return self._invalid(now)
            return [self._finish("value", digitos, "voice")]
        return [self._finish("value", transcript.strip(), "voice")]

    def tick(self, now: float) -> list[Action]:
        if self.done is not None:
            return []
        p = self.plan
        if self.buffer and now - self.last_key >= p.inter_digit_s:
            if p.is_option_menu:
                exata = next((o for o in p.options if o.key == self.buffer), None)
                if exata is not None:
                    return [self._finish("value", exata.id, "dtmf")]
                return self._invalid(now)
            return self._close_digits(now)
        if not self.buffer and self.armed_at is not None and now - self.armed_at >= p.first_timeout_s:
            return [self._finish("timeout")]
        return []

    # ── internos ──────────────────────────────────────────────────────────────

    def _digits_ok(self, digits: str) -> bool:
        p = self.plan
        return len(digits) >= p.min_digits and (p.max_digits is None or len(digits) <= p.max_digits)

    def _close_digits(self, now: float) -> list[Action]:
        valor, self.buffer = self.buffer, ""
        if self._digits_ok(valor):
            return [self._finish("value", valor, "dtmf")]
        return self._invalid(now)

    def _invalid(self, now: float) -> list[Action]:
        self.buffer = ""
        self.invalids += 1
        if self.plan.max_invalid is not None and self.invalids >= self.plan.max_invalid:
            return [self._finish("invalid")]
        self.armed_at = now          # nova tentativa: o prazo da primeira entrada recomeça
        return [Retry(self.invalids, self.plan.invalid_message)]

    def _finish(self, outcome: Literal["value", "invalid", "timeout"], value: str | None = None,
                via: Literal["dtmf", "voice", ""] = "") -> Done:
        self.buffer = ""
        self.done = Done(outcome, value, via)
        return self.done

    def _match_options(self, tokens: list[str]) -> list[str]:
        return match_options(self.plan.options, " ".join(tokens))

    def _spoken_digits(self, tokens: list[str]) -> str | None:
        out = []
        for t in tokens:
            if t.isdigit() or (t in ("*", "#") and self.plan.domain == "digits_star_hash"):
                out.append(t)
            elif t in _SPOKEN_DIGITS:
                out.append(_SPOKEN_DIGITS[t])
            elif t in _DIGIT_FILLERS:
                continue
            else:
                return None
        return "".join(out) or None
