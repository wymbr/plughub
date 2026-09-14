"""
resume_authority.py — APR-11 (2026-09-14)

O que um RESUME pode afirmar sobre quem decide, e o que só o servidor carimba.

Decisão do dono: num `suspend reason: approval` o aprovador é um SISTEMA (a operadora, na
portabilidade), e a posse do `resume_token` — opaco, de uso único — é a credencial. A porta
certa para um sistema é a EXTERNA (`/channel/webhook/resume/{token}`). Medido antes, ao vivo:

  - a porta externa descartava `decision`, e o bridge assume `input` → `on_resume`: a
    operadora RECUSOU a portabilidade e o processo seguiu como APROVADO;
  - a porta externa não aplicava a regra da AUT-46: a tarefa de promoção de deploy, que a
    rota interna recusa com 401 sem credencial, foi APROVADA pela externa (`efetuar_promocao`);
  - o carimbo `verification_class` só era escrito com aprovador ou `field_edits`, e por
    `setdefault` — resume anônimo ficava sem carimbo (o portão `== claimed` do fluxo não
    disparava) e o CHAMADOR podia declarar `possessed` no próprio corpo.

Duas funções puras, uma por fato:
  judge_external_decision — a decisão que a porta externa aceita, pelo motivo do suspend
  server_trust_stamp      — a classe de confiança do autor, que nunca vem do corpo
"""
from __future__ import annotations

from typing import Any

# Motivo de suspend cujo sinal é uma DECISÃO de um sistema externo.
SYSTEM_APPROVAL_REASON = "approval"
# O que um sistema externo decide. `timeout` e encerramento seguem sendo de quem tem principal.
EXTERNAL_DECISIONS: frozenset[str] = frozenset({"approved", "rejected"})


def judge_external_decision(decision: Any, suspend_reason: str | None) -> tuple[str, str | None]:
    """
    `("accept", decisão)` · `("drop", None)` · `("refuse", mensagem)`.

    Só `suspend reason: approval` recebe decisão pela porta externa, e ali ela é
    OBRIGATÓRIA: sem ela o bridge seguiria `on_resume`, e aprovação nunca é o default.
    Nos demais (form-fill, coleta, delegate), o campo segue descartado como antes — um
    sistema externo responde um resume, não encerra o trabalho de um humano.
    """
    if suspend_reason == SYSTEM_APPROVAL_REASON:
        if isinstance(decision, str) and decision in EXTERNAL_DECISIONS:
            return "accept", decision
        return "refuse", (
            "approval resume requires decision "
            + " | ".join(sorted(EXTERNAL_DECISIONS))
        )
    return "drop", None


def server_trust_stamp(approver: dict[str, Any] | None) -> dict[str, str]:
    """A classe de confiança do AUTOR do resume — do principal verificado, ou `claimed`/`system`."""
    if approver is not None:
        return {
            "verification_class": str(approver.get("verification_class", "possessed")),
            "principal_type":     str(approver.get("principal_type", "human")),
        }
    return {"verification_class": "claimed", "principal_type": "system"}
