# Roteiro de validação — pausa de mídia no Console durante o PIN pelo telefone (VOZ-37, 2ª rodada)

> **O que este roteiro prova** e nenhum probe automático alcança: com uma **chamada telefônica
> de verdade** e o **Console de verdade**, o atendente humano convida um especialista de IA que pede
> o PIN pelo teclado, e durante esse bloco:
> - o Console mostra a faixa *"Áudio pausado"*;
> - a rota de token responde **409**;
> - o áudio **volta sozinho** quando o cliente termina.
>
> **Já coberto por gate automático (NIV-07):**
> - a pausa em si, o PIN fora do stream e dos logs: `probe_voz02_sip_inbound.sh` K3/K3p/K3g/K4;
> - a chamada para pool humano atendida na hora: `probe_voz35_sip_line.sh`.
>
> O que falta é o lado de quem ATENDE: a tela, o 409 visto pelo browser e a volta sem recarregar.

**Duração estimada:** 15 minutos. **Quem executa:** uma pessoa, com o celular e o Console na
máquina onde a stack roda. **Quem prepara e lê os logs:** a sessão do Claude que acompanha.

---

## 0 · Antes de começar

| item | como conferir |
|---|---|
| Stack demo de pé | `http://localhost:5174` abre a tela de login |
| **Borda SIP aberta** | no `.env.demo`, `PLUGHUB_SIP_EDGE=true`, e depois `bash infra/scripts/up.sh` (WSL, raiz do repositório). O `up.sh` diz se abriu ou por que recusou |
| Borda conferida | `bash infra/test/probe_sip_edge_surface.sh` → **VERDE** |
| Rede do Windows como **Privada** | sem isso a chamada morre antes do serviço SIP (§ 19 do `arc15-webrtc.md`) |
| O celular da rodada da VOZ-32 | é o que já ligou para o número americano com sucesso |
| **Fone de ouvido no PC** | o celular fica perto do PC; sem fone, o Console realimenta |

⚠️ **Com o Console logado num pool `probe_*`, os probes automáticos reprovam** — o agente real pega
os contatos deles. Este roteiro usa só `voz37_humano`.

---

## 1 · O cenário (preparado pela sessão; repetir não custa)

```bash
bash infra/scripts/voz37_round.sh preparar
```

O comando cria ou confere:

| peça | o que é |
|---|---|
| `voz37_humano` | pool **humano** de `voice`, política de mídia de áudio, e `@pin` → `voz37_pin_ia` em *mentionable_pools* |
| `voz37_pin_ia` | pool de **IA** de `voice` rodando `skill_voz37_pin_especialista_v1` (fixture em `infra/test/fixtures/`) |
| escopo | os dois pools no `accessible_pools` do `admin@plughub.local` |

O especialista:
1. se apresenta;
2. pede o PIN pelo teclado, com o bloco **mascarado** (4 a 6 dígitos, terminando em `#`, 30 s
   para a primeira tecla);
3. confirma e devolve o cliente ao atendente. O PIN nunca volta a ninguém.

**Saia e entre de novo no Console** depois do `preparar`: o escopo novo só vai para o token num
login novo.

---

## 2 · O atendente fica disponível

1. Abra **Console** (`http://localhost:5174/console`) como `admin@plughub.local`.
2. Selecione o pool **`voz37_humano`** e fique disponível.
3. Quando o browser pedir, **permita o microfone** para `localhost:5174`.

**Esperado:** o pool aparece na lista. Se não aparecer, refaça o login (passo 1).

Avise a sessão: **"pronto para ligar"**. Ela roda o `apontar`, que faz o número americano tocar no
`voz37_humano` e guarda o pool de origem para devolver depois. Depois ela diz quando o cache do
gateway venceu (~40 s). **Anote o horário** em que você ligar.

---

## 3 · Ligar e observar

Do celular, ligue para **+1 551 324 1410**. Marque cada linha como ✅ / ❌; em ❌, anote o que
apareceu e o horário.

| # | onde | esperado |
|---|---|---|
| P1 | celular | a chamada é **atendida na hora** e fica em **silêncio** — nada de toque longo nem queda aos 60 s. É a linha da VOZ-35 |
| P2 | Console | o contato aparece e abre a sobreposição de chamada (*conectando* → áudio) |
| P3 | os dois | fale do celular e ouça no Console; fale no Console e ouça no celular |

Agora, no Console, aba **Ações** → seção **Agentes** → linha **`pin`** → **Acionar**. Se abrir o
campo de contexto, deixe vazio e clique **Convidar**. O equivalente pelo teclado é escrever `@pin` na
caixa de mensagem do Console e enviar.

| # | onde | esperado |
|---|---|---|
| P4 | celular | você ouve *"Olá, eu sou o assistente de segurança…"* e depois *"Por favor, informe o seu PIN. Digite e termine com jogo da velha."* — a segunda frase é o canal quem acrescenta, e deve soar **uma vez** só |
| P5 | Console | aparece a faixa ***"Áudio pausado: o cliente está digitando um dado protegido no telefone. Volta quando ele terminar."*** e você **deixa de ouvir** o celular |
| P6 | Console, F12 → **Rede** (filtro `token`) | pedidos a `/webrtc/token/…` respondendo **409**, um a cada ~2 s, enquanto a faixa estiver na tela |

No celular, digite **`73915#`**. É um PIN de teste fixo, para a sessão poder procurá-lo nos logs.
Anote se ouviu algo a cada tecla (bipe, silêncio ou o dígito falado): o eco em voz é a `NIV-06`, e
**o dígito falado seria defeito**.

| # | onde | esperado |
|---|---|---|
| P7 | celular | *"PIN recebido, obrigado. Vou devolver você ao atendente."* |
| P8 | Console | a faixa **some sozinha**, sem recarregar a página; o último pedido de token na aba Rede é **200** |
| P9 | os dois | o áudio **voltou nos dois sentidos** (repita o teste do P3) |
| P10 | Console | o PIN **não aparece** em lugar nenhum do Console: histórico, contexto e cartão do especialista |

**Opcional (desfecho sem tecla):** convide `@pin` de novo e **não digite nada**.

| # | onde | esperado |
|---|---|---|
| P11 | celular | em ~30 s: *"Não recebi o PIN. Vou devolver você ao atendente."* |
| P12 | Console | a faixa some sozinha também nesse desfecho, e o áudio volta |

Para encerrar, **desligue no celular**.

| # | onde | esperado |
|---|---|---|
| P13 | Console | o contato é encerrado e a sobreposição de chamada fecha |

---

## 4 · Devolver o resultado

Mande de volta, em texto:

1. a tabela P1–P13 marcada;
2. o **horário em que ligou** (a sessão usa como início da leitura dos logs);
3. em qualquer ❌: o horário aproximado, um print se possível, e o que o console do browser
   (F12 → Console) mostrou naquele momento.

A sessão então roda:

```bash
bash infra/scripts/voz37_round.sh evidencias <horário em UTC, ex. 2026-09-24T14:05:00Z> 73915
bash infra/scripts/voz37_round.sh devolver
```

O `evidencias` confronta a tela com os logs:
- a linha atendendo e a voz do especialista entrando;
- a pausa e quem saiu da sala;
- quantos 409 e quantos 200 a rota de token deu para aquela sessão;
- o PIN ausente dos logs de gateway, bridge, motor e mcp-server, e do stream da sessão.

⚠️ O PIN é procurado como texto. Um `ALERTA` pode ser coincidência com um id hexadecimal, e a
sessão confere a linha antes de concluir.

---

## 5 · Depois

- **Feche a borda SIP:** `PLUGHUB_SIP_EDGE=false` no `.env.demo` e `bash infra/scripts/up.sh`.
- O número americano volta ao pool de antes pelo `devolver`. Se a rodada for interrompida, rode o
  `devolver` mesmo assim: ele só age se o `apontar` guardou a origem.
- Os pools `voz37_*` podem ficar: são dado do tenant de demo, e reaproveitá-los pula o passo 1.

## O que este roteiro NÃO cobre

- **A tela do supervisor** com a mesma faixa: ela nunca foi montada (`VOZ-38`).
- **Gravação** durante o bloco: a pausa da gravação é medida pelo `probe_voz06_recording.sh`, e os
  pools desta rodada não gravam.
- **Tecla adiantada:** o cliente digitando o PIN antes de o especialista pedir chega ao humano. É
  o risco residual registrado na NIV-07.
- **Intrusão durante o bloco** (alguém entra na sala e a coleta é desfeita): coberta pelo teste
  unitário e pela testemunha ao vivo da NIV-07 (`CHANGELOG.md` § 2026-09-18 (3)).
