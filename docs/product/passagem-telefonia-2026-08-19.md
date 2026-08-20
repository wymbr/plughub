# Prompt de passagem — arco de TELEFONIA (sessão de 2026-08-19)

> Cole o bloco abaixo ao abrir a sessão nova (Opus, sessão limpa).
>
> ⚠️ **Esta passagem é de ARCO, não de sessão cronológica.** Ela NÃO sucede a
> [`passagem-2026-08-20.md`](passagem-2026-08-20.md) nem a encerra — aquele arco (P2, fila, `session:{id}:meta`,
> capacidade × pausa) segue exatamente onde estava. Esta sessão não tocou em código, não subiu stack, não rodou
> gate nenhum: foi **desenho e higienização de documentação**. Se você veio para continuar o P2, use a outra
> passagem e ignore esta.
>
> **Nota sobre a data:** todo o material desta sessão está carimbado `2026-08-19`, de forma consistente. Um
> `grep -rn "2026-08-19"` recupera o arco inteiro. As datas do repositório são pouco confiáveis como cronologia
> (sessões anteriores as trataram como números sequenciais) — use-as como **marcador de sessão**, nunca para
> ordenar fatos.

---

```
continuação sessão plughub — arco de TELEFONIA (desenho, sem código).

═══ REGRAS OPERACIONAIS ═══
Esta sessão não roda stack. As regras completas de ambiente estão em
docs/product/passagem-2026-08-20.md — não duplico aqui. As que valem sempre:
· o sandbox bash NÃO alcança o WSL — eu edito (Read/Write/Edit), você roda e cola
· inglês no código, PT em i18n/spec/dados de tenant
· CLAUDE.md ≤ 800 linhas; ✅ vai para CHANGELOG, nunca fica no CLAUDE.md
· toda decisão estrutural vira ADR em docs/adr/

═══ DE ONDE VEIO ═══
Pedido inicial: integrar o PlugHub a uma Avaya IP Office por CTI/CSTA.
O levantamento derrubou a premissa e o arco virou outra coisa — leia o "achado
que reposicionou" abaixo antes de qualquer implementação.

═══ ACHADO QUE REPOSICIONOU O ARCO ═══
O canal `voice` NÃO RODA. `VoiceAdapter.handle_inbound` chama CINCO métodos que
não existem em packages/channel-gateway — `_open_session`, `_route_inbound`,
`_publish_inbound`, `_normalize_text`, `_normalize_menu_result`
(adapters/voice.py:236,247,433,558,565; ausentes em adapters/base.py:44-77).
Os cinco são MOCKADOS em tests/test_voice_adapter.py:116-121 — o teste CRIA o que
a produção não tem e verifica que foi chamado. Suíte verde, AttributeError em
runtime real, zero sessões de voz no ambiente desde sempre.
Correlatos no mesmo adapter: `channel_name` em vez de `channel` (:90); `stt_queue`
nunca drenada e `_handle_stt_result` sem chamador (:624-629,657) ⇒ collect por voz
MORTO, só DTMF; `hangup` lendo chave nunca escrita (:884); `_get_contact_id`
retornando None por construção (:1032-1037); `deliver_outbound` (81 linhas) nunca
invocado (:772 vs outbound_consumer.py:95-106).
⚠️ NÃO tente consertar o VoiceAdapter. A decisão é RECONSTRUIR — ver V2 abaixo.

═══ DOIS ARCOS, NÃO UM — a fronteira é ATENDIMENTO × TELEFONIA INTERNA ═══
A plataforma não pretende ser PABX. Disso caem dois MODOS, que são ofertas
paralelas para clientes diferentes, NÃO fases um do outro:

MODO CTI  → docs/adr/adr-cti-gateway-multi-driver.md (proposto)
  PABX ancora · mídia nunca sai da LAN · SEM IA na voz · matriz de drivers
  Serviço on-prem packages/cti-gateway/ sobre perfil reduzido de CSTA
  Fases F0 (núcleo+IPO) → F1 (2º driver, estruturalmente diferente, OBRIGATÓRIA
  antes de qualquer outro) → F2 (demanda)
  NENHUM pré-requisito bloqueante

MODO SIP  → docs/adr/adr-voice-media-plane.md (proposto)
  Plataforma ancora · IA na voz · transcrição · gravação · portabilidade universal
  NÃO é integração com Avaya: a parte Avaya é UM TRONCO E UM `REFER`
  Fases V-F0 (infra de pé) → V-F1 (perna SIP) → V-F2 (bot leg STT/TTS) →
  V-F3 (gravação) → V-F4 (egress+supervisão) → V-F5 (validação)
  BLOQUEADO por plano de mídia, que está em ZERO

"Mais simples" ≠ "caminho mais curto até algo de pé": SIP é conceitualmente mais
simples E mais portável (todo PABX manda SIP ⇒ zero matriz de drivers), mas
depende de um plano de mídia inexistente. CTI é mais complexo e não depende de nada.

═══ DECISÕES QUE NÃO DEVEM SER REABERTAS SEM MEDIÇÃO NOVA ═══
V1  o plano de mídia NÃO tem topologia própria — acompanha o deploy da plataforma.
    Elimina SFU SaaS; on-prem = sem WAN e sem SBC de graça; nuvem = SBC é do produto
V3  UM ÚNICO plano de mídia: a SALA. Entrada por SIP ou navegador, internamente
    sempre a sala. Custos nomeados: transcodificação G.711↔Opus · SFU como ponto
    único de falha · `REFER` de saída no gateway SIP (risco novo)
    "Internamente só WebRTC" é IMPRECISO — unifica-se a SALA, não o protocolo
V5  gravação no AttachmentStore, sem store próprio; retenção = config POR CLASSE
    (namespace `storage`). Hoje é attachment_expiry_days=30 em env, número único,
    sem UI ⇒ viola 3 invariantes de config E apagaria gravação
V6  `_dev_mode` SAI — sem credencial o provider RECUSA ALTO. Token bem-formado e
    falso é o valor plausível mais caro do repositório: deixou o Arc 15 passar por
    pronto por meses, sobrevivendo a revisões de arquitetura
D4  capability declarada por driver, recusa alta, NUNCA emulação muda
D5  identidade da chamada é do GATEWAY, por componente conexa sob aliases —
    REUSA o padrão root_session_id+union-find, não inventa o 3º mecanismo
D8  ramal = 2 fatos: elegibilidade→auth.users · alocado→hash da instância.
    Monitor ⟺ alocação (segura o teto de sinalização do IPO).
    DESCARTADO: descobrir ramal por IP da estação (VLAN de voz × dados)
D10 nenhum driver na matriz de suporte sem TRAÇO GRAVADO (Record/Replay vira
    infra do arco, não backlog)
D12 distribuição no modo CTI = roteamento assistido (preferível) OU ponto de
    espera SEM ATENDENTE + deflect. Sem uma das duas, o modo CTI não entrega
    distribuição — vira observabilidade. É a capability mais decisiva da matriz.
    ⚠️ A PAUSA NÃO É DEPENDÊNCIA DA DISTRIBUIÇÃO (o árbitro é a plataforma)

═══ O QUE FOI ENTREGUE ═══
docs/adr/adr-cti-gateway-multi-driver.md      novo · D1–D12 · emendado com §0
docs/adr/adr-voice-media-plane.md             novo · V1–V10 · V3 emendada
docs/product/folder-tecnico-voz-telefonia.html         interno (eng + pré-venda)
docs/product/documento-tecnico-integracao-avaya-ipo.html  CLIENTE = a própria AVAYA
CLAUDE.md · TODO.md · INDEX.md                índice + seção "Telefonia — DOIS arcos"
+ 27 arquivos com banner de correção (canal voice / Arc 15 / discador)

═══ HIGIENIZAÇÃO — o que mudou no acervo e por quê ═══
3 docs de estado (visao-geral, layers/01-channel-layer, product/value-proposition)
afirmavam voz ENTREGUE desde a auditoria de 2026-05; pacotes/channel-gateway
carimbava "✅ Implementado". Corrigidos com a medição.
MATERIAL COMERCIAL corrigido, e este é o item sensível:
· material-sponsor-operacao-piloto.md dizia "se exigir voz é conversa de escopo e
  prazo — não impedimento técnico". É impedimento técnico.
· operacao-piloto-e-rodada-2.md tem um ROTEIRO DE DISCURSO inteiro sobre "temos
  voz e escolhemos não começar por ela". A recomendação de escopo continua certa;
  o fundamento e a fala mudam. ⚠️ PENDÊNCIA DO TIME, não da documentação:
  retomar a decisão antes da próxima conversa comercial.
LIÇÃO DE MÉTODO registrada em revisao-documentacao-2026-05.md: aquela auditoria
reclassificou `voice` de "lacuna aspiracional" para "implementado" LENDO
channel-gateway-multi-channel.md §9, sem executar nada. Auditoria que compara
documento com documento PROPAGA a afirmação. Critério fixado: "implementado" =
existe caminho EXECUTADO; sem execução observável, a classificação é INCONCLUSIVO.

═══ TAREFA — escolher, não fazer tudo ═══
Nada aqui é continuação obrigatória. Os dois ADRs estão fechados em DESENHO e
marcados como PROPOSTOS — não implementados, não ratificados.

1. **Classificação da borda SIP** — probe_edge_surface.sh só conhece prefixo HTTP;
   sinalização SIP está inteiramente fora da tabela de exposição. É o mesmo buraco
   que a allowlist HTTP foi escrita para fechar. Pré-requisito de V-F0.
2. **Script das 5 medições da §8 do ADR de CTI** contra central de homologação,
   para que o resultado seja TRAÇO e não relato. Começo natural do harness da D10.
3. **TODO.md § "Telefonia — DOIS arcos"** tem a lista completa, incluindo o
   derivado de retenção por classe (viola 3 invariantes de config HOJE, em env).
4. Se o arco for adiante: V-F0 (provisionar SFU + tirar `_dev_mode`) é fase
   PRÓPRIA e PRIMEIRA. Enquanto ela não existir, qualquer fase seguinte pode ficar
   verde sem funcionar.

═══ BLOQUEIOS QUE NÃO SÃO NOSSOS ═══
· 45 perguntas no questionário da §9 do documento do cliente (20 bloqueantes).
  As 4 que condicionam o desenho: existência+regime da interface no IPO ·
  mecanismo de encaminhamento sob controle da aplicação · limites de capacidade ·
  topologia do enlace SIP + licenciamento
· default de retenção por classe de gravação (negócio + jurídico)
· comportamento em falha do desvio de tronco (reversão automática ou operada)

═══ ARMADILHAS ESPECÍFICAS DESTE ARCO ═══
· CSTA no IP Office é PREMISSA DO CLIENTE, não fato verificado. A doc pública do
  IPO cobre TAPI e DevLink3; CSTA no portfólio Avaya aparece via AES sobre
  Communication Manager. O documento do cliente já pergunta isso como PRIMEIRA
  pergunta. NÃO reescrever como fato.
· o interlocutor é a PRÓPRIA AVAYA. Não pedir doc que ela publica; não explicar o
  produto dela; não depreciar IPOCC/ACCS/Experience Platform (há seção de
  coexistência no documento — mantê-la)
· Topologia B (desvio no SBC/IPO) é preferência de POSICIONAMENTO, não invariante
  técnico — e nela o enlace interno carrega 100% do tráfego de atendimento, logo
  2 canais da central por chamada. O corolário ORIGINAL do ADR dizia o contrário
  ("só nas transferidas") e foi emendado. Não reler a versão antiga como regra.
· "não alterar a rotina do agente" é PRINCÍPIO declarado do modo CTI. Onde houver
  escolha entre pedir passo novo e ler o que a operação já faz, LEIA.
```

---

## Anexo — mapa dos artefatos

| Arquivo | O que é | Estado |
|---|---|---|
| `docs/adr/adr-cti-gateway-multi-driver.md` | Modo CTI — modelo canônico, drivers, capability, D1–D12 | proposto |
| `docs/adr/adr-voice-media-plane.md` | Modo SIP — plano de mídia próprio, V1–V10 | proposto |
| `docs/product/folder-tecnico-voz-telefonia.html` | Interno — eng + pré-venda, dois modos lado a lado | v1.0 |
| `docs/product/documento-tecnico-integracao-avaya-ipo.html` | **Cliente (Avaya)** — 11 seções + questionário de 45 perguntas | v1.0 |
| `TODO.md` § *Telefonia — DOIS arcos* | Dívidas, fases e o derivado de retenção | aberto |
| `CLAUDE.md` § índice de ADRs | Duas linhas novas no bloco `docs/adr/` | atualizado |

## Anexo — o que este arco NÃO fez

- **Nenhuma linha de código.** Nenhum build, nenhum gate, nenhuma stack de pé.
- **Não consertou o canal `voice`** — por decisão (V2: reconstruir, não remendar).
- **Não provisionou SFU** — é V-F0, e é fase própria.
- **Não tocou** no P2, na fila, em `session:{id}:meta`, em capacidade × pausa nem em
  nenhum item da passagem de 2026-08-20.
