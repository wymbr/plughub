# Commerce Cards — Vocabulário de Interação do Nível (c) · Especificação

> **Contexto:** *Business in Any Media*, framework de loja. Define as primitivas de comércio que a **skill publicada no pool de I/O** (nível c) usa para conversar com o cliente — definidas **uma vez** no fluxo e renderizadas no formato nativo **mais rico** de cada canal.
> **Princípio inviolável:** o fluxo declara *o quê* (componente + dados); o **render por canal vive exclusivamente no Channel Gateway adapter** (invariante: "never implement channel-specific rendering logic in skill-flow"). Fallback/degradação é decisão do adapter.
> **Estende:** `menu` (interativo) e `notify` (display) + o enum `ChannelCapability` — não cria N steps novos.
> **Status:** especificação. Fiel a `packages/schemas/src/skill.ts`. **Data:** Junho 2026.

---

## 1. Como estende o modelo atual (fiel ao schema)

Hoje:
- `MenuStep.interaction ∈ {text, button, list, checklist, form}` + `options[]`/`fields[]`, `output_as`, `visibility`, `masked`.
- `NotifyStep` — mensagem unidirecional com `visibility`.
- `ChannelCapabilitySchema = {text, audio, video, file_upload, masked_input, rich_menu}` (Arc 16, seleção de canal).

A extensão é **um campo `component` tipado** em `notify` (display) e `menu` (interativo), carregando uma primitiva de comércio canônica e channel-agnostic. O `output_as` do `menu` passa a receber uma **ação canônica** (não só uma opção). Nada de novo step type.

```yaml
- id: mostrar_ofertas
  type: menu
  prompt: "Separei estas ofertas pra você:"
  component:
    type: carousel              # product_card | carousel | cart | checkout | order_status
    requires: [carousel]        # capability; adapter degrada se ausente
    data: "$.pipeline_state.ofertas"   # dados vêm de MCP (catálogo), não hardcoded
  output_as: escolha            # → { action, item_id?, qty? }
  on_success: revisar_cesta
```

Novos `ChannelCapability` (aditivos): **`rich_card`**, **`carousel`**, **`media_gallery`**. O `rich_menu` existente já cobre botões/listas; `payment` é coberto por `masked_input` + link.

---

## 2. As cinco primitivas (schemas canônicos)

Todos os dados são **channel-agnostic**; o adapter traduz. Valores monetários/sensíveis passam pelo mascaramento por role.

### 2.1 `product_card`
```jsonc
{ "type": "product_card",
  "data": { "id":"sku_123", "title":"Tênis X", "price":"R$ 299,90",
            "image_url":"…", "description":"…", "badges":["frete grátis"] },
  "actions": [ {"id":"buy","label":"Comprar"}, {"id":"details","label":"Detalhes"} ] }
```
Display (`notify`) ou selecionável (`menu`).

### 2.2 `carousel` / `catalog`
```jsonc
{ "type":"carousel",
  "data": { "items": [ <product_card.data>, … ], "page": 1, "has_more": true } }
```
Seleção retorna o `item_id` escolhido. Suporta paginação (próxima página = ação `more`).

### 2.3 `cart` (resumo de cesta)
```jsonc
{ "type":"cart",
  "data": { "lines":[ {"id":"sku_123","title":"Tênis X","qty":1,"unit":"R$ 299,90","subtotal":"R$ 299,90"} ],
            "total":"R$ 299,90", "discounts":[] },
  "actions":[ {"id":"checkout","label":"Finalizar"}, {"id":"edit","label":"Editar"} ] }
```
Ações de linha: `inc`/`dec`/`remove` com `item_id`+`qty`.

### 2.4 `checkout`
```jsonc
{ "type":"checkout",
  "data": { "summary": <cart.data>, "payment_methods":["pix","card"] },
  "requires":["masked_input"] }
```
Pagamento é **input mascarado** (`begin/end_transaction`, `@masked.*` in-memory) **ou** link do PSP. A plataforma **não decide nem guarda** dado de pagamento — captura e **repassa ao PSP/retaguarda do contratante via MCP**, guarda só o veredito (coerente com o princípio de identidade: não somos autoridade). Resultado: `{ action:"paid"|"failed"|"abandoned", ref? }`.

### 2.5 `order_status`
```jsonc
{ "type":"order_status",
  "data": { "order_id":"…", "steps":[ {"k":"placed","done":true,"at":"…"},
            {"k":"paid","done":true}, {"k":"shipped","done":false},
            {"k":"delivered","done":false} ], "tracking_url":"…" } }
```
Display (`notify`).

---

## 3. Semântica de resultado/ação

Um `menu` com `component` interativo grava em `output_as` uma **ação canônica**, e o adapter mapeia a interação nativa (tap num card, botão, dígito DTMF, número no SMS) para ela:

```jsonc
{ "action": "select|buy|add|inc|dec|remove|more|checkout|edit|paid|failed",
  "item_id": "sku_123",   // quando aplicável
  "qty": 1 }
```

O Skill Flow Engine continua recebendo **um único `interaction_result`** (como hoje) — a riqueza está no payload, não em novos contratos de engine.

---

## 4. Matriz de render por canal (vive no adapter)

O fluxo declara `component`; cada adapter renderiza no mais rico que o canal suporta e **degrada** quando falta capability. Exemplos:

| Componente | WhatsApp | Webchat | Voz | SMS | E-mail |
|---|---|---|---|---|---|
| product_card | imagem + corpo + reply buttons (ou product message do catálogo Meta) | card widget | TTS lê título+preço; DTMF p/ agir | "Tênis X — R$299,90. Responda 1 p/ comprar" | card HTML |
| carousel | list message / multi-product message | carrossel horizontal | lê top-N; "diga/disque o número" | lista numerada | grid HTML |
| cart | resumo texto + botões (Finalizar/Editar) | widget de cesta | lê itens + total | texto numerado | tabela HTML |
| checkout | link de pagamento / coleta mascarada sequencial | overlay mascarado + botão Pagar | DTMF mascarado (não persiste) ou "te envio o link" | link de pagamento | HTML com link |
| order_status | timeline em texto + link de rastreio | timeline widget | TTS | texto | HTML |

Regra: **capability ausente → degradação** definida no adapter (ex.: sem `carousel` → `rich_menu`/lista numerada; sem `rich_card` → texto + imagem solta). O fluxo nunca sabe disso.

---

## 5. Dados via MCP — nunca hardcoded

O conteúdo dos componentes vem de **MCP tools de domínio** do tenant (catálogo, estoque, preço, pedido, frete), buscados por `invoke` em (a) ou pela skill de I/O em (c). Toda chamada passa pelo **interception guard + audit**. A plataforma não embute catálogo nem regra de preço — orquestra. Ex.: `catalog_search`, `cart_get`, `order_create`, `order_status_get`, `payment_create` (PSP).

---

## 6. Checkout, pagamento e o princípio "não somos autoridade"

- Pagamento por **masked input** (`begin/end_transaction`) ou **link do PSP**. Dado de cartão/PIX nunca entra em stream, `pipeline_state` ou log; `@masked.*` é in-memory.
- A verificação/captura é **delegada ao PSP/retaguarda do contratante via MCP**; guardamos só `{ ok, ref }`. Mesma postura do `identity_verify` — capture-and-relay, decisão externa.
- DTMF de cartão na voz é mascarado e descartado; nunca persistido.

---

## 7. Webchat como referência rica (paridade+)

O WhatsApp é o vocabulário-referência (rico-porém-restrito). O **webchat adapter ganha os renderers ricos** (card, carrossel, cesta, checkout overlay) — pelo menos paridade com o WhatsApp e, podendo, mais (grid maior, imagem grande, carrossel fluido). É o passo concreto do "dar ao webchat as ferramentas do WhatsApp": elevar o vocabulário a canônico e deixar cada canal renderizar no seu teto.

---

## 8. Relação com (a)/(b)/(c)

- **(a)** fornece os **dados** (via `context`/MCP) e decide o passo de negócio — nunca toca render.
- **(c)** (skill publicada no pool de I/O) declara o `component` e conduz a interação; o **adapter** renderiza.
- **(b)** concilia canal (Arc 16): a seleção de canal usa as `ChannelCapability` (`rich_card`/`carousel`/`masked_input`) para escolher/oferecer o canal mais capaz quando há troca.
- O **agente de fila** (role=queue) pode usar os mesmos componentes (ex.: `product_card` de oferta enquanto espera, ou `order_status`).

---

## 9. Fases

| Fase | Entrega |
|---|---|
| **A — fundação** | `component` em `notify`/`menu`; capabilities `rich_card`/`carousel`/`media_gallery`; ação canônica em `output_as`; product_card + cart no **webchat** e **WhatsApp**; degradação base. |
| **B — catálogo/checkout** | carousel/catalog (paginação); checkout com masked input + PSP via MCP; order_status. |
| **C — demais canais** | render em voz (TTS/DTMF), SMS (numerado), e-mail (HTML); paridade de degradação. |
| **D — catálogo nativo WhatsApp** | mapear product/multi-product message do catálogo Meta quando conectado. |

---

## 10. Decisões (fechadas)

1. ~~`component` vs interaction novo~~ **Resolvido:** `component` é **campo** em `notify`/`menu` (não novos valores de `interaction`). Mantém os contratos de step e o engine intactos.
2. ~~Versionamento~~ **Resolvido:** todo componente carrega `component.version` em `@plughub/schemas`; adapters negociam por versão, evolução sem quebra.
3. ~~Ação canônica~~ **Resolvido (enum fechado):** `select | buy | add | inc | dec | remove | more | checkout | edit | paid | failed | abandoned`. Extensões futuras versionadas.
4. ~~Catálogo Meta~~ **Resolvido:** quando há catálogo conectado na WABA → product/multi-product message **nativo**; senão → render genérico (imagem + corpo + botões). Decisão do adapter, transparente ao fluxo (fase D).
5. ~~Checkout em voz~~ **Resolvido:** **default seguro = enviar link de pagamento** (SMS/WhatsApp); DTMF mascarado é opcional por configuração. Nunca persistir dado de pagamento.
