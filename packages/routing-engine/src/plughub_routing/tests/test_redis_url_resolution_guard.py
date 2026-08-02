"""
test_redis_url_resolution_guard.py — guarda contra a suíte que PULA em silêncio.

**O defeito que este arquivo impede de voltar.** Os testes de integração deste pacote
resolvem o endereço do Redis por variável de ambiente. Quem lê só `REDIS_URL` cai no
default `redis://localhost:6379`, que **não resolve dentro do container** — o serviço
define `PLUGHUB_REDIS_URL`. O resultado não é vermelho: é `pytest.skip`, que sai VERDE.

Já aconteceu duas vezes, e a segunda foi pior que a primeira:

  · 2026-07-30 — 9 testes do claim pull nunca haviam rodado no container. Corrigido
    naquele arquivo, e só naquele.
  · 2026-08-02 — a mesma causa em `test_instance_semaphore.py` (24 testes) e
    `test_human_instance_identity.py` (11). Medição: `171 passed, 35 skipped`. Os 24
    cobrem o `claim_instance` — concorrência, teto e a tag de pool da fatia 1. É o
    código de maior consequência da plataforma, e a rede dele nunca chegou a rodar
    onde o serviço roda.

A segunda foi pior porque a lição da primeira existia e não alcançou os vizinhos: a
correção foi feita arquivo a arquivo. **Correção pontual não fecha classe de defeito
cujo sintoma é a ausência de sintoma** — sem uma guarda, a próxima cópia do padrão
antigo volta a passar despercebida, e ninguém olha a contagem de `skipped`.

Não substitui rodar a suíte com Redis alcançável; impede que a suíte MINTA quando ele
está alcançável e mesmo assim ninguém o consulta.
"""
from __future__ import annotations

import pathlib
import re

TESTS_DIR = pathlib.Path(__file__).parent

# Casa a leitura da variável em qualquer forma (`get("REDIS_URL")`,
# `get('REDIS_URL', default)`, `environ["REDIS_URL"]`).
_READS_REDIS_URL = re.compile(r"""(?:environ\.get\(|environ\[)\s*["']REDIS_URL["']""")


def test_every_test_module_reading_redis_url_also_reads_the_service_variable():
    """Quem lê `REDIS_URL` tem de ler `PLUGHUB_REDIS_URL` também.

    A ordem entre as duas não importa (uma é override de dev, a outra é a do compose);
    o que importa é que a do SERVIÇO seja consultada, senão o arquivo pula inteiro
    dentro do container.
    """
    offenders: list[str] = []
    checked = 0
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if path.name == pathlib.Path(__file__).name:
            continue
        src = path.read_text(encoding="utf-8")
        if not _READS_REDIS_URL.search(src):
            continue
        checked += 1
        if "PLUGHUB_REDIS_URL" not in src:
            offenders.append(path.name)

    # DENOMINADOR PRIMEIRO. Sem nenhum arquivo lendo a variável, "zero infratores" não
    # é aprovação — é a asserção nunca tendo alcançado a condição que deveria julgar.
    # Um refactor que mova a resolução para um `conftest.py` compartilhado tornaria
    # este teste inútil sem torná-lo vermelho; então ele se declara.
    assert checked > 0, (
        "nenhum módulo de teste lê `REDIS_URL` — ou a resolução migrou para um lugar "
        "compartilhado (bom: adapte esta guarda para apontar para lá), ou o padrão de "
        "busca deixou de casar. Nos dois casos esta guarda não está guardando nada."
    )
    assert not offenders, (
        "módulo(s) lendo só `REDIS_URL`: " + ", ".join(offenders) + ".\n"
        "Dentro do container isso vira `pytest.skip` — VERDE — e os testes de "
        "integração daquele arquivo nunca rodam onde o serviço roda. Usar:\n"
        '    REDIS_URL = (os.environ.get("REDIS_URL")\n'
        '                 or os.environ.get("PLUGHUB_REDIS_URL")\n'
        '                 or "redis://localhost:6379")'
    )
