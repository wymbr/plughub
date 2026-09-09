"""
test_ret12_park_token_discovery.py — RET-12: o leitor de parque via UM sufixo.

O gate `infra/test/probe_park_token_key_contract.sh` mede os NOMES das chaves — que
todo sufixo escrito por um step do engine esteja na lista que o bridge reconhece.
Esta suíte mede outra proposição: que o leitor, olhando um `pipeline_state.results`
real, extraia o par `(token, step_id)` CERTO. Um leitor que reconhecesse os dois
sufixos e devolvesse o step errado passaria no gate e falharia aqui.

Defeito de origem: `collect.ts` grava `{step}:__collect_token__` e o bridge só
casava `:__resume_token__`, então a sessão parqueada por `collect` publicava
`session_suspended` SEM token — e a analytics só grava `session_transitions`
`if payload.get("resume_token")`. Medido: 49 sessões suspensas sem registro
durável, 41 delas do `limite_entrega`.
"""
from __future__ import annotations

import pytest

import plughub_orchestrator_bridge.main as bridge_mod

descobrir = bridge_mod.descobrir_parque


class TestOsDoisSufixos:
    """O par que o leitor extrai, por step que parqueia."""

    def test_suspend_grava_resume_token(self):
        # suspend.ts:53
        token, step, prazo = descobrir({
            "aguarda_aprovacao:__resume_token__": "rt_abc",
            "aguarda_aprovacao:__expires_at__":   "2026-09-16T12:00:00Z",
        })
        assert (token, step) == ("rt_abc", "aguarda_aprovacao")
        assert prazo == "2026-09-16T12:00:00Z"

    def test_delegate_grava_resume_token(self):
        # delegate.ts:41 — mesmo sufixo do suspend
        token, step, _ = descobrir({"pede_ao_especialista:__resume_token__": "rt_del"})
        assert (token, step) == ("rt_del", "pede_ao_especialista")

    def test_collect_grava_collect_token(self):
        """⚠️ O caso da RET-12: antes do conserto isto devolvia ('', '')."""
        # collect.ts:38
        token, step, prazo = descobrir({
            "pergunta_ao_cliente:__collect_token__": "ct_xyz",
            "pergunta_ao_cliente:__expires_at__":    "2026-09-12T00:00:00Z",
        })
        assert token == "ct_xyz", (
            "o collect_token DOUBLES AS o resume_token (adapters/webhook.py:2226) — "
            "sem ele o session_suspended sai sem endereço e a sessão fica órfã"
        )
        assert step == "pergunta_ao_cliente", (
            "o step_id é o que sobra ao TIRAR o sufixo — tirar o sufixo errado deixaria "
            "um resíduo (`pergunta_ao_cliente:__collect`) e o gate de nomes não veria"
        )
        assert prazo == "2026-09-12T00:00:00Z"

    @pytest.mark.parametrize("sufixo", list(bridge_mod.SUFIXOS_DE_TOKEN_DE_PARQUE))
    def test_todo_sufixo_declarado_e_de_fato_lido(self, sufixo: str):
        """Controle sobre a própria lista: declarar não é reconhecer.

        Se alguém acrescentar um sufixo à tupla e o laço não o consumir (ou o
        recorte do step_id errar), este caso reprova — a tupla sozinha é
        declaração, e declaração sem mecanismo é o que este repositório persegue.
        """
        token, step, _ = descobrir({f"algum_step{sufixo}": "tk"})
        assert (token, step) == ("tk", "algum_step")


class TestOQueNaoEToken:
    """A ausência de token é um FATO, e o leitor não pode fabricá-la nem escondê-la."""

    def test_sem_parque_devolve_vazio(self):
        assert descobrir({"passo_a:__output__": {"x": 1}}) == ("", "", "")

    def test_results_ausente_nao_explode(self):
        # O call site passa `.get("results")`, que pode ser None num
        # pipeline_state truncado — e explodir aqui derrubaria a publicação
        # inteira do session_suspended, não só o token.
        assert descobrir(None) == ("", "", "")
        assert descobrir({}) == ("", "", "")

    def test_expires_sozinho_nao_vira_token(self):
        """`__expires_at__` NÃO casa o padrão de token — é prazo, não endereço."""
        token, step, prazo = descobrir({"passo:__expires_at__": "2026-09-16T12:00:00Z"})
        assert (token, step) == ("", "")
        assert prazo == "2026-09-16T12:00:00Z"

    def test_token_vazio_continua_vazio(self):
        """Valor falsy vira "", nunca "None" — o consumidor testa `if token`."""
        token, step, _ = descobrir({"passo:__resume_token__": None})
        assert token == ""
        assert step == "passo", "o step é fato mesmo quando o token não veio"

    def test_chave_nao_string_e_ignorada(self):
        # `results` vem de JSON, mas o bridge também o recebe de fixtures e de
        # caminhos internos; uma chave int fazia `endswith` explodir.
        assert descobrir({7: "x", "p:__resume_token__": "tk"})[:2] == ("tk", "p")
