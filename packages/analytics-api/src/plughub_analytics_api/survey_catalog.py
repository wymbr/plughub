"""
survey_catalog.py — catálogo canônico dos instrumentos de pesquisa (Customer Surveys S1).

FONTE ÚNICA da definição por métrica: escala, direção, bandas de rótulo e roll-up.
Derivado da tabela canônica de `docs/arcos/customer-surveys.md` §4.

Por que um catálogo, e não `if metric == ...`:
a mesma verdade estava em TRÊS lugares — os `if` do consumer (`models.py`), o
`CV_INSTRUMENTS` do relatório (`reports_query.py`) e a lista da UI
(`AnaliseSurveysPage.tsx`) — e já tinham divergido: o CES estava com
`higher_is_better: False` no relatório contra "nota alta = bom (baixo esforço)" na
spec. Fato definido em N lugares diverge por construção; aqui ele mora em um só, e
o relatório + o consumer o importam.

Por que a `scale` carimbada pelo produtor NÃO é a fonte:
ela é opcional (`SurveySignalSchema.scale`), ausente em 100% do caminho web
(`survey_web.py`) e no caminho legado `capture.metric` do `survey_record`, e dá
apenas o INTERVALO — não a direção nem os pontos de corte (`min=1,max=3` não diz que
PMF 1 é o melhor; `0–10` não distingue NPS de um CSAT 0–10, que tem outro corte).
Quando presente e divergente do catálogo, ela é usada para RE-ESCALAR o valor antes
de escolher a banda (ver `label_for`) — nunca para inventar a semântica.
"""
from __future__ import annotations

from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Catálogo
#
# bands: [(limite_inferior_inclusivo, label)] — da MELHOR faixa para a PIOR, na
#   escala do catálogo. A primeira faixa cujo limite o valor alcança vence.
# rollup: como a série diária é agregada na lente Customer Voice
#   "avg"       — média do valor (CSAT, CES)
#   "nps_index" — %promotores − %detratores (NPS)
#   "pct"       — % das respostas que satisfazem `rollup_cond` (PMF, FCR)
# higher_is_better: refere-se ao VALOR EXIBIDO (o roll-up), não ao número cru —
#   é o que a UI usa para colorir. PMF é o caso que exige a distinção: o número cru
#   é invertido (1 = "muito decepcionado" = melhor sinal), mas o indicador exibido é
#   "% muito decepcionado", onde maior é melhor.
#
# Rótulos (decisão 2026-07-27): CES/PMF/FCR nascem em INGLÊS (spec + invariante de
# linguagem do CLAUDE.md — inglês no código, PT só em i18n). NPS/CSAT permanecem em
# pt-BR porque já há histórico gravado assim; a UI traduz por i18n. A unificação do
# legado está registrada no TODO.
# ─────────────────────────────────────────────────────────────────────────────

SURVEY_INSTRUMENTS: dict[str, dict[str, Any]] = {
    "nps": {
        "source":            "customer_nps",
        "label":             "NPS",
        "scale":             (0, 10),
        "bands":             [(9, "promotor"), (7, "neutro"), (0, "detrator")],
        "rollup":            "nps_index",
        "higher_is_better":  True,
        "grains":            ["segment", "session", "journey"],
    },
    "csat": {
        "source":            "customer_csat",
        "label":             "CSAT",
        "scale":             (1, 5),
        "bands":             [(4, "satisfeito"), (3, "neutro"), (1, "insatisfeito")],
        "rollup":            "avg",
        "higher_is_better":  True,
        "grains":            ["segment", "session", "journey"],
    },
    "ces": {
        "source":            "customer_ces",
        "label":             "CES",
        "scale":             (1, 7),
        # Spec: nota ALTA = bom (baixo esforço). O catálogo do relatório dizia o
        # contrário (`higher_is_better: False`) — divergência corrigida aqui.
        "bands":             [(5, "low_effort"), (4, "neutral"), (1, "high_effort")],
        "rollup":            "avg",
        "higher_is_better":  True,
        "grains":            ["segment", "session", "journey"],
    },
    "pmf": {
        "source":            "customer_pmf",
        "label":             "PMF (% muito decepcionado)",
        "scale":             (1, 3),
        "bands":             [(3, "not_disappointed"), (2, "somewhat_disappointed"),
                              (1, "very_disappointed")],
        # Sean Ellis: o indicador é a FATIA de "very_disappointed" (alvo ≥ 40%),
        # não a média de uma escala categórica — `avg` sobre 1–3 não significa nada.
        "rollup":            "pct",
        "rollup_cond":       "value_num <= 1",
        "higher_is_better":  True,
        "grains":            ["session", "journey"],
    },
    "fcr": {
        # FCR percebido (perguntado). O determinístico vive em session_metric.fcr.
        "source":            "customer_survey",
        "label":             "FCR (percebido)",
        "scale":             (0, 1),
        "bands":             [(1, "resolved"), (0, "unresolved")],
        # Binário: o indicador é % resolvido, não "média 0,62".
        "rollup":            "pct",
        "rollup_cond":       "value_num >= 1",
        "higher_is_better":  True,
        "grains":            ["session", "journey"],
    },
}

# Fallback para métricas fora do catálogo (pesquisa customizada do tenant): valor
# passa cru, sem rótulo. Nunca inventar semântica para métrica desconhecida.
DEFAULT_SOURCE = "customer_survey"


def source_for(metric: str) -> str:
    """`session_signal.source` da métrica (fallback `customer_survey`)."""
    inst = SURVEY_INSTRUMENTS.get(metric)
    return inst["source"] if inst else DEFAULT_SOURCE


def _rescale(value: float, frm: tuple[float, float], to: tuple[float, float]) -> float:
    """Converte linearmente `value` da escala do PRODUTOR para a do catálogo.

    Só para ESCOLHER a banda — `value_num` gravado continua sendo o valor cru que o
    cliente respondeu (a verdade do respondente não é reescrita). Ex.: CSAT 1–10
    respondendo 8 → 4.1 na escala 1–5 → banda `satisfeito`.
    """
    f_min, f_max = frm
    t_min, t_max = to
    if f_max == f_min:
        return value
    return t_min + (value - f_min) * (t_max - t_min) / (f_max - f_min)


def label_for(
    metric: str,
    value: float,
    scale: dict | None = None,
) -> str | None:
    """Rótulo categórico da métrica para o valor, ou `None` quando não catalogada.

    `scale` = snapshot carimbado pelo produtor (`{min,max}`). Quando presente e
    diferente da escala do catálogo, o valor é re-escalado antes da comparação —
    é o caso que a spec prevê (CSAT 1–5 × 1–10 têm cortes diferentes).
    """
    inst = SURVEY_INSTRUMENTS.get(metric)
    if not inst:
        return None

    v = float(value)
    cat_scale = inst["scale"]
    if isinstance(scale, dict):
        s_min, s_max = scale.get("min"), scale.get("max")
        if s_min is not None and s_max is not None:
            try:
                src = (float(s_min), float(s_max))
                if src != tuple(float(x) for x in cat_scale):
                    v = _rescale(v, src, (float(cat_scale[0]), float(cat_scale[1])))
            except (TypeError, ValueError):
                pass

    for threshold, label in inst["bands"]:
        if v >= threshold:
            return label
    # Abaixo da menor banda (valor fora da escala): usa a pior faixa declarada.
    return inst["bands"][-1][1] if inst["bands"] else None
