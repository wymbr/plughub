# -*- coding: utf-8 -*-
"""DEFAULT_CONTEXT_MAP — o espelho Python do mapa embutido.

── Por que ele mora AQUI, e não mais no `seed.py` do config-api (2026-09-02) ────

Ele sempre foi cópia de `DEFAULT_CONTEXT_MAP` em `@plughub/schemas/context-map.ts`, que
segue sendo a AUTORIDADE — é ela que o oráculo `verifyContextMap` julga e que o runtime do
mcp-server usa como fallback. O que mudou é a casa, e o motivo é a ALW-02 passo 3: o
carregador Python precisa do mesmo fallback que o TS tem, e sem ele as duas metades
divergiriam **exatamente durante uma queda do config-api** — o TS carimbando
`{tipo: "cpf_br", origem: "canonical", fallback: true}` e o Python carimbando
`{origem: "unknown", fallback: true}` para o mesmo campo.

Essa divergência seria pior que qualquer das duas sozinha, e por uma razão concreta:
`unknown` é a população que a V4 conta. Uma queda de config-api despejaria ruído de
indisponibilidade dentro do número que autoriza inverter o default, e só quem soubesse
filtrar por `fallback` perceberia.

**O número de cópias não mudou** — eram duas (TS + `seed.py`), continuam duas (TS + esta).
O `seed.py` passou a importar daqui.

⚠️ **Gerado da TS, nunca digitado à mão.** A divergência não é confiada à disciplina:
`infra/test/probe_context_map_audit.sh` compara a config VIVA contra a TS e reprova.

⚠️ **Fonte de verdade em runtime é o STORE, não este arquivo.** O seed é seed-if-absent:
editar aqui depois de a base estar semeada é NO-OP. Este valor só serve para (a) semear base
vazia e (b) ser o fallback de quem não conseguiu falar com o config-api.
"""
from __future__ import annotations

from typing import Any

__all__ = ["DEFAULT_CONTEXT_MAP"]

#: O mapa é a ALLOWLIST, e `mode: "audit"` não recusa nada — apenas conta. O enum tem UM
#: valor de propósito: não existe config capaz de ligar imposição antes de a V4 escrever o
#: código que a honre.
DEFAULT_CONTEXT_MAP: dict[str, Any] = {
    "mode": "audit",
    # CNS-03 — `core.segment.` entra junto porque a CNS-02 reservou o root
    # `core.*`: quando a escrita do bridge virar `core.segment.{segId}.…`, sem
    # este prefixo ela deixaria de ser FAMÍLIA dinâmica e passaria a contar como
    # `unknown`, inflando justamente o número que autoriza a V4 com um campo que
    # é impossível declarar folha a folha.
    "dynamic_prefixes": ["agent.", "segment.", "core.segment."],
    "contexto": {
        "core": {
            "contact": {
                "close_origin": {"tipo": "texto", "legado": ["session.contato.close_origin", "session.close_origin"]},
                "customer_participant_id": {"tipo": "texto", "legado": ["session.contato.customer_participant_id", "session.customer_participant_id"]},
                "human_agent_participant_id": {"tipo": "texto", "legado": ["session.contato.human_agent_participant_id", "session.human_agent_participant_id"]},
                "last_primary_agent_key": {"tipo": "texto", "legado": ["session.contato.last_primary_agent_key", "session.last_primary_agent_key"]},
                "last_primary_segment_id": {"tipo": "texto", "legado": ["session.contato.last_primary_segment_id", "session.last_primary_segment_id"]},
                "root_session_id": {"tipo": "texto", "legado": ["session.contato.root_session_id", "session.root_session_id"]},
                "spawn_reason": {"tipo": "texto", "legado": ["session.contato.spawn_reason", "session.spawn_reason"]}
            },
            "copilot": {
                "last_analysis": {"tipo": "texto", "legado": ["session.copilot.ultima_analise"]},
                "recommended_actions": {"tipo": "texto", "legado": ["session.copilot.acoes_recomendadas"]},
                "risk_flags": {"tipo": "texto", "legado": ["session.copilot.flags_risco"]},
                "suggested_reply": {"tipo": "texto", "legado": ["session.copilot.sugestao_resposta"]}
            },
            "pool": {
                "agent_groups": {"tipo": "texto", "legado": ["session.pool.agent_groups"]},
                "channels": {"tipo": "texto", "legado": ["session.pool.channels"]},
                "id": {"tipo": "texto", "legado": ["session.pool.id"]},
                "llm_account_ids": {"tipo": "texto", "legado": ["session.pool.llm_account_ids"]},
                "max_reply_time_ms": {"tipo": "texto", "legado": ["session.pool.max_reply_time_ms"]},
                "mentionable_pools": {"tipo": "texto", "legado": ["session.pool.mentionable_pools"]}
            },
            "process": {
                "outcome": {"tipo": "texto", "legado": ["session.processo.outcome", "session.process_outcome"]}
            },
            "queue": {
                "eta_ms": {"tipo": "texto", "legado": ["session.queue.eta_ms"]},
                "position": {"tipo": "texto", "legado": ["session.queue.position"]}
            },
            "sentiment": {
                "category": {"tipo": "texto", "label": "Classificada na LEITURA — sem produtor próprio", "legado": ["session.sentimento.categoria"]},
                "current": {"tipo": "texto", "legado": ["session.sentimento.current"]}
            },
            "survey": {
                "agent_key": {"tipo": "texto", "legado": ["session.survey.agent_key", "session.survey_agent_key", "session.surveyed_agent_key"]},
                "grain": {"tipo": "texto", "legado": ["session.survey.grain", "session.survey_grain"]},
                "pool_id": {"tipo": "texto", "legado": ["session.survey.pool_id", "session.survey_pool_id"]},
                "segment_id": {"tipo": "texto", "legado": ["session.survey.segment_id", "session.survey_segment_id", "session.surveyed_segment_id"]},
                "target_id": {"tipo": "texto", "legado": ["session.survey.target_id", "session.survey_target_id"]}
            },
            "workflow": {
                "current_round": {"tipo": "texto", "legado": ["session.workflow.current_round", "session.current_round"]},
                "delegate_resume_token": {"tipo": "credential", "legado": ["session.workflow.delegate_resume_token", "session.delegate_resume_token"]},
                "dialog_form_id": {"tipo": "texto", "legado": ["session.workflow.dialog_form_id", "session.dialog_form_id"]},
                "origin_session_id": {"tipo": "texto", "legado": ["session.workflow.origin_session_id", "session.origin_session_id"]},
                "resume_token": {"tipo": "credential", "legado": ["session.workflow.resume_token", "session.workflow_resume_token"]},
                "review_decision": {"tipo": "texto", "legado": ["session.workflow.review_decision", "session.review_decision"]},
                "round_echoed": {"tipo": "texto", "legado": ["session.workflow.round_echoed", "session.round_echoed"]}
            },
        },
        "session": {
            "cliente": {
                "nome": {"tipo": "texto", "legado": ["caller.nome"]},
                "cpf": {"tipo": "cpf", "legado": ["caller.cpf", "session.cpf"]},
                "telefone": {"tipo": "phone", "legado": ["caller.telefone"]},
                "email": {"tipo": "email_addr", "legado": ["caller.email"]},
                "customer_id": {"tipo": "texto", "legado": ["caller.customer_id", "session.customer_id"], "label": "ID interno — não-PII, necessário p/ histórico/360"},
                "account_id": {"tipo": "texto", "legado": ["caller.account_id"]},
                "motivo_contato": {"tipo": "texto", "legado": ["caller.motivo_contato", "session.motivo_contato"]},
                "intencao_primaria": {"tipo": "texto", "legado": ["caller.intencao_primaria"]},
                "sentimento_atual": {"tipo": "texto", "legado": ["caller.sentimento_atual"]}
            },
            "copilot": {
                "mode": {"tipo": "texto", "label": "Interruptor — `mention.set_context`"}
            },
            "wrapup": {
                "resumo": {"tipo": "texto"},
                "classificacao": {"tipo": "texto"},
                "escalation_reason": {"tipo": "texto"},
                "proximos_passos": {"tipo": "texto"}
            },
            "workflow": {
                "max_rounds": {"tipo": "texto", "legado": ["session.max_rounds"]},
                "decisions": {"tipo": "texto", "legado": ["session.decisions"]},
                "briefing_session_id": {"tipo": "texto", "legado": ["session.briefing_session_id"]},
                "title": {"tipo": "texto", "legado": ["session.title"]},
                "summary": {"tipo": "texto", "legado": ["session.summary", "approval.summary"]},
                "status": {"tipo": "texto", "legado": ["session.status"]},
                "approval_threshold": {"tipo": "texto", "legado": ["session.approval_threshold"]}
            },
            "contato": {
                "contact_channel": {"tipo": "texto", "legado": ["session.contact_channel"]},
                "contact_identifier": {"tipo": "texto", "legado": ["session.contact_identifier"]},
                "contact_outcome": {"tipo": "texto", "legado": ["session.contact_outcome"]},
                "customer_present": {"tipo": "texto", "legado": ["session.customer_present"]},
                "confirmation_channel": {"tipo": "texto", "legado": ["session.confirmation_channel"]},
                "resume_origin": {"tipo": "texto", "legado": ["session.resume_origin"]},
                "pergunta_coleta": {"tipo": "texto", "legado": ["session.pergunta_coleta"]}
            },
            "survey": {
                "form_id": {"tipo": "texto", "legado": ["session.survey_form_id"]},
                "origin": {"tipo": "texto", "legado": ["session.survey_origin"]},
                "origin_pool": {"tipo": "texto", "legado": ["session.survey_origin_pool"]},
                "customer_key": {"tipo": "texto", "legado": ["session.survey_customer_key"]}
            },
            "deploy": {
                "notes": {"tipo": "texto", "legado": ["session.deploy_notes"]},
                "deployed_by": {"tipo": "texto", "legado": ["session.deployed_by"]},
                "skill_id": {"tipo": "texto", "legado": ["session.skill_id"]}
            },
            "campanha": {
                "campaign_id": {"tipo": "texto", "legado": ["session.campaign_id"]},
                "delivery_id": {"tipo": "texto", "legado": ["session.delivery_id"]}
            },
            "hook": {
                "wrapup_pool": {"tipo": "texto", "legado": ["hook.wrapup_pool"]},
                "dialog_form_id": {"tipo": "texto", "legado": ["hook.dialog_form_id"]},
                "acw_timeout_hours": {"tipo": "texto", "legado": ["hook.acw_timeout_hours"]},
                # ALW-01 pre-requisito (2026-09-02): as DUAS unicas tags que a auditoria viva
                # acusava como `unknown` (30 leituras). Escritas pelo bridge em
                # `_fire_detached_hook` na grafia PLANA; os tres irmaos acima ja declaravam o
                # alias, estes dois nao existiam. `texto` porque sao identificador e enum.
                "type": {"tipo": "texto", "legado": ["hook.type"]},
                "origin_pool": {"tipo": "texto", "legado": ["hook.origin_pool"]}
            }
        },
        "journey": {
        }
    }
}
