FROM python:3.11-slim

WORKDIR /app

# Copy rules-engine package
COPY packages/rules-engine ./packages/rules-engine

WORKDIR /app/packages/rules-engine

RUN pip install --no-cache-dir -e .

# Write the startup script that runs both the API server and the pub/sub listener
RUN printf '#!/bin/sh\nset -e\nuvicorn plughub_rules.api:app --host 0.0.0.0 --port 3201 &\npython -m plughub_rules.main &\nwait\n' > /app/start.sh && \
    chmod +x /app/start.sh

CMD ["/app/start.sh"]
