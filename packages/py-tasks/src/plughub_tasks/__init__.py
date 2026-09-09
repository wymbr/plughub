"""
plughub_tasks — dono e alarme para as tasks EFÊMERAS de trabalho.

⚠️ Isto NÃO é o supervisor das tasks de BOOT. São dois fenômenos, com ciclos de
vida OPOSTOS, e por isso dois helpers:

    task de BOOT       vive enquanto o processo vive; nasce no `lifespan`; quem a
                       guarda é a variável do lifespan; **terminar é incidente**.
    task EFÊMERA       uma por mensagem/evento, nasce dentro de um handler,
                       termina em segundos; **terminar é o normal**.

Copiar o alarme de boot para cá seria o defeito: um WARNING por mensagem afogaria
o log e ensinaria a ignorá-lo — a mesma razão pela qual um runner com 476 falsos
vermelhos é pior que runner nenhum.

O que este módulo fecha são DOIS danos independentes de `create_task(...)` solto:

  1. **A exceção some.** A Task que ninguém aguarda guarda a exceção dentro de si;
     ela não aparece em log nenhum. Medido em 2026-09-09: `_process_inbound` do
     WhatsApp (mensagem de cliente) e `_process_message` do routing-engine
     (mensagem Kafka do roteador) estavam nessa condição.
  2. **A task pode ser COLETADA no meio da execução.** Sem referência forte, o
     loop é o único dono, e o CPython documenta que ela pode desaparecer antes de
     terminar. Este dano vale para TODAS, inclusive as que já embrulham o corpo
     inteiro num `try` — proteger o corpo não dá dono a ninguém.

Gate: `infra/test/probe_background_task_supervision.sh` § C.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

__all__ = ["disparar", "vivas"]

logger = logging.getLogger("plughub.tasks")

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

    ⚠️ Não engole nem reinicia: o `try` que já existir dentro da corrotina continua
    mandando, e isto é o *net* de quem escapou. Reiniciar em laço esconderia falha
    permanente atrás de ruído.
    """
    t = asyncio.create_task(coro, name=nome)
    _VIVAS.add(t)
    t.add_done_callback(_encerrou)
    return t


def vivas() -> int:
    """Quantas tarefas efêmeras estão em voo. Para teste e diagnóstico."""
    return len(_VIVAS)
