FROM python:3.11-slim

WORKDIR /app

# ⚠️ O CONTEXTO DE BUILD DESTE SERVIÇO É A RAIZ DO MONOREPO (mudou em 2026-08-28).
# Era `packages/auth-api`, e essa escolha custava duas coisas:
#
#   1. o verificador canônico (`packages/py-authz`) ficava FORA do contexto, logo o
#      auth-api não podia consumi-lo — era o pré-requisito do passo 5 da consolidação;
#   2. `infra/modules.yaml` também ficava fora, e o compose de demo contornava com um
#      volume mount. O `docker-compose.full.yml` NÃO tinha esse mount nem a env —
#      então lá o `_seed_modules_from_yaml` caía no aviso "pulando registro de módulos"
#      e a stack subia com `auth.module_registry` VAZIO. Como `build_module_config` lê
#      justamente essa tabela, todo usuário criado nascia SEM grant nenhum — e, sob
#      grant-first, nascer sem grant é nascer cego. Contorno em UM dos dois composes é
#      exatamente o "ambiente que só sobe porque já subiu antes".
#
# Verificador canônico de JWT+ABAC. Instalado ANTES do serviço para que o
# `pip install -e .` dele encontre a dependência já satisfeita em vez de procurá-la no
# PyPI, onde ela não existe.
COPY packages/py-authz ./packages/py-authz
RUN pip install --no-cache-dir -e ./packages/py-authz

# Declaração dos módulos ABAC. Vai para `/infra/modules.yaml` porque é exatamente o
# caminho de fallback que `main.py` já resolve quando o WORKDIR é `/app` (a heurística
# `parents[4]` levanta IndexError ali). O volume mount do compose de demo continua
# funcionando e apenas sobrepõe este arquivo — mas agora a IMAGEM basta sozinha.
COPY infra/modules.yaml /infra/modules.yaml

COPY packages/auth-api/pyproject.toml .
COPY packages/auth-api/src/ ./src/

RUN pip install --no-cache-dir -e .

EXPOSE 3200

CMD ["python", "-m", "plughub_auth_api.main"]
