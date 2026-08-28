FROM python:3.11-slim

WORKDIR /app

# Verificador canonico de JWT+ABAC (packages/py-authz). Instalado ANTES do servico
# para que o `pip install -e .` dele encontre a dependencia ja satisfeita em vez de
# procura-la no PyPI, onde ela nao existe.
COPY packages/py-authz ./packages/py-authz
RUN pip install --no-cache-dir -e ./packages/py-authz

# Copy channel-gateway package
COPY packages/channel-gateway ./packages/channel-gateway

WORKDIR /app/packages/channel-gateway

RUN pip install --no-cache-dir -e .

CMD ["uvicorn", "plughub_channel_gateway.main:app", "--host", "0.0.0.0", "--port", "8010"]
