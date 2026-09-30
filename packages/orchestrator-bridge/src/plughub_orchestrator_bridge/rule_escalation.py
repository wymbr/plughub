"""
rule_escalation.py — a escalação por REGRA chega ao dono da ativação (RUL-02, 2026-09-30).

O rules-engine decide que uma regra disparou e publica `rules.escalation.events`. Quem pode
tirar o contato da IA sem deixá-la rodando é este serviço, que a ativou. Aqui fica a DECISÃO,
pura e testável; o `main.py` só liga o consumidor e acorda a IA.

Decisões do dono (2026-09-30):
  1. **A IA para na fronteira do passo.** Nada é interrompido no meio: o bridge grava a marca
     e acorda a espera; o engine de skill-flow vê a marca no topo do próximo passo e a IA
     escala a si mesma pelo `escalate` de sempre (`skill-flow-engine/src/rule-preemption.ts`).
  2. **Só com IA conduzindo e sem humano na sessão.** A regra não arranca o contato de uma
     pessoa atendendo; com humano presente, recusa e diz por quê.
  3. **Uma vez por sessão.** A regra avalia a cada turno e dispara de novo; a marca é SET NX
     e os disparos seguintes morrem aqui, contados no log. Evita pingue-pongue se o destino
     for outro pool de IA que também dispara regra.
  4. **O aviso ao cliente é da regra**, opcional; viaja na marca e o engine o envia.

Quem CONDUZ é lido do roster `session:{id}:participants` (o mesmo que decide @mention e
escalação no mcp-server): entrada sem `left_at`, `role == "primary"`. Humano é `agent_type`
`human` (ou está em `session:{id}:human_agents`); a IA nativa tem `participant_id` = instância.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

MARK_TTL_S = 86_400
MAX_NOTICE = 500


def mark_key(session_id: str) -> str:
    return f"session:{session_id}:rule_escalation"


@dataclass(frozen=True)
class Decision:
    """`ok` = marcar e acordar `instance_id`; senão `reason` diz por que não."""
    ok:          bool
    reason:      str
    instance_id: str = ""


def validate_event(ev: dict) -> str:
    """'' se o evento serve; senão o motivo da recusa."""
    if not isinstance(ev, dict):
        return "evento ilegível"
    for f in ("session_id", "tenant_id", "rule_id", "target_pool"):
        if not isinstance(ev.get(f), str) or not ev.get(f):
            return f"campo '{f}' ausente"
    if ev.get("shadow_mode") is True:
        return "evento de shadow no tópico de escalação — shadow mede, não age"
    notice = ev.get("customer_notice")
    if notice is not None and (not isinstance(notice, str) or len(notice) > MAX_NOTICE):
        return "customer_notice inválido"
    return ""


def decide_conductor(roster: list | None, human_agents: set[str]) -> Decision:
    """Quem conduz, e se a regra pode tirá-lo. Sem leitura positiva, NÃO age."""
    if human_agents:
        return Decision(False, "human_present")
    if not isinstance(roster, list):
        return Decision(False, "no_roster")
    ativos = [p for p in roster if isinstance(p, dict) and not p.get("left_at")]
    if any(p.get("agent_type") == "human" for p in ativos):
        return Decision(False, "human_present")
    ia = [p for p in ativos
          if p.get("role") == "primary" and p.get("agent_type") == "native" and p.get("participant_id")]
    if not ia:
        return Decision(False, "no_ai_conductor")
    if len(ia) > 1:
        # A costura da passagem de bastão tem dois primary por ~100 ms. Escolher um seria
        # adivinhar quem para; o próximo turno decide com o roster assentado.
        return Decision(False, "ambiguous_conductor")
    return Decision(True, "ok", str(ia[0]["participant_id"]))


def build_mark(ev: dict, instance_id: str) -> str:
    mark = {
        "instance_id": instance_id,
        "target_pool": ev["target_pool"],
        "rule_id":     ev["rule_id"],
        "rule_name":   ev.get("rule_name") or "",
        "marked_at":   datetime.now(timezone.utc).isoformat(),
    }
    notice = (ev.get("customer_notice") or "").strip()
    if notice:
        mark["customer_notice"] = notice
    return json.dumps(mark, ensure_ascii=False)


def preempt_signal() -> str:
    """O sinal que acorda o menu/resolve em espera BLOQUEADA. Não decide nada: quem escala é
    o topo do loop do engine, antes de o `on_failure` do passo rodar."""
    return json.dumps({"_rule_preempt": True})
