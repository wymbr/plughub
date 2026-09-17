"""
reference.py — as frases de referência da verificação e como cada uma é julgada (VOZ-23). Puro.

Cada ITEM é falado pelo executor na chamada; as transcrições que o gateway publica na janela do item são
atribuídas a ele. Dois tipos:

  phrase  frase com texto esperado → WER por palavra (texto normalizado: minúsculas, sem acento, sem
          pontuação); `correct` = WER 0. A confiança é a MENOR das transcrições do item.
  noise   trecho sem fala → `hallucinated` = alguma transcrição não vazia apareceu na janela.

⚠️ O conjunto é FIXO de propósito: a verificação compara com a linha de base, e trocar uma frase troca a
régua. Uma frase que a instalação erra sempre ("Fatura." virou "Batura!" pela chamada, VOZ-18) não é
defeito da régua — errada na linha de base e errada hoje não é regressão. `REFERENCE_VERSION` vai no
resultado, e o relatório só compara execuções da MESMA versão.

Só há conjunto em português; outra língua no perfil é recusada antes da chamada (`unsupported_language`).
"""
from __future__ import annotations

import math
import random
import re
import struct
import unicodedata
from dataclasses import dataclass

REFERENCE_VERSION = "pt-1"
SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class Item:
    id:       str
    kind:     str            # "phrase" | "noise"
    text:     str = ""       # o que é sintetizado e esperado (só phrase)


ITEMS_PT: tuple[Item, ...] = (
    Item("p01", "phrase", "Atendente."),
    Item("p02", "phrase", "Cancelar."),
    Item("p03", "phrase", "Quero falar com um atendente."),
    Item("p04", "phrase", "Segunda via da fatura."),
    Item("p05", "phrase", "Não entendi a pergunta."),
    Item("p06", "phrase", "Meu pedido ainda não chegou."),
    Item("n01", "noise"),
    Item("p07", "phrase", "Obrigado, era só isso."),
)


def items_for(language: str) -> tuple[Item, ...] | None:
    """O conjunto da língua (`pt`, `pt-BR`), ou None se não há."""
    return ITEMS_PT if (language or "").lower().split("-")[0] == "pt" else None


def noise_pcm(seconds: float = 2.0, rms: float = 1500.0, seed: int = 23) -> bytes:
    """Ruído branco determinístico em PCM16 mono a 16 kHz — passa o limiar de energia do gateway."""
    rnd = random.Random(seed)
    n = int(SAMPLE_RATE * seconds)
    amostras = (max(-32768, min(32767, int(rnd.gauss(0.0, rms)))) for _ in range(n))
    return struct.pack(f"<{n}h", *amostras)


def normalize(text: str) -> list[str]:
    semacento = unicodedata.normalize("NFKD", text or "")
    semacento = "".join(c for c in semacento if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", semacento.lower())


def wer(ref: list[str], hyp: list[str]) -> float:
    """Word error rate: distância de edição por palavra / palavras da referência."""
    if not ref:
        return 0.0 if not hyp else 1.0
    ant = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(ant[j] + 1, cur[j - 1] + 1, ant[j - 1] + (r != h))
        ant = cur
    return round(ant[-1] / len(ref), 4)


def judge(item: Item, transcripts: list[tuple[str, float | None]]) -> dict:
    """O veredicto de UM item — só números e booleanos, nunca o texto transcrito."""
    textos = [t for t, _ in transcripts if (t or "").strip()]
    confs = [c for t, c in transcripts if (t or "").strip() and c is not None]
    base = {"id": item.id, "kind": item.kind, "transcripts": len(textos),
            "confidence": round(min(confs), 4) if confs else None}
    if item.kind == "noise":
        return {**base, "hallucinated": bool(textos), "correct": None, "wer": None}
    w = wer(normalize(item.text), normalize(" ".join(textos)))
    return {**base, "hallucinated": None, "correct": w == 0.0, "wer": w}


def _pct(valores: list[float], q: float) -> float | None:
    if not valores:
        return None
    s = sorted(valores)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 4)


def summarize(judged: list[dict]) -> dict:
    """Os agregados da execução. Sem frase medida, os agregados são None — nunca 0."""
    frases = [j for j in judged if j["kind"] == "phrase"]
    ruidos = [j for j in judged if j["kind"] == "noise"]
    confs = [j["confidence"] for j in frases if j["confidence"] is not None]
    certas = sum(1 for j in frases if j["correct"])
    return {
        "phrases_total":       len(frases),
        "phrases_correct":     certas,
        "phrases_transcribed": sum(1 for j in frases if j["transcripts"]),
        "accuracy":            round(certas / len(frases), 4) if frases else None,
        "wer_mean":            round(sum(j["wer"] for j in frases) / len(frases), 4) if frases else None,
        "confidence_p10":      _pct(confs, 0.1),
        "confidence_p50":      _pct(confs, 0.5),
        "confidence_p90":      _pct(confs, 0.9),
        "noise_total":         len(ruidos),
        "hallucinations":      sum(1 for j in ruidos if j["hallucinated"]),
    }
