"""
prompt_composer.py — T8-B (spec §5.1/§16.3).

Camada de COMPOSIÇÃO do prompt do avaliador (upstream; o ai-gateway segue stateless e
genérico). Monta o prompt efetivo a partir de:

    prompt = instruções gerais (rubrica-template, resolvida ou built-in)
           + critérios do formulário (cada um com seu scoring_guidance/escala/peso)
           + RAG (calibration_notes por criterion_id)
           + transcript do segmento avaliado

Usada pelo endpoint de PREVIEW (UI) e — no T8-B2 — pela fiação de runtime
(evaluation_context_get expõe `rubric_instructions`). Aqui não há I/O: recebe os dados já
buscados e devolve o texto composto + seções estruturadas.
"""
from __future__ import annotations

from typing import Any

# Built-in factory: usada quando não há rubrica-template publicada (resolve_rubric → None).
# É o piso de qualidade — a UI Rubrica/Prompt (T8-C) permite sobrepor por tenant/campanha.
DEFAULT_RUBRIC_BODY = (
    "Você é um avaliador de qualidade. Avalie o atendimento do agente com base "
    "exclusivamente no transcript e nos critérios do formulário.\n"
    "- Pontue cada critério na sua escala (ex.: 0 = não atendido, 5 = parcial, "
    "10 = plenamente atendido); use a orientação específica do critério quando houver.\n"
    "- IGNORE critérios do tipo auto_computed — são preenchidos por métricas, não por você.\n"
    "- Para cada critério avaliado, CITE evidência referenciando o stream_entry_id da "
    "mensagem do transcript, com um trecho curto e a relação com o critério.\n"
    "- Marque na=true apenas quando o critério não se aplica (applies_when não satisfeito); "
    "explique o porquê.\n"
    "- Seja imparcial: não infira além do transcript, não penalize o canal/idioma, "
    "trate casos semelhantes de forma consistente.\n"
    "- Considere as notas de calibração do curador antes de pontuar; se contrariar uma "
    "nota, justifique explicitamente."
)


def _criterion_id(c: dict[str, Any]) -> str:
    return str(c.get("criterion_id") or c.get("id") or "")


def _criterion_label(c: dict[str, Any]) -> str:
    return str(c.get("label") or c.get("name") or _criterion_id(c) or "critério")


def render_criteria(form: dict[str, Any] | None) -> tuple[str, int]:
    """Renderiza os critérios avaliáveis (pula auto_computed) com escala, peso e
    scoring_guidance. Retorna (texto, n_critérios_avaliáveis)."""
    if not form:
        return "(formulário não informado)", 0
    lines: list[str] = []
    count = 0
    for dim in form.get("dimensions") or []:
        if not isinstance(dim, dict):
            continue
        dname = dim.get("name") or dim.get("dimension_id") or "Dimensão"
        dweight = dim.get("weight")
        lines.append(f"### Dimensão: {dname}" + (f" (peso {dweight})" if dweight is not None else ""))
        for c in dim.get("criteria") or []:
            if not isinstance(c, dict):
                continue
            ctype = c.get("type") or "score"
            cid = _criterion_id(c)
            label = _criterion_label(c)
            if ctype == "auto_computed":
                lines.append(f"- [{cid}] {label} — tipo auto_computed → NÃO avaliar (métrica).")
                continue
            count += 1
            scale = ""
            if ctype == "score":
                lo = c.get("min_score", 0)
                hi = c.get("max_score", 10)
                scale = f" escala {lo}–{hi}"
            elif ctype == "boolean":
                scale = " (true/false)"
            elif ctype == "choice":
                opts = c.get("scale") or c.get("options")
                scale = f" opções: {opts}" if opts else " (choice)"
            weight = c.get("weight")
            head = f"- [{cid}] {label} — tipo {ctype}{scale}"
            if weight is not None:
                head += f", peso {weight}"
            lines.append(head)
            q = c.get("question") or c.get("description")
            if q:
                lines.append(f"    Pergunta: {q}")
            sg = c.get("scoring_guidance")
            if sg:
                lines.append(f"    Orientação de pontuação: {sg}")
            if c.get("allow_na") and c.get("na_guidance"):
                lines.append(f"    N/A quando: {c.get('na_guidance')}")
            if c.get("applies_when"):
                lines.append(f"    Aplica-se quando: {c.get('applies_when')}")
    return ("\n".join(lines) if lines else "(sem critérios)"), count


def render_calibration_notes(notes: list[dict[str, Any]] | None) -> tuple[str, int]:
    """Renderiza notas de calibração, agrupando por critério quando há criterion_id
    (T14 c) — o avaliador recebe a orientação no bloco do critério certo."""
    if not notes:
        return "(sem notas de calibração)", 0
    lines: list[str] = []
    for n in notes:
        crit = n.get("criterion_id") or n.get("dimension_id") or "geral"
        sev = n.get("severity") or "low"
        text = n.get("text") or ""
        lines.append(f"- [{crit}] (severidade {sev}) {text}")
    return "\n".join(lines), len(notes)


def compose_rubric_prompt(
    *,
    rubric_body: str,
    rubric_source: str,
    form: dict[str, Any] | None,
    calibration_notes: list[dict[str, Any]] | None = None,
    transcript_note: str | None = None,
) -> dict[str, Any]:
    """Monta o prompt composto + seções estruturadas. Sem I/O (dados já buscados)."""
    crit_text, crit_count = render_criteria(form)
    calib_text, calib_count = render_calibration_notes(calibration_notes)
    transcript_block = transcript_note or "(o transcript do segmento é inserido em runtime)"

    sections = {
        "general_instructions": rubric_body or DEFAULT_RUBRIC_BODY,
        "criteria":             crit_text,
        "calibration_notes":    calib_text,
        "transcript":           transcript_block,
    }
    composed = (
        "## Instruções gerais de avaliação\n" + sections["general_instructions"] + "\n\n"
        "## Critérios do formulário\n" + sections["criteria"] + "\n\n"
        "## Notas de calibração (curador)\n" + sections["calibration_notes"] + "\n\n"
        "## Transcript do segmento avaliado\n" + sections["transcript"]
    )
    return {
        "composed_prompt":         composed,
        "sections":                sections,
        "rubric_source":           rubric_source,
        "criteria_count":          crit_count,
        "calibration_notes_count": calib_count,
    }
