"""
tarefas.py — dono e alarme para as tasks EFÊMERAS de trabalho.

⚠️ Isto NÃO é o supervisor das tasks de boot (RET-13). São dois fenômenos:

    task de BOOT       vive enquanto o processo vive; nasce no `lifespan`; quem a
                       guarda é a variável do lifespan; morrer é incidente.
    task EFÊMERA       uma por mensagem/evento, nasce dentro de um handler,
                       termina em segundos; terminar é o NORMAL.

O que elas têm em comum é o modo de falha, e são DOIS:

  1. **A exceção some.** `asyncio.create_task(...)` como statement devolve uma Task
     que ninguém aguarda; se a corrotina levanta, a exceção fica presa no objeto e
     não aparece em log nenhum. Medido em 2026-09-09: seis corrotinas do
     channel-gateway com instruções de topo FORA de qualquer `try`, entre elas
     `WhatsAppAdapter._process_inbound` — ou seja, **mensagem de cliente que some
     sem uma linha de log**.

  2. **A task pode ser COLETADA no meio da execução.** Sem referência forte, o loop
     é o único dono, e o CPython documenta que a task pode desaparecer antes de
     terminar. Este dano vale para TODAS as efêmeras, inclusive as que já embrulham
     o corpo inteiro num `try` — proteger o corpo não dá dono a ninguém.

`disparar()` fecha os dois com uma linha no call site: guarda a referência num
conjunto de módulo enquanto a task vive, e loga se ela morrer.

⚠️ **O conjunto vive no PRODUTO, nunca no teste** — é a lição de 2026-08-30: um
helper que varra `asyncio.all_tasks` varre também as tasks de quem chamou.

⚠️ **Não engole nem reinicia.** O `try` que já existir dentro da corrotina continua
mandando; este é o *net* de quem escapou. Reiniciar em laço esconderia falha
permanente atrás de ruído — a mesma postura do supervisor de boot.

Gate: `infra/test/probe_background_task_supervision.sh`, seção C.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger("plughub.channel-gateway.tarefas")

# Referência forte enquanto a task vive. É um `set`, e o callback faz `discard`:
# sem isso o conjunto viraria um vazamento — uma entrada por mensagem, para sempre.
_VIVAS: set[asyncio.Task] = set()


def _encerrou(t: asyncio.Task) -> None:
    _VIVAS.discard(t)
    if t.cancelled():
        return                                  # shutdown ou cancelamento pedido
    exc = t.exception()
    if exc is not None:
        logger.error(
            "tarefa '%s' MORREU: %s — o trabalho que ela carregava NAO aconteceu "
            "(mensagem, gravacao ou entrega perdida). Sem esta linha, nada apareceria.",
            t.get_name(), exc, exc_info=exc,
        )


def disparar(coro: Coroutine[Any, Any, Any], *, nome: str) -> asyncio.Task:
    """Cria uma task efêmera COM dono e COM alarme.

    Use no lugar de `asyncio.create_task(...)` sempre que ninguém for aguardar o
    resultado. Quem aguarda (ou guarda a task numa lista que sobrevive) não precisa
    disto — já tem dono, e o `await` já traz a exceção.
    """
    t = asyncio.create_task(coro, name=nome)
    _VIVAS.add(t)
    t.add_done_callback(_encerrou)
    return t


def vivas() -> int:
    """Quantas tarefas efêmeras estão em voo. Existe para o teste e para diagnóstico."""
    return len(_VIVAS)
