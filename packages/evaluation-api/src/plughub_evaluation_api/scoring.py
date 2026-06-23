"""
scoring.py — T7a — agregação determinística + validação form-driven.

O formulário (snapshot pinado, T6b) é a **fonte única** da nota: a `overall_score`
do LLM é descartada e recomputada dos valores por critério pelos pesos/tipos do form
(spec §5.2/§16.2). Aqui só há lógica pura (sem I/O) — chamada pelo `_ingest_core`.

Modelo do form: aninhado `dimensions[].criteria[]` (o que o DB armazena e a UI edita).
Cada critério carrega `type`, `weight` (dentro da dimensão), `min_score`/`max_score`,
e (T6a) `choice_scores`/`true_score`/`false_score`. `na`/`text` ficam fora do agregado.
"""
from __future__ import annotations

from typing import Any


def _iter_criteria(form: dict[str, Any]) -> list[dict[str, Any]]:
    """Achata dimensions[].criteria[] anexando o dimension_id de origem."""
    out: list[dict[str, Any]] = []
    for dim in (form.get("dimensions") or []):
        if not isinstance(dim, dict):
            continue
        dim_id = dim.get("dimension_id") or "default"
        for c in (dim.get("criteria") or []):
            if isinstance(c, dict):
                out.append({**c, "_dimension_id": c.get("dimension_id") or dim_id})
    return out


def _criterion_index(form: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["criterion_id"]: c for c in _iter_criteria(form) if c.get("criterion_id")}


def _na_allowed(crit: dict[str, Any]) -> bool:
    # tolera as variantes de nome em uso (na_allowed/allow_na/allows_na).
    return bool(crit.get("na_allowed") or crit.get("allow_na") or crit.get("allows_na"))


def _to_0_10(score: float, max_score: float) -> float:
    if not max_score:
        return 0.0
    return max(0.0, min(10.0, (score / max_score) * 10.0))


def validate_criterion_responses(
    form: dict[str, Any], responses: list[dict[str, Any]]
) -> list[str]:
    """Valida cada response contra a definição do form. Retorna lista de violações
    (vazia = ok): criterion_id existe, regra de `na`, faixa do score."""
    errors: list[str] = []
    idx = _criterion_index(form)
    for r in responses:
        cid = r.get("criterion_id")
        if not cid:
            errors.append("response missing criterion_id")
            continue
        crit = idx.get(cid)
        if not crit:
            errors.append(f"unknown criterion_id: {cid}")
            continue
        if r.get("na"):
            if not _na_allowed(crit):
                errors.append(f"{cid}: na not allowed for this criterion")
            continue
        ctype = crit.get("type") or "score"
        if ctype in ("score", "auto_computed"):
            s = r.get("score")
            if s is None:
                errors.append(f"{cid}: score required")
                continue
            lo = float(crit.get("min_score") or 0)
            hi = float(crit.get("max_score") or 10)
            try:
                sv = float(s)
            except (TypeError, ValueError):
                errors.append(f"{cid}: score not numeric ({s!r})")
                continue
            if not (lo <= sv <= hi):
                errors.append(f"{cid}: score {sv} out of range [{lo}, {hi}]")
    return errors


def _criterion_value_0_10(crit: dict[str, Any], resp: dict[str, Any]) -> float | None:
    """Valor do critério normalizado p/ 0..10 conforme o tipo. None = sem valor usável."""
    ctype = crit.get("type") or "score"
    max_score = float(crit.get("max_score") or 10)

    if ctype == "boolean":
        bv = resp.get("boolean_value")
        if bv is None:
            s = resp.get("score")
            return None if s is None else _to_0_10(float(s), max_score)
        ts, fs = crit.get("true_score"), crit.get("false_score")
        if ts is not None or fs is not None:
            v = ts if bv else fs
            return None if v is None else _to_0_10(float(v), max_score)
        return 10.0 if bv else 0.0

    if ctype == "choice":
        cv = resp.get("choice_value")
        cs = crit.get("choice_scores") or {}
        if cv is not None and cv in cs:
            return _to_0_10(float(cs[cv]), max_score)
        s = resp.get("score")
        return None if s is None else _to_0_10(float(s), max_score)

    # score / auto_computed
    s = resp.get("score")
    return None if s is None else _to_0_10(float(s), max_score)


def aggregate_scores(
    form: dict[str, Any], responses: list[dict[str, Any]]
) -> tuple[float | None, list[dict[str, Any]]]:
    """Recomputa a nota bottom-up pelos pesos do form.

    Retorna `(overall_score 0..10 | None, [{dimension_id, score}])`. `na`/`text` são
    excluídos e os pesos são re-normalizados entre os critérios aplicáveis. `None` quando
    não há nenhum critério pontuável (ex.: só text/na) — chamador faz fallback.
    """
    resp_by = {r.get("criterion_id"): r for r in responses if r.get("criterion_id")}
    # acumula por dimensão
    acc: dict[str, dict[str, float]] = {}
    for c in _iter_criteria(form):
        cid = c.get("criterion_id")
        if not cid:
            continue
        if (c.get("type") or "score") == "text":
            continue
        r = resp_by.get(cid)
        if not r or r.get("na"):
            continue
        val = _criterion_value_0_10(c, r)
        if val is None:
            continue
        w = float(c.get("weight") or 1)
        d = acc.setdefault(c.get("_dimension_id") or "default", {"ws": 0.0, "w": 0.0})
        d["ws"] += val * w
        d["w"] += w

    dim_weight = {
        d.get("dimension_id"): float(d.get("weight") or 1)
        for d in (form.get("dimensions") or [])
        if isinstance(d, dict)
    }

    by_dimension: list[dict[str, Any]] = []
    ows = ow = 0.0
    for dim_id, agg in acc.items():
        if agg["w"] == 0:
            continue
        ds = agg["ws"] / agg["w"]
        by_dimension.append({"dimension_id": dim_id, "score": round(ds, 3)})
        dw = dim_weight.get(dim_id, 1.0)
        ows += ds * dw
        ow += dw

    overall = round(ows / ow, 3) if ow > 0 else None
    return overall, by_dimension


def compute_dimension_diffs(
    ai_by_dim: list[dict[str, Any]],
    human_by_dim: list[dict[str, Any]],
    severity_min: float,
) -> list[dict[str, Any]]:
    """R8c — diff por dimensão entre a nota da IA e a re-pontuação cega do humano.

    Entradas no formato de `aggregate_scores` (`[{dimension_id, score}]`, score 0..10).
    `diff` é normalizado para 0..1 (`|ai-human|/10`) p/ comparar com `severity_min`
    (limiar de desacordo). Dimensão presente em só um lado → sem comparação
    (`diff=None`, `disagree=False`: é `na`, não discordância). União das dimensões,
    ordem estável (IA primeiro, depois extras do humano).
    """
    ai = {d["dimension_id"]: float(d.get("score") or 0.0)
          for d in ai_by_dim if d.get("dimension_id")}
    hu = {d["dimension_id"]: float(d.get("score") or 0.0)
          for d in human_by_dim if d.get("dimension_id")}

    ordered: list[str] = list(ai.keys()) + [d for d in hu if d not in ai]

    out: list[dict[str, Any]] = []
    for dim in ordered:
        a_present, h_present = dim in ai, dim in hu
        a, h = ai.get(dim), hu.get(dim)
        if a_present and h_present:
            diff = abs(a - h) / 10.0
            disagree = diff > severity_min
        else:
            diff, disagree = None, False
        out.append({
            "dimension_id": dim,
            "ai_score":     round(a, 3) if a_present else None,
            "human_score":  round(h, 3) if h_present else None,
            "diff":         round(diff, 4) if diff is not None else None,
            "disagree":     disagree,
        })
    return out


def blind_resolution_status(disagreement_count: int, flag_bias: bool) -> str:
    """R8c — status terminal da curadoria cega a partir do diff revelado.

    Sem desacordo → ``approved`` (concorda com a IA, nenhuma `CalibrationNote`).
    Com desacordo → ``recalibrated`` (gera nota[s]) ou ``bias_flagged`` se o curador
    sinalizar viés (severidade alta). Reusa o domínio de status da `curation_reviews`.
    """
    if disagreement_count <= 0:
        return "approved"
    return "bias_flagged" if flag_bias else "recalibrated"
