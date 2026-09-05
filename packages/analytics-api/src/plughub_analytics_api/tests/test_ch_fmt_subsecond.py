"""O limite de tempo nao pode andar para TRAS.

`agent_business_events.emitted_at` e `DateTime64(3)`. O `_ch_fmt` truncava para o
segundo inteiro, o que move um limite SUPERIOR para o passado: um recorte pedido em
`19:34:45.858` virava `19:34:45`, e os eventos daquele milissegundo saiam do
resultado. Medido no ClickHouse antes do conserto: 39 eventos com o literal em ms,
**36** truncado.

O dano nao era um numero levemente errado — a lente de taxonomia recorta pelos
limites EXATOS da propria epoca, entao a epoca carimbada desenhava "sem amostra no
periodo" logo abaixo do proprio cabecalho dizendo "2 marcacoes · 1 contato". Duas
afirmacoes contrarias na mesma tela, e a falsa era a que tinha cara de ausencia de
dado — o "valor plausivel" da secao Postura de Engenharia.

⚠️ O teste que importa aqui NAO e o do caso novo: e o **controle negativo**, que
prende a entrada `YYYY-MM-DD` da barra de filtros a saida que ela sempre teve. Sem
ele, preservar sub-segundo poderia mudar em silencio os 67 outros call sites.
"""

from plughub_analytics_api.reports_query import _ch_fmt


def test_sub_segundo_do_chamador_sobrevive():
    # O limite superior TEM de conter o proprio evento que o definiu.
    assert _ch_fmt("2026-09-05T19:34:45.858000+00:00", upper=True) == "2026-09-05 19:34:45.858"
    assert _ch_fmt("2026-09-05T19:34:45.857000+00:00") == "2026-09-05 19:34:45.857"


def test_entrada_sem_fracao_sai_identica():
    """Controle negativo: a barra de filtros manda `YYYY-MM-DD`, e nada muda para ela."""
    assert _ch_fmt("2026-09-05") == "2026-09-05 00:00:00"
    assert _ch_fmt("2026-09-05", upper=True) == "2026-09-05 23:59:59"
    assert _ch_fmt("2026-09-05T19:34:45") == "2026-09-05 19:34:45"
    assert _ch_fmt("2026-09-05T19:34:45+00:00", upper=True) == "2026-09-05 19:34:45"


def test_fracao_zero_nao_ganha_sufixo():
    """`.000` e ausencia de fracao: emitir `.000` mudaria a forma da string para
    todo chamador que passasse um ISO completo, sem ganhar precisao nenhuma."""
    assert _ch_fmt("2026-09-05T19:34:45.000000+00:00") == "2026-09-05 19:34:45"

