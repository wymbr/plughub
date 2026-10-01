# Validação do canal `a2a` com clientes corporativos — roteiro

> **Para quem:** o dono da instalação, com URL pública e contas nos produtos. **Por que um roteiro e
> não um probe:** os três clientes abaixo são produtos de terceiros com conta, console e termos
> próprios; nenhum roda num container contra `localhost`. O que dava para medir sem eles — os dois
> SDKs oficiais (Python e JavaScript) e o isolamento entre tenants — está medido em
> `infra/test/probe_aas08_a2a_sdk.sh` (AAS-08). Este roteiro é a ficha **AAS-18**.
>
> As telas dos produtos mudam; o roteiro diz **o que observar**, não onde clicar. Onde ele afirma o
> comportamento de um produto, a afirmação é **a conferir** — anote o que viu.

## 0. Antes de qualquer cliente

| Pré-requisito | Como conferir |
|---|---|
| A borda publica **`/a2a`** em HTTPS, e só os prefixos públicos (`probe_edge_surface.sh`) | `curl -sI https://<publico>/a2a/<slug>/.well-known/agent-card.json` → `200` |
| `PLUGHUB_A2A_PUBLIC_BASE_URL` = a base PÚBLICA (o card anuncia a interface a partir dela) | o `supportedInterfaces[0].url` do card começa com `https://<publico>/` |
| Pool de contato com o canal `a2a`, descritor `a2a` `discoverable: true`, deploy `current` e um `ChannelEndpoint` `a2a` | tela `/config/channels`; o card responde `200` |
| Um principal `partner` (tela `/config/agents`) com o pool em `allowed_pools`, e a credencial `pha_…` copiada na hora (ela não é mostrada de novo) | `POST /a2a/<slug>/` com `Authorization: Bearer pha_…` e `A2A-Version: 1.0` não responde `401`/`403` |

Duas coisas que a AAS-08 mediu e que derrubam cliente sem mensagem clara do lado dele:

1. **O endereço do agente termina em `/`** (`https://<publico>/a2a/<slug>/`). Cliente que recebe a
   URL sem a barra e resolve `.well-known/agent-card.json` por RFC 3986 — o SDK JavaScript faz isso —
   busca `/a2a/.well-known/…`, recebe `404` e diz "card não encontrado". **Cadastre sempre o
   endereço com a barra**, que é o que o card anuncia.
2. **`A2A-Version` ausente vale 0.3** (spec v1.0 § 3.6), e esta interface só fala **1.0**: a resposta
   é `-32009 VersionNotSupportedError`, com `data.requested` dizendo o que o cliente pediu e
   `data.assumed: true` quando ele não mandou nada. **Se um cliente corporativo ainda fala 0.3, é aqui
   que ele para** — anote a versão que ele manda (log do gateway: `a2a: versão … recusada`).

## 1. O que se observa em cada cliente

O percurso é o mesmo do probe, para comparar: pedido inicial → pergunta do pool → resposta → artefato.

| Passo | Esperado (o que a plataforma faz) | Onde conferir do lado PlugHub |
|---|---|---|
| Cadastro pelo card | o cliente lê nome, descrição, skills e o esquema de segurança `Bearer` | log do gateway: `GET /a2a/<slug>/.well-known/agent-card.json 200` |
| Pedido inicial | task nova em `INPUT_REQUIRED` com o prompt numerado e o JSON Schema da resposta | Monitor → Sessões, canal `a2a` |
| Resposta (texto `2` ou o `data` do schema) | `COMPLETED` com um artefato `data` | a sessão fecha com `flow_complete`; `{t}:session:{sid}:result` |
| Pedido fora do contrato (`input_schema`) | `-32602` nomeando o campo — nada nasce | nenhuma sessão nova |
| Cancelar (se o cliente oferece) | `CANCELED`; `close_reason = caller_cancel` | `sessions.close_reason` |
| Outro principal lendo a task | `-32001` (a task "não existe" para ele) | — |

### OpenClaw

Pelo ADR (D15.4): só bearer, texto e JSON, **polling** (sem streaming) e **sem cancelar**. Observar:
se ele faz `GetTask` em laço até sair de `INPUT_REQUIRED` — e como ele mostra o prompt ao usuário;
se manda `A2A-Version`; se aceita o artefato `data` ou só lê `text`.

### Copilot Studio (Microsoft)

Observar: como o produto cadastra um agente A2A (pela URL do card ou pela da interface); se manda
`A2A-Version: 1.0`; se usa `SendStreamingMessage` (o card anuncia `streaming: true`; o stream fecha
em `INPUT_REQUIRED` e o cliente continua com `SendMessage`); como ele apresenta a pergunta
intermediária ao usuário.

### Gemini Enterprise (Google)

Observar o mesmo, e mais: se ele pede **A2UI** (extensão de interface rica). O canal ainda não a
oferece — é a **AAS-17**, que espera exatamente esta validação para escolher a versão da spec contra
um cliente que renderize. Anote a versão de A2UI que o produto anuncia.

## 2. Registrar

Para cada cliente: versão do produto, data, `A2A-Version` enviada, passos que funcionaram e o
primeiro que falhou com a resposta crua (o corpo JSON-RPC). Incompatibilidade nossa vira ficha no
grupo do `adr-a2a-server-binding`; incompatibilidade do cliente com a spec v1.0 vira nota no ADR
(§ 6, A6), para que ninguém "conserte" a porta afrouxando o contrato.
