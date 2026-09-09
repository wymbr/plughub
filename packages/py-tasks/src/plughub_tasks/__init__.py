"""
plughub_tasks — a morte de uma task de asyncio tem de APARECER.

Duas funções, para as DUAS populações. Elas moram juntas porque o eixo é o mesmo
(task que ninguém aguarda guarda a exceção dentro de si e some), e continuam
SEPARADAS porque os ciclos de vida são opostos:

    task de BOOT       vive enquanto o processo vive; nasce no `lifespan`; quem a
                       guarda é a variável do lifespan; **terminar é incidente**.
    task EFÊMERA       uma por mensagem/evento, nasce dentro de um handler,
                       termina em segundos; **terminar é o normal**.

    `supervisionar(nome, task)`   para as de BOOT: avisa quando ela morre E quando
                                  ela TERMINA sozinha, porque ali retornar é
                                  incidente.
    `disparar(coro, nome=...)`    para as EFÊMERAS: dá dono e avisa quando ela
                                  morre — e CALA no término normal.

⚠️ Trocar uma pela outra é o defeito, nos dois sentidos: um WARNING por mensagem
afogaria o log (e ensinaria a ignorá-lo, a mesma razão pela qual um runner com 476
falsos vermelhos é pior que runner nenhum); e um consumidor de boot que retorna em
silêncio é justamente o caso que `t.exception()` não vê.

O que `disparar` fecha são DOIS danos independentes de `create_task(...)` solto:

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

__all__ = ["disparar", "supervisionar", "vivas"]

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


def supervisionar(nome: str, task: asyncio.Task) -> asyncio.Task:
    """Faz a MORTE de uma task de BOOT aparecer. Envolve a task e a devolve.

    ⚠️ Conserto de uma cegueira medida em 2026-08-07: estas tasks rodam sob
    `create_task` e ninguém as aguarda enquanto o serviço vive. Se a corrotina
    levanta, a exceção fica presa no objeto Task e some — o serviço segue de pé,
    `/health` verde, com um consumidor a menos, e o sintoma aparece longe (no caso
    que criou isto, *"revogar token não vale"*, três camadas abaixo).

    ⚠️ São DOIS ramos, e o segundo não tem exceção nenhuma para inspecionar: a task
    pode TERMINAR sozinha. Um alarme que só olhasse `t.exception()` ficaria mudo
    exatamente aí — e foi assim que o analytics-api gritava em todo shutdown até a
    RET-13 descobrir que `run_consumer` **retorna** no SIGTERM.

    ⚠️ Não reinicia de propósito — reiniciar em laço esconde falha permanente atrás
    de ruído. Isto é o alarme; a política de recuperação é decisão à parte e precisa
    do alarme para ser tomada.

    ⚠️ A mensagem não nomeia o SERVIÇO (a cópia por serviço nomeava): quem o
    identifica é o log do container, e um parâmetro a mais só para repetir o que o
    prefixo já diz seria contrato novo sem fato novo.
    """
    def _fim(t: asyncio.Task) -> None:
        if t.cancelled():
            return                              # shutdown normal
        exc = t.exception()
        if exc is not None:
            logger.error(
                "task de background '%s' MORREU: %s — o servico segue de pe SEM ela. "
                "Reinicie o servico depois de tratar a causa.",
                nome, exc, exc_info=exc,
            )
        else:
            logger.warning(
                "task de background '%s' TERMINOU sozinha (sem excecao) — "
                "consumidores nao deveriam retornar enquanto o servico vive.", nome,
            )
    task.add_done_callback(_fim)
    return task


def vivas() -> int:
    """Quantas tarefas efêmeras estão em voo. Para teste e diagnóstico."""
    return len(_VIVAS)
