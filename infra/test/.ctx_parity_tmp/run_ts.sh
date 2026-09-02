#!/bin/sh
cd /repo/packages/schemas || exit 90
# esbuild em vez de tsx/ts-node: é o único transpilador já presente em node_modules, e
# esta bancada não tem rede dentro do container. `--bundle` puxa o FONTE do schemas
# junto, então o runner mede o fonte, nunca um `dist/` que pode estar atrasado.
./node_modules/.bin/esbuild --bundle --platform=node --format=cjs \
  --log-level=error --outfile=/tmp/ctx_runner.cjs \
  /repo/infra/test/_ctx_extract_runner.ts 2>/tmp/ts.err || exit 91
node /tmp/ctx_runner.cjs /repo/infra/test/fixtures/context_tag_surfaces.json 2>>/tmp/ts.err
