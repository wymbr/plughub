"""
registry_invalidation_consumer.py
Consome `registry.changed` e invalida o cache in-process do `endpoint_resolver`.

── O PROBLEMA QUE ISTO FECHA ─────────────────────────────────────────────────
O `endpoint_resolver` cacheia a resolução de endereço por `endpoint_cache_ttl_s`
(30 s). Enquanto o cache só guardava `pool_id`, a defasagem era desconfortável mas
inofensiva. Com o arco de autenticação ela passou a ter consequência de segurança:
**revogar ou rotacionar um token levava até 30 s para valer**, porque o gateway
seguia verificando contra o hash antigo. Um token vazado continuava aceito por meia
janela depois de o operador tê-lo revogado.

O `invalidate()` do resolver **existia desde sempre** — com um docstring dizendo
"until that consumer is wired". Este é o consumidor. Vale para `origin` e `pool_id`
também, não só para token: qualquer mudança na linha passa a valer na hora.

── POR QUE UM GROUP_ID ÚNICO POR PROCESSO ────────────────────────────────────
Esta é a decisão que não pode ser copiada dos outros consumidores do repo, e a razão
é o tipo de estado que cada um guarda.

O `kafka_listener` do routing-engine consome `registry.changed` com um group_id
COMPARTILHADO (`{grupo}-listener`) — e está certo, porque o cache que ele atualiza
vive no **Redis**: um consumidor escreve, todas as réplicas leem. É trabalho, e
trabalho se divide.

Aqui o cache é um **dicionário de módulo, dentro do processo**. Se duas réplicas do
channel-gateway compartilhassem o group, o Kafka entregaria cada evento a UMA delas —
e a outra seguiria servindo o hash revogado até o TTL. Invalidação de cache local é
**broadcast**, não fila: toda réplica precisa de TODOS os eventos, logo cada processo
precisa do próprio grupo.

O custo é proliferação de consumer groups no broker (offsets de grupos efêmeros, que
o Kafka expira sozinho). É o preço padrão do fan-out por Kafka, e é barato perto de
uma réplica servindo credencial revogada.

── DEGRADAÇÃO É BARULHENTA ───────────────────────────────────────────────────
Se o consumidor não subir, o gateway continua funcionando — o cache volta a expirar
só por TTL, que é o comportamento anterior. Mas isso é uma REGRESSÃO silenciosa de
segurança, então a falha loga em ERROR dizendo exatamente o que se perdeu.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from aiokafka import AIOKafkaConsumer

from . import endpoint_resolver
from .config import Settings

logger = logging.getLogger("plughub.channel-gateway.registry-invalidation")

# Entidades cujo evento afeta o cache de endereço. `channel_endpoint` é a linha em
# si; qualquer outra (pool, skill, slot) não muda nada do que está cacheado, e
# invalidar a cada uma delas esvaziaria o cache a todo boot do RegistrySyncer, que
# publica dezenas de eventos em sequência.
_RELEVANT_ENTITIES = {"channel_endpoint"}


class RegistryInvalidationConsumer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._seen = 0

    async def run(self) -> None:
        """
        ⚠️ TODO O CORPO É PROTEGIDO, e isso é conserto de um defeito REAL, não zelo.
        Esta corrotina roda em `asyncio.create_task`, e **exceção em task que ninguém
        aguarda morre em silêncio**: a task some, o serviço segue de pé, e o sintoma
        aparece longe daqui — no caso, "revogação não vale" 30 s depois, num gate.
        Sem este `except`, a única evidência de que o consumidor morreu é a AUSÊNCIA
        do efeito dele, que é exatamente o tipo de sinal que esta base já provou não
        saber ler. (Os outros consumidores deste arquivo têm a mesma exposição — ver
        `_supervise` em `main.py`.)
        """
        # Grupo ÚNICO por processo — ver o comentário do topo. `uuid4` e não hostname:
        # dois processos no mesmo host (ou um container reiniciado com o mesmo nome)
        # voltariam a compartilhar grupo sem que nada ficasse vermelho.
        group_id = f"channel-gateway-registry-invalidation-{uuid.uuid4().hex[:12]}"
        topic    = self._settings.kafka_topic_registry_changed

        try:
            consumer = AIOKafkaConsumer(
                topic,
                bootstrap_servers = self._settings.kafka_brokers,
                group_id          = group_id,
                # `latest`: o que interessa é o que muda DAQUI PRA FRENTE. Reprocessar
                # o histórico só esvaziaria o cache repetidamente no boot.
                auto_offset_reset = "latest",
                fetch_max_wait_ms = 250,
                fetch_min_bytes   = 1,
            )
            await consumer.start()
        except Exception as exc:
            logger.error(
                "NÃO subiu o consumidor de invalidação (topic=%s brokers=%s): %s — o "
                "cache de endereço volta a expirar SÓ por TTL (%ss). Consequência: "
                "revogar/rotacionar token leva até esse tempo para valer, e o gateway "
                "segue aceitando a credencial antiga nesse intervalo.",
                topic, self._settings.kafka_brokers, exc,
                self._settings.endpoint_cache_ttl_s, exc_info=True,
            )
            return

        logger.info(
            "registry invalidation consumer started — topic=%s group=%s", topic, group_id,
        )

        # ── JANELA CEGA DO JOIN ───────────────────────────────────────────────
        # Entre o `start()` e o grupo ser efetivamente atribuído passam ~3 s
        # (medido: subscribe 23:40:10 → "Joined group (generation 1)" 23:40:13).
        # Com `auto_offset_reset=latest` num grupo NOVO, o que for publicado nessa
        # janela **não é entregue nunca** — não fica pendente, some.
        #
        # Consequência real: um endereço resolvido e cacheado logo após o boot, cuja
        # linha mude dentro desses segundos, fica servindo dado velho pelo TTL inteiro
        # (30 s) — e, com token, isso é credencial revogada ainda aceita.
        #
        # Esvaziar o cache DEPOIS do join fecha a janela por construção: tudo que
        # entrou nele durante o período cego é descartado, e o que for lido a seguir
        # vem do registry. Custa uma consulta por endereço em uso, uma vez por boot.
        #
        # Isto é conserto que NÃO depende do diagnóstico estar certo: mesmo que a
        # falha observada em 2026-08-07 tenha tido outra causa, a janela existe e é
        # fechável — e fechar o que se entende é melhor que apostar no que se supõe.
        endpoint_resolver.invalidate_all()

        try:
            async for msg in consumer:
                try:
                    self._handle(json.loads(msg.value.decode()))
                except Exception as exc:
                    # Uma mensagem malformada não pode derrubar o consumidor: sem ele,
                    # a revogação volta a depender do TTL, em silêncio.
                    logger.warning("registry.changed inválido, ignorado: %s", exc)
        except asyncio.CancelledError:
            raise                      # shutdown normal — não é falha
        except Exception as exc:
            logger.error(
                "consumidor de invalidação MORREU (topic=%s): %s — revogação de token "
                "volta a depender do TTL até o serviço reiniciar.",
                topic, exc, exc_info=True,
            )
        finally:
            await consumer.stop()

    def _handle(self, event: dict) -> None:
        entity = str(event.get("entity_type") or "")

        # Testemunha do PIPE, uma vez só. "Consumidor de pé" e "eventos chegando" são
        # fatos diferentes, e a ausência de invalidação não distingue os dois: pode
        # ser consumidor morto, tópico errado, grupo mal posicionado ou filtro que não
        # casa. Uma linha no primeiro evento separa "não chega nada" de "chega e é
        # descartado" — e se limita sozinha, então não vira ruído.
        self._seen += 1
        if self._seen == 1:
            logger.info(
                "primeiro registry.changed recebido (entity_type=%r) — o pipe funciona; "
                "daqui em diante só linhas de invalidação efetiva.", entity,
            )

        if entity not in _RELEVANT_ENTITIES:
            return

        tenant_id = str(event.get("tenant_id") or "")
        operation = str(event.get("operation") or "?")

        if not tenant_id:
            # Sem tenant não dá para recortar — derruba tudo. Ver `invalidate_all`.
            logger.warning(
                "registry.changed de %s SEM tenant_id (op=%s) — invalidando o cache "
                "inteiro por segurança", entity, operation,
            )
            endpoint_resolver.invalidate_all()
            return

        # Invalidação por TENANT, não por endereço: o evento carrega `entity_id` (o
        # UUID da linha), e o cache é chaveado por (tenant, canal, IDENTIFICADOR) —
        # não há como traduzir um para o outro sem uma consulta, que é exatamente o
        # que a invalidação existe para evitar. Derrubar as entradas do tenant custa
        # uma consulta por endereço ainda em uso; precisão exigiria o produtor mandar
        # canal+identificador no evento, e isso é mudança de contrato para economizar
        # microssegundos.
        endpoint_resolver.invalidate(tenant_id=tenant_id)
        logger.info(
            "cache de endereço invalidado — tenant=%s entidade=%s op=%s",
            tenant_id, entity, operation,
        )


### `start_registry_invalidation()` REMOVIDA (2026-08-07) — era um guard que não
### guardava nada. Ela envolvia `asyncio.create_task` num try/except, mas
### `create_task` **não levanta** a exceção da corrotina: ele agenda e volta. O
### `except` só pegaria falha ao AGENDAR (que não acontece), e a falha real — dentro
### do `run()` — passava por ele e morria calada. Pior: a existência do helper dava
### aparência de proteção, e eu ainda o deixei sem uso (o `main.py` chamava
### `create_task(...run())` direto), então havia dois motivos independentes para ele
### não funcionar. A proteção real está DENTRO do `run()`, onde a exceção nasce.
###
### Lição do caso: um guard só protege o que ele consegue observar. Try/except em
### volta de um agendamento observa o agendamento, não o trabalho.
