/**
 * Gerador do deck técnico do PlugHub (.pptx) — 23 slides.
 *
 * Uso (a partir da raiz do repo; pptxgenjs já está em node_modules da raiz):
 *   node docs/product/folder-tecnico-deck.build.js
 *
 * Saída: plughub-descritivo-tecnico.pptx no diretório corrente (ignorado pelo git).
 *
 * Público-alvo: arquitetura de cliente prospectivo. Reunião de 30–40 min.
 * Conteúdo espelha docs/product/folder-tecnico-plughub.html (23 páginas A4), página a página.
 * Slide 21 (análise sobre os KPIs) e a prova por login federado (slides 12 e 17) entraram em
 * 2026-10-01: roadmap, fichas ANL-01/02, KPI-06/07/08, PMN-01 e FED-01/02, PID-21.
 * Notas do apresentador em todos os slides (painel de notas do PowerPoint).
 *
 * Revisão de 2026-10-01:
 *  · Incorpora o que tinha sido editado à mão no .pptx (evidência de mercado, perguntas novas,
 *    notas) e passa a ser de novo a fonte do deck — editar aqui, não no PowerPoint.
 *  · Ênfase na fronteira para agentes de automação (slides 15–18): canal a2a, contrato, identidade
 *    e mandato, ferramentas por MCP. Fonte: docs/adr/adr-a2a-server-binding.md (rev. 2) e as fichas
 *    AAS-* do pending.md. Cada afirmação traz o estado — só A0 (canal + descritor) existe.
 *  · Sai a correção de 2026-08-19 sobre áudio: o plano de mídia foi reconstruído (VOZ-01/02/04/06/32).
 *    Continua NÃO existindo chamada sainte (VOZ-33), e por isso nem discador.
 *  · Corrigido o slide de fronteira anterior, que dizia "MCP · EM OPERAÇÃO — o agente do seu time chama
 *    as ferramentas da plataforma": o transporte do servidor MCP é interno (credencial de serviço desde
 *    a CAP-10) e não tem tráfego de terceiro. Ao agente de fora se publica o POOL.
 */
const pptxgen = require("pptxgenjs");

const GR = "2E3138", GR2 = "3E434D", CH = "990011", TL = "046A6A";
const OW = "F2F2F2", MU = "6B7280", WH = "FFFFFF", PALE = "E39AA4", LT = "C9CDD4";
const TLD = "0E4E4E", TLP = "8FB8B8", CDE = "CDE3E3", CFE = "CFE7E7", PK = "FBE4E7", TLT = "DDEDED";
const HF = "Cambria", BF = "Calibri";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";           // 13.33 x 7.5
pres.author = "PlugHub";
pres.title = "PlugHub — descritivo técnico";

const M = 0.55, CW = 13.33 - 2 * 0.55;
const C2 = 6.83, W2 = 5.95;                 // segunda coluna de duas
const C3 = [M, 4.72, 8.89], W3 = 3.9;       // três colunas

function kicker(s, t, c, y) {
  s.addText(t, { x: M, y: y === undefined ? 0.32 : y, w: 9, h: 0.26, fontSize: 10, bold: true,
    color: c || CH, fontFace: BF, charSpacing: 2, margin: 0 });
}
function title(s, t, c, size, y) {
  s.addText(t, { x: M, y: y === undefined ? 0.58 : y, w: CW, h: 0.8, fontSize: size || 29, bold: true,
    color: c || GR, fontFace: HF, margin: 0, valign: "top" });
}
function lead(s, t, y, c, w, size) {
  s.addText(t, { x: M, y: y, w: w || 11.4, h: 0.62, fontSize: size || 13.5,
    color: c || GR2, fontFace: BF, margin: 0, valign: "top", lineSpacingMultiple: 1.12 });
}
function card(s, o) {
  s.addShape(pres.ShapeType.roundRect, { x: o.x, y: o.y, w: o.w, h: o.h,
    fill: { color: o.fill || OW }, line: { color: o.line || o.fill || OW, width: o.line ? 1 : 0,
      dashType: o.dash ? "dash" : "solid" }, rectRadius: 0.06 });
  let ty = o.y + 0.13;
  if (o.head) {
    s.addText(o.head, { x: o.x + 0.22, y: ty, w: o.w - 0.44, h: 0.34, fontSize: o.headSize || 13,
      bold: true, color: o.headColor || GR, fontFace: BF, margin: 0, valign: "top",
      italic: o.headItalic || false });
    ty += (o.headGap || 0.4);
  }
  if (o.body) {
    s.addText(o.body, { x: o.x + 0.22, y: ty, w: o.w - 0.44, h: o.y + o.h - ty - 0.1,
      fontSize: o.size || 10.5, color: o.bodyColor || GR2, fontFace: BF, margin: 0,
      valign: "top", lineSpacingMultiple: 1.1 });
  }
}
function numDot(s, x, y, n, d) {
  const dd = d || 0.34;
  s.addShape(pres.ShapeType.ellipse, { x: x, y: y, w: dd, h: dd, fill: { color: CH } });
  s.addText(String(n), { x: x, y: y, w: dd, h: dd, fontSize: 12, bold: true,
    color: WH, fontFace: BF, align: "center", valign: "middle", margin: 0 });
}
function arrow(s, x, y, w, c) {
  s.addText("→", { x: x, y: y, w: w, h: 0.3, fontSize: 15, color: c || CH,
    align: "center", valign: "middle", fontFace: BF, margin: 0 });
}
function tbl(s, rows, opt) {
  const head = rows[0].map(t => ({ text: t, options: { bold: true, color: WH, fill: { color: GR } } }));
  s.addTable([head].concat(rows.slice(1)), Object.assign({
    x: M, w: CW, fontSize: 10, fontFace: BF, color: GR2, fill: { color: WH },
    border: { type: "solid", color: "DDDEE1", pt: 0.75 }, valign: "middle", margin: 0.08
  }, opt));
}
function chip(s, x, y, w, t, c) {
  s.addShape(pres.ShapeType.roundRect, { x: x, y: y, w: w, h: 0.24,
    fill: { color: "FFFFFF", transparency: 100 }, line: { color: c, width: 1 }, rectRadius: 0.04 });
  s.addText(t, { x: x, y: y, w: w, h: 0.24, fontSize: 8, bold: true, color: c,
    fontFace: BF, align: "center", valign: "middle", charSpacing: 0.6, margin: 0 });
}
function pageFoot(s, left, n, dark) {
  s.addText(left, { x: M, y: 7.02, w: 8, h: 0.26, fontSize: 9, color: dark ? "8A9099" : MU,
    fontFace: BF, margin: 0 });
  s.addText(String(n), { x: 12.0, y: 7.02, w: 0.78, h: 0.26, fontSize: 9,
    color: dark ? "8A9099" : MU, fontFace: BF, align: "right", margin: 0 });
}

/* ---------------- 1 · capa ---------------- */
let s = pres.addSlide();
s.background = { color: GR };
s.addShape(pres.ShapeType.ellipse, { x: 9.1, y: -2.5, w: 7.2, h: 7.2, fill: { color: GR2 } });
s.addText("PLATAFORMA DE ATENDIMENTO E PROCESSOS", { x: M, y: 1.5, w: 8.6, h: 0.3,
  fontSize: 11, bold: true, color: PALE, fontFace: BF, charSpacing: 2.4, margin: 0 });
s.addText("Uma plataforma desenhada\npara orquestrar agentes\nhumanos e de IA.", {
  x: M, y: 2.0, w: 8.6, h: 2.5, fontSize: 40, bold: true, color: WH, fontFace: HF,
  margin: 0, valign: "top", lineSpacingMultiple: 1.06 });
s.addShape(pres.ShapeType.rect, { x: M, y: 4.65, w: 1.6, h: 0.06, fill: { color: CH } });
s.addText("Sessão é sala de conferência, não fila de passagem · a competência entra na conversa em vez de o cliente ser passado adiante · licença por capacidade simultânea · a mesma operação atende quem chega por um canal e quem chega pelo agente de automação dele",
  { x: M, y: 4.95, w: 8.8, h: 1.1, fontSize: 13, color: LT, fontFace: BF, margin: 0, lineSpacingMultiple: 1.15 });
s.addText("Descritivo técnico · 2026", { x: M, y: 6.6, w: 6, h: 0.3, fontSize: 10.5, color: "8A9099", fontFace: BF, margin: 0 });
s.addNotes("Abrir dizendo que não é apresentação de funcionalidades: é o argumento de arquitetura, com os limites declarados. 30–40 min, perguntas ao longo.\n\nA última frase do subtítulo é a novidade deste material: a plataforma atende também o agente de automação do cliente ou do parceiro, por protocolo aberto. Os slides 15 a 18 tratam disso, e o 22 diz o que já existe e o que é roadmap.");

/* ---------------- 2 · as perguntas ---------------- */
s = pres.addSlide();
kicker(s, "AS PERGUNTAS");
title(s, "Oito perguntas sobre o curso natural de um atendimento");
s.addText("Nenhuma é sobre funcionalidade faltando. Todas descrevem algo que a operação aprendeu a contornar — e o contorno virou processo, treinamento e indicador. A última ainda não virou contorno porque é nova.",
  { x: M, y: 1.36, w: 12.0, h: 0.42, fontSize: 12, color: GR2, fontFace: BF, margin: 0 });
const qs = [
  ["“Trazer um especialista exige transferir o cliente?”", "Não é falta de roteamento por competência: é que ela mora em outra fila, e alcançá-la significa passar o cliente adiante.", 0.5],
  ["“A necessidade do cliente sempre termina em um atendimento?”", "Quatro conversas sobre o mesmo caso viram quatro registros e três falhas de FCR.", 0.5],
  ["“Se a resposta não existe agora, como se dá continuidade?”", "Protocolo, planilha ou campanha montada depois. O caso fica parado esperando alguém lembrar dele.", 0.5],
  ["“Por que se disca para centenas de pessoas tudo junto? Quantas vezes já entramos em contato? Estamos incomodando?”", "Boa parte dessas ligações só entrega uma informação. E ninguém soma entre discador, SMS e WhatsApp.", 0.68],
  ["“Como cada agente visualizou os dados sensíveis?”", "A pergunta sobre quem vê o dado é antiga. A nova — o que uma IA consultou, sob que permissão — quase nunca tem resposta.", 0.5],
  ["“Como você compara uma pessoa e uma IA que fazem o mesmo trabalho?”", "Contenção do bot de um lado; TMA, monitoria e pesquisa do outro. Universos separados, em sistemas diferentes.", 0.5],
  ["“Quantos contatos foram necessários para resolver o problema?”", "Relatórios e estatísticas são de contatos, não se ligam ao problema do cliente.", 0.5],
  ["“Quando quem procura você for o agente do seu cliente — ou o do seu parceiro —, quem atende, e com que garantias?”", "Hoje ele chega pelo webchat se passando por cliente, ou por integração sob medida que pula fila, SLA e auditoria.", 0.68]
];
qs.forEach((q, i) => {
  const col = i % 3, row = Math.floor(i / 3);
  const last = i === 7;
  card(s, { x: C3[col], y: 1.9 + row * 1.68, w: W3, h: 1.56,
    fill: last ? TL : OW, head: q[0], headColor: last ? WH : CH, headSize: 10.5, headItalic: true,
    headGap: q[2], body: q[1], size: 9, bodyColor: last ? TLT : GR2 });
});
card(s, { x: C3[2], y: 1.9 + 2 * 1.68, w: W3, h: 1.56, fill: CH, headColor: WH, bodyColor: PK,
  head: "A pergunta que vira o documento", headSize: 10.5, headGap: 0.36, size: 9.5,
  body: "Como seria o atendimento se ele seguisse o curso natural da necessidade do cliente — chegue ela por uma pessoa ou pelo agente dela —, e não o que a ferramenta permite?" });
pageFoot(s, "As perguntas", 2);
s.addNotes("Perguntar ao vivo qual delas dói mais. A quinta, a sexta e a sétima costumam ser as que ninguém tinha formulado assim.\n\nA oitava (em verde) é a que abre a conversa sobre agentes de automação. Ela não descreve um contorno antigo, e sim um que está começando: assistentes corporativos e pessoais já agem em nome de gente, e chegam pelo webchat ou pelo telefone se passando por cliente — o que contamina a métrica — ou por uma integração bespoke que pula fila, SLA, auditoria e avaliação. Nenhum dos dois diz quem chamou, em nome de quem, com que autorização. Voltamos a ela no slide 15.");

/* ---------------- 3 · a herança ---------------- */
s = pres.addSlide();
kicker(s, "A HERANÇA");
title(s, "Por que as plataformas de atendimento são do jeito que são");
s.addText("Não é desleixo nem falta de investimento — é sedimentação. Quase tudo que existe hoje descende de uma central telefônica, onde a unidade era a chamada, o recurso era o ramal e o canal era o tronco.",
  { x: M, y: 1.42, w: 11.6, h: 0.5, fontSize: 12.5, color: GR2, fontFace: BF, margin: 0, lineSpacingMultiple: 1.1 });
tbl(s, [
  ["Conceito da central", "O que virou", "O que ficou preso"],
  ["Chamada", "Contato, interação", "A unidade de medida segue sendo o contato isolado; o problema do cliente não existe como registro."],
  ["Ramal / assento", "Licença de agente", "O recurso é a pessoa logada. Agente de IA virou consumo à parte, e o dimensionamento soma duas moedas."],
  ["Transferência", "Handoff, escalonamento", "Trazer competência para dentro do atendimento significa passar o cliente adiante: virou a interface de integração."],
  ["Pós-atendimento", "Wrap-up, disposição", "Continua dentro do tempo do contato, porque contato e ocupação do agente são a mesma coisa. Daí o TMA inflado."],
  ["Discagem", "Contato ativo, campanha", "Alcançar alguém só é possível abrindo um canal ao vivo. Como o processo não sabe esperar, a saída é ligar."],
  ["Estatística da chamada", "Relatório operacional", "Contenção do bot e TMA do humano viraram universos separados: não há régua em que pessoa e IA apareçam juntas."]
], { y: 1.88, colW: [2.1, 2.3, 7.83], rowH: 0.42, fontSize: 9 });
card(s, { x: M, y: 5.85, w: CW, h: 0.95, fill: GR, head: "E a segunda camada: a costura entre produtos",
  headColor: WH, bodyColor: LT, size: 10.5, headGap: 0.34,
  body: "O que se chama de “plataforma” costuma ser uma suíte — CCaaS de um fornecedor, WFM de outro, qualidade de um terceiro, discador, bot, pesquisa e CRM de mais alguns. O cliente da pesquisa não é o cliente do discador, e a jornada não existe porque nenhuma peça sozinha a enxerga." });
pageFoot(s, "A herança", 3);
s.addNotes("Frase de fecho, para dizer em voz alta: premissa de arquitetura não se conserta com módulo novo — e costura entre produtos não se conserta com conector.");

/* ---------------- 4 · evidência de mercado ---------------- */
s = pres.addSlide();
kicker(s, "A EVIDÊNCIA DE MERCADO");
title(s, "A adoção já aconteceu. A orquestração, não.");
lead(s, "Pesquisa Infobip em seis países, Brasil incluído: automação e IA agêntica já são maioria — e param onde o atendimento precisaria resolver.", 1.4, null, 12.0, 13);
const ev4 = [
  ["O QUE JÁ FOI ADOTADO", GR, PALE, LT, [["96%", "das empresas já usam ferramentas de automação."], ["53%", "já adotaram inteligência artificial agêntica."]]],
  ["ONDE A ADOÇÃO PARA", CH, "F3C4CB", PK, [["12%", "dos chatbots resolvem o atendimento do início ao fim, sem intervenção humana."], ["42%", "enfrentam gargalos porque os canais digitais operam isolados uns dos outros."]]],
  ["O QUE FALTA — E TEM NOME", TL, "BFE0E0", TLT, [["27%", "das organizações usam uma plataforma de orquestração de comunicação."], ["40%", "das organizações não têm o dado do cliente em armazenamento centralizado."]]]
];
ev4.forEach((c, i) => {
  const x = C3[i];
  s.addShape(pres.ShapeType.roundRect, { x: x, y: 1.95, w: W3, h: 2.72, fill: { color: c[1] }, rectRadius: 0.06 });
  s.addText(c[0], { x: x + 0.24, y: 2.07, w: W3 - 0.4, h: 0.26, fontSize: 9.5, bold: true, color: c[2], fontFace: BF, charSpacing: 1.4, margin: 0 });
  c[4].forEach((n, j) => {
    const y = 2.42 + j * 1.1;
    s.addText(n[0], { x: x + 0.24, y: y, w: 1.4, h: 0.62, fontSize: 32, bold: true, color: WH, fontFace: HF, margin: 0, valign: "middle" });
    s.addText(n[1], { x: x + 1.62, y: y, w: W3 - 1.82, h: 0.9, fontSize: 10.5, color: c[3], fontFace: BF, margin: 0, valign: "top", lineSpacingMultiple: 1.06 });
  });
});
card(s, { x: M, y: 4.82, w: CW, h: 1.42, fill: OW, headColor: CH, size: 10.5, headGap: 0.36,
  head: "A causa que a pesquisa nomeia é a camada de baixo — e é onde o PlugHub foi construído",
  body: "O fator principal apontado é a fragmentação de dados e a desconexão entre sistemas internos. Acoplar IA agêntica a uma plataforma herdada não vira resolução: o agente novo herda os canais isolados, a identidade partida e a medição em dois universos. E o mesmo vale para o agente que vem de fora — o assistente do parceiro e o do consumidor —, que hoje chega a um canal isolado, sem identidade e sem medição." });
s.addText("Fonte: Infobip — Relatório de Maturidade em CX 2026 (release de 13 de maio de 2026), com empresas de seis países, Brasil incluído. Percentuais sobre o total de marcas pesquisadas; os 40% são o complemento dos 60% que declaram armazenamento centralizado do dado do cliente.",
  { x: M, y: 6.34, w: CW, h: 0.5, fontSize: 8.5, italic: true, color: MU, fontFace: BF, margin: 0, valign: "top" });
pageFoot(s, "A evidência de mercado", 4);
s.addNotes("Este slide existe para provar, com dado de fora, o que o slide anterior afirma por dedução: premissa de arquitetura não se conserta com módulo novo. O par 53% adotam / 12% resolvem é o argumento inteiro — adoção alta, resolução baixa.\n\nO número que abre a porta comercial é o 27%. Ele nomeia a categoria: plataforma de orquestração de comunicação. Três em cada quatro empresas não têm uma, e é exatamente a peça que falta entre 'temos IA' e 'a IA resolve'.\n\nSe perguntarem se não é só integração: no mesmo relatório, metade das marcas diz ter as ferramentas totalmente prontas para API — e mesmo assim os canais seguem isolados. Integrar dois produtos não produz sessão única, identidade única nem medição única; é o ponto do slide 3.\n\nDado complementar, se pedirem mais: na mesma pesquisa a IA agêntica é usada em tarefas de baixo valor (coleta de feedback 56%, lembretes 52%, envio de senha 45%) e cai justamente onde retém cliente (onboarding 26%, devolução e reembolso 15%). E 71% dos executivos citam falta de confiança como barreira — que é o argumento do dial do anteparo e do laço supervisionado, mais adiante.\n\nOs 40% são derivados: o relatório diz que 60% têm armazenamento centralizado do dado do cliente. Se pedirem o número direto, é esse. E uma armadilha de tradução que vale conhecer: a matéria brasileira diz \"apenas 27% usam plataforma de orquestração e só metade tem ferramentas preparadas para integração via APIs\", o que sugere metade dos 27%. No release original são dois números independentes sobre a mesma base.\n\nA última frase do cartão é o gancho para a fronteira (slides 15–18): a pesquisa mede a IA de dentro de casa; a próxima onda é o agente de fora.");

/* ---------------- 5 · o que é ---------------- */
s = pres.addSlide();
kicker(s, "O QUE É");
title(s, "Plataforma única de atendimento e processos, feita para agentes — humanos e de IA", null, 25);
s.addText("A unidade de recurso não é o assento humano nem a licença de bot: é o agente, com pool, canais, competências, disponibilidade e score. O roteador não sabe qual dos dois está alocando. O atendimento segue o curso da necessidade — e quem chega pode ser uma pessoa, pelos canais, ou o agente de automação dela, por um canal dedicado, ao mesmo pool.",
  { x: M, y: 1.55, w: 11.9, h: 0.9, fontSize: 13, color: GR2, fontFace: BF, margin: 0, lineSpacingMultiple: 1.12 });
card(s, { x: M, y: 2.6, w: CW, h: 2.18, fill: CH, headColor: WH, bodyColor: PK, size: 10.5,
  head: "A interface de integração é o agente especialista, não a transferência", headGap: 0.38,
  body: "Onde o CCaaS passa o cliente para outra fila quando precisa de uma competência, aqui o orquestrador convoca um especialista para dentro da sessão que já está acontecendo — e o cliente não percebe movimento nenhum. Não é a conferência da telefonia: é o modelo base de todo contato, em qualquer canal, com visibilidade por participante e um segmento medível para cada um.\n\nO especialista serve os dois tipos de orquestrador: o humano, pelo Console, e o de IA, pelo fluxo. Nos incumbentes essa capacidade é construída duas vezes — copilot para a pessoa de um lado, fluxo de bot do outro. Aqui é construída uma vez e rende nos dois.\n\nCom ocupante único, a IA só pode ser etapa antes ou painel ao lado — nunca participante da mesma sala. É por isso que acoplar IA a uma plataforma herdada não produz a mesma coisa." });
card(s, { x: C3[0], y: 4.90, w: W3, h: 1.92, fill: OW, head: "Uma superfície", headColor: CH,
  body: "Receptivo, ativo, automação de processo, pesquisa, qualidade e conformidade no mesmo produto — não módulos licenciados à parte, com configuração, billing e times próprios." });
card(s, { x: C3[1], y: 4.90, w: W3, h: 1.92, fill: TL, head: "Uma unidade de custo", headColor: WH, bodyColor: TLT,
  body: "Licença por concorrência configurada. Humanos e IA na mesma moeda; o ganho de eficiência da IA fica com o cliente, não com o fornecedor." });
card(s, { x: C3[2], y: 4.90, w: 3.89, h: 1.92, fill: GR, head: "Um substrato", headColor: WH, bodyColor: LT,
  body: "Todo contato — humano, de IA, receptivo, ativo, importado de terceiro ou pedido por um agente de automação — produz o mesmo dado de sessão, segmento e evento. Um só pipeline de qualidade e de analytics." });
pageFoot(s, "O que é", 5);
s.addNotes("O cartão vermelho é o slide inteiro: a diferença é de interface, não de recurso. Construir a capacidade uma vez, em vez de duas, é o argumento que o avaliador leva para o financeiro dele.\n\nO terceiro parágrafo é a justificativa de por que isto teve de ser construído do substrato para cima: 'IA e humano trabalhando juntos' é impossível sobre um modelo de sessão de ocupante único. Se o avaliador vier de um incumbente e disser 'temos conferência desde sempre', a resposta é a precisão do primeiro parágrafo — lá a conferência é caminho de exceção na voz; aqui é o modelo base de todo contato, em qualquer canal, com segmento medível por participante.\n\nO argumento mais forte com o comprador é o pedágio, não a arquitetura: com um ocupante por contato, toda competência adicional vira transferência — e transferência perde contexto, reinicia a espera e parte a medição em dois. Isso ele confere nos próprios números de transferência, recontato e TMA.\n\nA última frase do texto de abertura antecipa os slides 15–18. Só plantar: 'o mesmo pool que atende o seu cliente atende o agente dele'. Não detalhar aqui.");

/* ---------------- 6 · como é (figura) ---------------- */
s = pres.addSlide();
kicker(s, "COMO É");
title(s, "O recurso básico é o agente — e o papel de orquestrador é cambiável", null, 25);
lead(s, "Toda sessão tem um orquestrador, que conduz e decide o que delegar. Em torno dele entram e saem especialistas, convocados pelo mesmo roteador. O papel pode ser ocupado por uma pessoa ou por uma instância de fluxo, sem que nada mais na sessão mude.", 1.6, null, 12.0, 12.5);
s.addShape(pres.ShapeType.roundRect, { x: M, y: 2.42, w: CW, h: 3.35, fill: { color: OW }, rectRadius: 0.06 });
s.addText("SESSÃO · SALA DE CONFERÊNCIA", { x: M + 0.3, y: 2.55, w: 6, h: 0.3, fontSize: 10,
  bold: true, color: MU, fontFace: BF, charSpacing: 1.8, margin: 0 });
s.addShape(pres.ShapeType.roundRect, { x: 0.85, y: 3.4, w: 1.5, h: 0.95, fill: { color: GR }, rectRadius: 0.06 });
s.addText("cliente", { x: 0.85, y: 3.52, w: 1.5, h: 0.3, fontSize: 12, bold: true, color: WH, align: "center", fontFace: BF, margin: 0 });
s.addText("canal negociado", { x: 0.85, y: 3.85, w: 1.5, h: 0.3, fontSize: 9.5, color: LT, align: "center", fontFace: BF, margin: 0 });
arrow(s, 2.4, 3.72, 0.5);
s.addShape(pres.ShapeType.roundRect, { x: 2.98, y: 3.05, w: 3.2, h: 1.72, fill: { color: WH },
  line: { color: CH, width: 1.75, dashType: "dash" }, rectRadius: 0.06 });
s.addText("ORQUESTRADOR", { x: 2.98, y: 3.16, w: 3.2, h: 0.28, fontSize: 10, bold: true,
  color: CH, align: "center", fontFace: BF, charSpacing: 1.4, margin: 0 });
s.addShape(pres.ShapeType.roundRect, { x: 3.18, y: 3.5, w: 1.42, h: 1.08, fill: { color: CH }, rectRadius: 0.06 });
s.addText("humano", { x: 3.18, y: 3.7, w: 1.42, h: 0.3, fontSize: 12, bold: true, color: WH, align: "center", fontFace: BF, margin: 0 });
s.addText("Console", { x: 3.18, y: 4.03, w: 1.42, h: 0.28, fontSize: 9.5, color: PK, align: "center", fontFace: BF, margin: 0 });
s.addShape(pres.ShapeType.roundRect, { x: 4.72, y: 3.5, w: 1.28, h: 1.08, fill: { color: TL }, rectRadius: 0.06 });
s.addText("IA", { x: 4.72, y: 3.7, w: 1.28, h: 0.3, fontSize: 12, bold: true, color: WH, align: "center", fontFace: BF, margin: 0 });
s.addText("fluxo de agente", { x: 4.72, y: 4.03, w: 1.28, h: 0.28, fontSize: 9, color: TLT, align: "center", fontFace: BF, margin: 0 });
s.addText("mesmo pool · mesmo roteador · mesma sessão", { x: 2.98, y: 4.85, w: 3.2, h: 0.3,
  fontSize: 9.5, italic: true, color: MU, align: "center", fontFace: BF, margin: 0 });
arrow(s, 6.22, 3.72, 0.5);
s.addText("convoca", { x: 6.08, y: 3.42, w: 0.8, h: 0.25, fontSize: 9, color: MU, align: "center", fontFace: BF, margin: 0 });
s.addShape(pres.ShapeType.roundRect, { x: 6.82, y: 2.95, w: 5.96, h: 2.42, fill: { color: WH },
  line: { color: "DDDEE1", width: 1 }, rectRadius: 0.06 });
s.addText("ESPECIALISTAS (entram e saem)", { x: 7.02, y: 3.06, w: 5, h: 0.28, fontSize: 10,
  bold: true, color: MU, fontFace: BF, charSpacing: 1.2, margin: 0 });
const spec = [
  ["captura mascarada", "o orquestrador não vê o dado", GR2, 7.02, 3.42],
  ["consulta / RAG", "base vetorial", GR2, 9.98, 3.42],
  ["supervisor", "visibilidade privada", GR2, 7.02, 4.42],
  ["avaliador", "online ou pós-sessão", TL, 9.98, 4.42]
];
spec.forEach(sp => {
  s.addShape(pres.ShapeType.roundRect, { x: sp[3], y: sp[4], w: 2.8, h: 0.85, fill: { color: sp[2] }, rectRadius: 0.06 });
  s.addText(sp[0], { x: sp[3], y: sp[4] + 0.12, w: 2.8, h: 0.3, fontSize: 11, bold: true, color: WH, align: "center", fontFace: BF, margin: 0 });
  s.addText(sp[1], { x: sp[3], y: sp[4] + 0.45, w: 2.8, h: 0.3, fontSize: 9, color: LT, align: "center", fontFace: BF, margin: 0 });
});
s.addText([
  { text: "Papéis de participação ", options: { bold: true, color: GR } },
  { text: "primary · specialist · supervisor · evaluator · reviewer — fato do par (participante, sessão).   ", options: { color: GR2 } },
  { text: "Visibilidade por mensagem ", options: { bold: true, color: GR } },
  { text: "todos, só agentes, ou lista explícita.   ", options: { color: GR2 } },
  { text: "O endereço é o pool ", options: { bold: true, color: GR } },
  { text: "— canal, processo, agenda e cartão de agente apontam para um pool, nunca para um fluxo.", options: { color: GR2 } }
], { x: M, y: 5.95, w: CW, h: 0.8, fontSize: 10.5, fontFace: BF, margin: 0, lineSpacingMultiple: 1.15 });
pageFoot(s, "Como é", 6);
s.addNotes("Se perguntarem se isso é multiagente com humano no loop: não. Ali o humano aprova ou intervém; aqui ele é recurso roteável indistinguível pelo motor de alocação.\n\n'O endereço é o pool' prepara a fronteira: o cartão publicado a um agente de fora também aponta para um pool. Qual fluxo e qual versão rodam é detalhe interno do deploy — muda sem que quem chama perceba.");

/* ---------------- 7 · o dial ---------------- */
s = pres.addSlide();
s.background = { color: GR };
kicker(s, "O DIAL DO ANTEPARO", PALE);
title(s, "E ele gira nos dois sentidos", WH);
s.addText("O humano começa no comando do processo e a IA assume pedaços; a cada pedaço que a avaliação prova confiável, o humano recua. Mas o inverso também é regime normal.",
  { x: M, y: 1.5, w: 11.6, h: 0.66, fontSize: 14, color: LT, fontFace: BF, margin: 0, lineSpacingMultiple: 1.12 });
card(s, { x: M, y: 2.35, w: W2, h: 1.65, fill: GR2, head: "Humano orquestra, IA assume", headColor: WH, bodyColor: LT,
  body: "A IA entra como especialista em trechos delimitados: consulta, captura de dado sensível, cálculo, redação. Cada avanço é medido antes do próximo." });
card(s, { x: C2, y: 2.35, w: W2, h: 1.65, fill: TL, head: "IA orquestra, humano assume", headColor: WH, bodyColor: TLT,
  body: "A IA conduz e convoca a pessoa para o trecho que não deve decidir sozinha: exceção, autorização, negociação, empatia. Sem transferência visível ao cliente." });
s.addShape(pres.ShapeType.roundRect, { x: M, y: 4.25, w: CW, h: 2.2, fill: { color: "1F2126" }, rectRadius: 0.06 });
s.addText("E como a IA aprende com o humano", { x: M + 0.32, y: 4.42, w: 11.5, h: 0.35,
  fontSize: 14, bold: true, color: WH, fontFace: BF, margin: 0 });
s.addText([
  { text: "Quando os dois ocupam o mesmo papel na mesma estrutura de sessão, o atendimento humano produz exatamente o registro — turno, ferramenta chamada, decisão, desfecho — de que o agente de IA precisa para ser construído, avaliado e corrigido.\n", options: { color: LT } },
  { text: "Sendo honesto sobre o mecanismo: não é aprendizado automático em produção. ", options: { color: WH, bold: true } },
  { text: "É um laço supervisionado — avaliação com critérios, calibração do avaliador, nova versão publicada e medida contra a anterior — em que cada avanço da IA é explícito, auditável e reversível por versão.", options: { color: LT } }
], { x: M + 0.32, y: 4.85, w: 11.5, h: 1.45, fontSize: 12, fontFace: BF, margin: 0, lineSpacingMultiple: 1.14 });
pageFoot(s, "O dial", 7, true);
s.addNotes("Não prometer aprendizado automático. A honestidade aqui é o que diferencia de quem vende auto-melhoria mágica — e o laço supervisionado é auditável, que é o que o comprador regulado quer.\n\nO dial vale também atrás do endpoint de agentes (slide 15): quando a tarefa vem de um assistente externo, a IA conduz e convoca uma pessoa do mesmo jeito, e o chamador nem percebe.");

/* ---------------- 8 · caso ponta a ponta ---------------- */
s = pres.addSlide();
kicker(s, "UM CASO INTEIRO");
title(s, "Uma portabilidade, do primeiro contato ao registro final");
s.addText("Deliberadamente um processo que não cabe num atendimento: depende de terceiro, atravessa dias e muda de canal — que é a forma da maior parte do que um contact center realmente resolve.",
  { x: M, y: 1.4, w: 11.6, h: 0.35, fontSize: 12.5, color: GR2, fontFace: BF, margin: 0 });
tbl(s, [
  ["Quando", "O que acontece", "O que a plataforma usa"],
  ["Dia 1 · 21h40", "Pede portabilidade pelo WhatsApp. Um agente de IA orquestra, resolve a identidade pelas âncoras do canal e abre o processo.", "Canais · identificação · fluxo negocial"],
  ["Dia 1 · 21h48", "Precisa de documento e dado de conta. O orquestrador convoca um especialista de captura: vê progresso e resultado, não o dado.", "Especialista na sessão · mascaramento"],
  ["Dia 1 · 21h52", "A doadora só responde em horário útil. O processo suspende, o contato encerra porque o cliente saiu e nenhum agente fica bloqueado.", "Suspend · três camadas de ciclo de vida"],
  ["Dia 3 · 06h10", "A resposta chega por webhook fora do horário. O processo retoma sozinho e a informação sai pelo canal do cliente, após o portão de fadiga.", "Webhook como canal · governança de contato"],
  ["Dia 3 · 19h30", "O cliente responde pelo webchat, não pelo WhatsApp. A âncora comprovada resolve a identidade e o processo continua de onde parou.", "Retomada cross-canal · posse de canal"],
  ["Dia 4 · 10h05", "Exceção que a IA não deve decidir: ela convoca uma pessoa. O pós-atendimento dela não entra no tempo do contato.", "Dial invertido · wrap-up destacado"]
], { y: 1.9, colW: [1.45, 7.2, 3.58], rowH: 0.42, fontSize: 9 });
card(s, { x: M, y: 5.8, w: CW, h: 1.0, fill: CH, headColor: WH, bodyColor: PK, size: 10.5,
  head: "Nenhuma ligação foi feita. Nenhum agente ficou bloqueado esperando. Nada foi repetido pelo cliente.", headGap: 0.34,
  body: "É este o caso que substitui boa parte da discagem — não porque discar seja proibido, mas porque a informação chegou sozinha, no canal certo, no instante em que passou a existir." });
pageFoot(s, "Um caso inteiro", 8);
s.addNotes("Se o tempo apertar, este slide sozinho carrega o argumento operacional. Vale perguntar antes: como esse mesmo caso corre hoje na operação deles.\n\nO slide 16 conta este mesmo caso pedido pelo assistente do cliente, por A2A. Vale anunciar: 'vamos ver o mesmo caso sem o cliente digitar nada'.");

/* ---------------- 9 · camadas ---------------- */
s = pres.addSlide();
kicker(s, "ARQUITETURA");
title(s, "Sete camadas de abstração — e o caminho de um contato por elas");
s.addText("Cada camada só conhece o contrato da vizinha. A leitura das camadas é top-down, por nível de abstração; o caminho do contato é outro.",
  { x: M, y: 1.4, w: 11.6, h: 0.32, fontSize: 12.5, color: GR2, fontFace: BF, margin: 0 });
const layers = [
  ["1 · Bus interno", "sinalização, eventos e dados num único barramento, com contrato versionado e stream canônico por sessão", CH, "F3D6DA", "∞", "transversal"],
  ["2 · Fluxos de agente", "workflows declarativos em três níveis: negocial · acesso · entrada e saída", "7A2E38", "EBD5D8", "5", "executa o processo"],
  ["3 · Canais", "normalização de entrada, renderização de saída, degradação por capacidade — pessoas e agentes", GR, LT, "1", "o contato entra"],
  ["4 · Identificação", "âncoras, posse de canal comprovada, pendências em aberto, política de retomada", GR2, LT, "2", "quem é, e o que retoma"],
  ["5 · Roteamento", "competência, canal, disponibilidade, SLA e performance — fila própria, despacho push ou pull", "2C5560", "CFE0E4", "3", "a quem vai"],
  ["6 · Agentes", "runtime único para humano e IA · capacidade compartilhada · ferramentas mediadas e auditadas", TL, CFE, "4", "quem atende"],
  ["7 · Contato", "jornada → sessão → segmento, com métrica e avaliação em cada grão", TLD, CDE, "6", "vira registro"]
];
layers.forEach((L, i) => {
  const y = 1.86 + i * 0.575;
  s.addShape(pres.ShapeType.roundRect, { x: M, y: y, w: 9.05, h: 0.5, fill: { color: L[2] }, rectRadius: 0.05 });
  s.addText([{ text: L[0] + "   ", options: { bold: true, color: WH, fontSize: 12 } },
             { text: L[1], options: { color: L[3], fontSize: 9.5 } }],
    { x: M + 0.22, y: y, w: 8.7, h: 0.5, fontFace: BF, valign: "middle", margin: 0 });
  if (L[4] === "∞") {
    s.addShape(pres.ShapeType.ellipse, { x: 9.85, y: y + 0.08, w: 0.34, h: 0.34, fill: { color: WH }, line: { color: CH, width: 1.5 } });
    s.addText("∞", { x: 9.85, y: y + 0.08, w: 0.34, h: 0.34, fontSize: 12, bold: true, color: CH, align: "center", valign: "middle", fontFace: BF, margin: 0 });
  } else {
    numDot(s, 9.85, y + 0.08, L[4]);
  }
  s.addText(L[5], { x: 10.32, y: y, w: 2.5, h: 0.5, fontSize: 10, bold: true, color: GR, fontFace: BF, valign: "middle", margin: 0 });
});
s.addText("ORDEM DO CONTATO", { x: 9.85, y: 1.52, w: 3, h: 0.26, fontSize: 9.5, bold: true, color: CH, fontFace: BF, charSpacing: 1.4, margin: 0 });
const trail = [["1 · canal", "sessão criada", GR], ["2 · identidade", "e pendências", GR2],
  ["3 · roteador", "fila e score", "2C5560"], ["4 · agente", "humano ou IA", TL],
  ["5 · fluxo", "e sistemas", "7A2E38"], ["6 · registro", "jornada", TLD]];
trail.forEach((t, i) => {
  const x = M + i * 2.07;
  s.addShape(pres.ShapeType.roundRect, { x: x, y: 6.05, w: 1.72, h: 0.7, fill: { color: t[2] }, rectRadius: 0.05 });
  s.addText(t[0], { x: x, y: 6.14, w: 1.72, h: 0.26, fontSize: 10.5, bold: true, color: WH, align: "center", fontFace: BF, margin: 0 });
  s.addText(t[1], { x: x, y: 6.4, w: 1.72, h: 0.26, fontSize: 9, color: LT, align: "center", fontFace: BF, margin: 0 });
  if (i < 5) arrow(s, x + 1.74, 6.28, 0.33);
});
pageFoot(s, "Arquitetura · mapa", 9);
s.addNotes("Ponto do slide: a ordem de abstração e a ordem do contato são diferentes. O bus é transversal, não uma etapa.\n\nO agente de automação entra pela camada 3 como qualquer contato: canal próprio, mesma trilha dali em diante.");

/* ---------------- 10 · bus + fluxos ---------------- */
s = pres.addSlide();
kicker(s, "CAMADAS 1 E 2");
title(s, "Bus interno padronizado e fluxos em três níveis");
card(s, { x: M, y: 1.5, w: W2, h: 2.35, fill: GR, head: "1 · Bus interno", headColor: WH, bodyColor: LT,
  body: "Nenhum componente lê o banco de outro; nenhum integra por chamada interna ad-hoc. Todo evento que cruza fronteira de pacote tem contrato declarado e validado antes da escrita, e consumidores críticos têm retry e fila de mensagens mortas.\n\nFamílias: conversa · ciclo de vida do agente · fluxo e processo · governança e acesso · qualidade e uso · operação." });
card(s, { x: C2, y: 1.5, w: W2, h: 2.35, fill: OW, head: "Stream canônico por sessão", headColor: CH,
  body: "Além do bus, cada sessão tem a sua única fonte de verdade de eventos, com escritor único e validação de esquema na escrita.\n\nMensagem carrega conteúdo mascarado e conteúdo original em campos distintos: só papéis autorizados alcançam o segundo, e todo acesso deixa registro imutável. Mascarar deixa de ser filtro de tela e vira propriedade do dado." });
s.addText("2 · Fluxos de agente — quinze tipos de passo, um interpretador, três níveis de direito de saber",
  { x: M, y: 4.02, w: 11.6, h: 0.35, fontSize: 14, bold: true, color: GR, fontFace: BF, margin: 0 });
card(s, { x: C3[0], y: 4.45, w: W3, h: 1.85, fill: CH, head: "a · Negocial", headColor: WH, bodyColor: PK,
  body: "O processo em si: portabilidade, cobrança, onboarding, crédito. Espera em horário útil, aprovação humana, chamada de sistema. Não sabe por onde o cliente chegou." });
card(s, { x: C3[1], y: 4.45, w: W3, h: 1.85, fill: GR, head: "b · Acesso", headColor: WH, bodyColor: LT,
  body: "Quem é o cliente e o que ele pode retomar. É o nível que o CCaaS tradicional não tem como primeira classe — vira código repetido dentro de cada fluxo." });
card(s, { x: C3[2], y: 4.45, w: 3.89, h: 1.85, fill: TL, head: "c · Entrada e saída", headColor: WH, bodyColor: TLT,
  body: "Formulário de diálogo versionado, renderizado em quatro superfícies — chat, encerramento, página web e Console — a partir do mesmo conteúdo." });
pageFoot(s, "Arquitetura · bus e fluxos", 10);
s.addNotes("Dois perfis de execução com passos mutuamente exclusivos: workflow suspende mas não fala com o cliente; agente fala mas não suspende — e alcança o processo longo delegando a um pool de workflow. A recusa é no DEPLOY, porque o perfil é fato do pool onde o fluxo vai rodar, não do fluxo. O canal de agentes de automação roda no perfil de agente.");

/* ---------------- 11 · canais ---------------- */
s = pres.addSlide();
kicker(s, "CAMADA 3");
title(s, "Abstração de canais — e contato ativo sem discador");
s.addText([
  { text: "webchat · WhatsApp · SMS · e-mail · voz (tronco SIP) · WebRTC · Instagram · Telegram · webhook", options: { color: MU } },
  { text: "   ·   a2a — agentes de automação (canal criado, execução em roadmap)", options: { color: TL } }
], { x: M, y: 1.42, w: 12.2, h: 0.3, fontSize: 11.5, bold: true, fontFace: BF, margin: 0 });
card(s, { x: M, y: 1.85, w: W2, h: 1.5, fill: OW, head: "Canal × meio", headColor: CH, headGap: 0.36, size: 9,
  body: "Canal é filtro duro de roteamento — e por isso também o opt-in: pool que não declara o canal de agentes não atende agente de fora. Meio é fator de score. A lógica específica vive no adaptador: menu de botões vira lista numerada onde botão não existe. Áudio: navegador e telefone entrante na mesma sala, com transcrição e voz de IA, validados com pessoas; chamada sainte ainda não existe." });
card(s, { x: C2, y: 1.85, w: W2, h: 1.5, fill: OW, head: "Ativo pelo mesmo motor", headColor: CH, headGap: 0.36, size: 10,
  body: "Audiência + campanha + entrega, endereçando um pool — nunca um fluxo específico. Disparo por agenda recorrente, distribuição em paralelo, importação com rejeição por linha. Sem stack de outbound paralela." });
card(s, { x: M, y: 3.5, w: CW, h: 1.95, fill: CH, headColor: WH, bodyColor: PK, size: 10.5, headGap: 0.38,
  head: "Contato ativo sem discador — a premissa que estamos recusando",
  body: "O pacing do discador preditivo é função da disponibilidade do agente: ele disca N linhas porque M operadores ficarão livres em T segundos. A prova documental é o teto regulatório de taxa de abandono — a indústria admitindo, por escrito, que o modelo transfere custo para quem atende.\n\nA inversão: mensagem com motivo e assunto, e a decisão de quando conversar volta para quem foi procurado. Sobe a conversão por tentativa, cai o custo por contato útil, e o volume absoluto de conversas pode cair — é a troca, feita de propósito. O diferencial não é o link (callback existe há vinte anos): é o processo já ter estado do outro lado.\n\nO limite: cobrança relevante, prospecção fria e urgência regulatória exigem voz ativa em volume — e aí não temos discador." });
card(s, { x: M, y: 5.6, w: CW, h: 1.25, fill: TL, headColor: WH, bodyColor: TLT, size: 10.5, headGap: 0.38,
  head: "Governança de contato — um portão só, inclusive para pesquisa",
  body: "Antes de qualquer abordagem: opt-out global (salvo transacional) → janela de calendário → fadiga (frequência, quarentena, teto por canal) → supressão de mailing. A decisão sempre nomeia a regra, e o registro é gravado na mesma transação. Por ser genérico, a pesquisa de satisfação passa pelo mesmo portão: convite para avaliar conta como abordagem." });
pageFoot(s, "Arquitetura · canais", 11);
s.addNotes("Amarrar com o slide 8: o caso da portabilidade é a demonstração deste slide. E a pesquisa passando pelo portão de fadiga costuma surpreender — quase ninguém trata pesquisa como contato.\n\nÁudio, se perguntarem: o plano de mídia foi reconstruído sobre um SFU auto-hospedado. Chamada pelo navegador e telefone ENTRANTE pelo tronco SIP entram na mesma sala, com transcrição e voz de IA dentro dela, gravação ligada por política do pool e coleta de dado protegido por teclado com pausa de mídia. Validado com pessoas, em ambiente controlado. NÃO existem ainda: chamada SAINTE e transferência para ramal externo. Por isso o discador continua não existindo — não prometer data.\n\nO canal a2a em verde: existe no registro desde 2026-10-01 e o pool só o expõe com contrato declarado. O adaptador que executa tarefas é roadmap. Não dizer 'temos A2A'.");

/* ---------------- 12 · identidade ---------------- */
s = pres.addSlide();
kicker(s, "CAMADA 4");
title(s, "Identificação do usuário — três classes de evidência");
lead(s, "A identidade é resolvida por âncoras e construída de forma progressiva. O que distingue esta camada não é a resolução, e sim a escala de evidência: cada âncora carrega como foi obtida, e a autorização lê essa classe, nunca só o valor.", 1.42);
const ev = [
  ["Alegada", "o que o cliente diz  ·  declaração no atendimento", "Identificar e personalizar. Nunca autoriza retomar processo em outro canal nem revelar contexto pendente. Identidade dita por um agente de automação pesa o mesmo — nunca mais.", GR2, LT],
  ["Comprovada", "OTP · em operação  ·  login federado · roadmap", "Código com limite de tentativas, guardado só como hash; nenhum agente escreve a marcação. Libera retomada cross-canal. Roadmap: login do tenant (a âncora mais forte) e login social, que prova posse de e-mail — vale o OTP só se o e-mail é verificado e já cadastrado.", CH, PK],
  ["Inerente", "o que ele é  ·  biometria · roadmap", "Biometria de voz ou comportamental como terceira classe, com confiança e validade próprias. A plataforma consome o veredito de um provedor e o registra com proveniência — ela não é o motor biométrico.", "6B7280", "EFEFEF"]
];
ev.forEach((e, i) => {
  const x = M + i * 4.17;
  s.addShape(pres.ShapeType.roundRect, { x: x, y: 2.25, w: 3.9, h: 2.2, fill: { color: e[3] }, rectRadius: 0.06 });
  s.addText(e[0], { x: x + 0.22, y: 2.38, w: 3.46, h: 0.3, fontSize: 14, bold: true, color: WH, fontFace: BF, margin: 0 });
  s.addText(e[1], { x: x + 0.22, y: 2.7, w: 3.46, h: 0.28, fontSize: 9.5, italic: true, color: e[4], fontFace: BF, margin: 0 });
  s.addText(e[2], { x: x + 0.22, y: 3.02, w: 3.46, h: 1.3, fontSize: 10, color: e[4], fontFace: BF, margin: 0, lineSpacingMultiple: 1.08 });
});
card(s, { x: M, y: 4.7, w: W2, h: 1.75, fill: OW, head: "A postura, e por que a biometria não a muda", headColor: CH, headGap: 0.38,
  body: "A plataforma é autoridade sobre posse de canal, não sobre identidade de registro — esta continua no CRM do cliente. A biometria não desloca essa fronteira justamente porque entra como evidência de terceiro registrada com proveniência, e não como veredito próprio da plataforma." });
card(s, { x: C2, y: 4.7, w: W2, h: 1.75, fill: OW, head: "Consequência para LGPD", headColor: CH, headGap: 0.38,
  body: "Dado biométrico é dado pessoal sensível: exige base legal e consentimento específicos, retenção própria e trilha de acesso. Por isso o desenho guarda o veredito e a proveniência, e não o template biométrico — que permanece no provedor. É por conformidade, e não por esforço de código, que a biometria é roadmap." });
pageFoot(s, "Arquitetura · identidade", 12);
s.addNotes("Aqui entra a resposta à pergunta de continuidade do slide 2: retomada cross-canal só é liberada com âncora comprovada, nunca com âncora alegada.\n\nGancho para o slide 17: quando quem fala é um agente de automação, a identidade que ele declara é sempre ALEGADA. Se o fluxo exige prova, a tarefa pede autenticação e devolve um link; a PESSOA prova no navegador, e o agente só vê o resultado — nunca o código. É o mesmo mecanismo de evidência, com outro transporte (roadmap).\n\nLogin federado (roadmap, FED-01): dois mecanismos novos de evidência pelo mesmo escritor único. (1) O login do portal ou app do PRÓPRIO tenant (Keycloak, Auth0, Entra External ID) liga a pessoa ao cadastro pelo (emissor, sub) — é a âncora mais forte do desenho, 0,95 contra 0,70 do telefone por OTP. (2) Login social — Google, Apple, Microsoft; Facebook por último, porque o login da Meta é OIDC só parcial e o e-mail é opcional. CUIDADO com a premissa comum: login social prova posse de um E-MAIL, não de telefone, e não é identidade civil (o Google não devolve CPF). Vale como o OTP só quando o e-mail vem verificado E já é âncora cadastrada do cliente; e-mail sem dono cadastrado dá falhou, nunca cliente novo. Access e refresh token são descartados depois de validar: a plataforma não age na conta da pessoa. O client_secret de cada provedor vai para cofre de segredo por tenant (FED-02). A mesma prova serve ao webchat (botão), ao WhatsApp e SMS (link) e ao A2A (AUTH_REQUIRED) — e é pré-requisito do token do agente do consumidor (slide 17).");

/* ---------------- 13 · roteador + agentes ---------------- */
s = pres.addSlide();
kicker(s, "CAMADAS 5 E 6");
title(s, "Roteamento e agentes — árbitro único, runtime único");
card(s, { x: M, y: 1.5, w: W2, h: 2.15, fill: OW, head: "5 · Regras de alocação e fila", headColor: CH, headGap: 0.36, size: 10,
  body: "Canal e pausa são filtros duros; gateway sem sinal de vida é excluído; empate de score decide pela fila mais curta; performance entra com peso configurável e volume mínimo.\n\nA ordem da fila é chegada, nunca prioridade armazenada — prioridade e envelhecimento são recalculados na leitura, então quem espera mais sobe." });
card(s, { x: C2, y: 1.5, w: W2, h: 2.15, fill: GR, head: "Dois modos de despacho", headColor: WH, bodyColor: LT, headGap: 0.36, size: 10,
  body: "Push: o roteador entrega ao agente. Pull: o item fica numa caixa de trabalho e o agente reivindica, com reivindicação atômica, prazo e devolução à fila.\n\nO pull direcionado reserva o item a um agente específico e transborda para o pool por idade — é o “ramal” sem quebrar a regra de que a unidade endereçável é o pool." });
card(s, { x: M, y: 3.85, w: W2, h: 1.5, fill: TL, head: "Capacidade é do recurso, não do pool", headColor: WH, bodyColor: TLT, headGap: 0.36, size: 10,
  body: "A ocupação é derivada de um semáforo por recurso, não de contador. Uma pessoa com três vagas presente em três pools rende três, não nove. Licença humana e de IA nunca são somadas — inclusive no portão de admissão." });
card(s, { x: C2, y: 3.85, w: W2, h: 1.5, fill: OW, head: "6 · Contrato único de agente", headColor: CH, headGap: 0.36, size: 10,
  body: "Autenticar → pronto → ocupado → concluir, com desfecho e status de tratativa obrigatórios, igual para humano e IA. Nenhum agente acessa sistema de negócio diretamente: a integração passa por servidores de ferramenta no protocolo aberto MCP." });
card(s, { x: M, y: 5.55, w: CW, h: 1.32, fill: CH, headColor: WH, bodyColor: PK, size: 10.5, headGap: 0.36,
  head: "O portão de ferramentas, abaixo de um milissegundo — e onde ele está em vigor",
  body: "Permissão — a lista de ferramentas declarada no fluxo viaja assinada no token de sessão, nunca como argumento da chamada · guarda de injeção por heurística · auditoria nos três desfechos (permitida, negada, bloqueada). A política é da ferramenta, não da chamada. Em vigor no servidor MCP, para a ferramenta de domínio chamada por um agente; os fluxos nativos ainda chamam direto, e a borda única está proposta em ADR." });
pageFoot(s, "Arquitetura · roteamento e agentes", 13);
s.addNotes("A linha de capacidade costuma ser o momento em que arquiteto de CCaaS presta atenção: somar licenças de tipos diferentes é o erro que gera outage com agente ocioso.\n\nO cartão vermelho foi corrigido em 2026-10-01 e é preciso não escorregar: o interceptador dentro do processo do agente nativo NUNCA foi instanciado, e o sidecar de proxy para agente de terceiro saiu do roadmap por decisão de produto. O portão que existe e é testado é o do servidor MCP, do lado do servidor. Os fluxos nativos da plataforma ainda chamam o servidor de ferramenta direto; a proposta (ADR de borda única) é passar tudo por ali e exigir que o servidor de domínio não seja alcançável por fora dele. Se o arquiteto perguntar 'toda chamada passa pelo portão?', a resposta honesta é: a de agente, sim; a de fluxo nativo, ainda não.");

/* ---------------- 14 · grãos ---------------- */
s = pres.addSlide();
kicker(s, "CAMADA 7");
title(s, "Jornada, sessão e segmento — o registro que sobra do caso");
s.addShape(pres.ShapeType.roundRect, { x: M, y: 1.45, w: CW, h: 0.82, fill: { color: TLD }, rectRadius: 0.05 });
s.addText("JORNADA · o processo do cliente", { x: M + 0.25, y: 1.55, w: 6, h: 0.3, fontSize: 12, bold: true, color: WH, fontFace: BF, margin: 0 });
s.addText("componente conexa de sessões — identidade pela raiz canônica, união por alias, resolvida na leitura · SLA por etapa · contexto compartilhado por 30 dias",
  { x: M + 0.25, y: 1.85, w: 11.7, h: 0.32, fontSize: 10, color: CDE, fontFace: BF, margin: 0 });
const sess = [["SESSÃO · contato", "WhatsApp · dia 1", M, 4.0], ["SESSÃO · suspensa", "retomada por webhook · dia 3", 4.72, 3.9],
  ["SESSÃO · contato", "webchat · dia 3", 8.78, 4.0]];
sess.forEach(v => {
  s.addShape(pres.ShapeType.roundRect, { x: v[2], y: 2.4, w: v[3], h: 0.72, fill: { color: GR }, rectRadius: 0.05 });
  s.addText(v[0], { x: v[2] + 0.2, y: 2.48, w: v[3] - 0.4, h: 0.28, fontSize: 11, bold: true, color: WH, fontFace: BF, margin: 0 });
  s.addText(v[1], { x: v[2] + 0.2, y: 2.76, w: v[3] - 0.4, h: 0.26, fontSize: 9.5, color: LT, fontFace: BF, margin: 0 });
});
const segs = [["IA primária", CH, M, 1.9], ["captura mascarada", TL, 2.58, 1.9], ["IA primária", CH, 4.72, 1.9],
  ["humano especialista", GR2, 6.74, 1.88], ["pós-atendimento destacado — fora do TMA", GR2, 8.78, 4.0]];
segs.forEach(v => {
  s.addShape(pres.ShapeType.roundRect, { x: v[2], y: 3.25, w: v[3], h: 0.66, fill: { color: v[1] }, rectRadius: 0.05 });
  s.addText("SEGMENTO", { x: v[2], y: 3.32, w: v[3], h: 0.24, fontSize: 9, bold: true, color: WH, align: "center", fontFace: BF, margin: 0 });
  s.addText(v[0], { x: v[2] + 0.08, y: 3.56, w: v[3] - 0.16, h: 0.26, fontSize: 9, color: "EFEFEF", align: "center", fontFace: BF, margin: 0 });
});
card(s, { x: M, y: 4.1, w: W2, h: 1.35, fill: OW, head: "Segmento — a janela de cada participante", headColor: CH, headGap: 0.34,
  body: "Carimba pool, papel, tipo de agente, segmento pai, sequência, duração, desfecho, canal e versão de deploy. É o grão da avaliação de qualidade.", size: 10.5 });
card(s, { x: C2, y: 4.1, w: W2, h: 1.35, fill: OW, head: "Sessão — a perspectiva do cliente", headColor: CH, headGap: 0.34,
  body: "Ativa, suspensa, encerrada ou abandonada, com domínio fechado de motivos. Suspensa é primeira classe: o processo que espera dias é a mesma sessão.", size: 10.5 });
card(s, { x: M, y: 5.6, w: CW, h: 1.05, fill: CH, bodyColor: PK, size: 11,
  body: "Três camadas que a indústria colapsa em uma:  contato (a perspectiva do cliente, cujas estatísticas congelam quando ele sai) · segmento (a janela de cada participante, cujo recurso do pool é liberado na conclusão) · conferência (a sala, que só termina quando o último participante sai). Separá-las é o que faz o pós-atendimento virar item de fila do próprio agente e o TMA voltar a ser verdade. Validado com atendimento real." });
pageFoot(s, "Arquitetura · grãos de contato", 14);
s.addNotes("Este é o exemplo mais concreto de arquitetura virando operação. Se duvidarem que separação de camadas gera resultado, é este o caso a contar.\n\nGuardar para o slide 16: a tarefa A2A É a sessão, e o contexto A2A É a jornada. É por isso que o canal de agentes não precisa de entidade nova.");

/* ---------------- 15 · fronteira — dois públicos, um pool ---------------- */
s = pres.addSlide();
kicker(s, "FRONTEIRA · AGENTES DE AUTOMAÇÃO");
title(s, "O pool atende quem chega por um canal — e o agente que chega em nome de alguém", null, 25);
lead(s, "O seu agente continua onde está; o que atravessa a fronteira é contrato, em protocolo aberto. Atrás do endpoint deles há um agente; atrás deste pode haver um time — e o contrato não muda.", 1.5, null, 12.0, 13);

s.addText("QUEM CHAMA", { x: M, y: 2.12, w: 3, h: 0.22, fontSize: 9, bold: true, color: MU, fontFace: BF, charSpacing: 1.2, margin: 0 });
s.addText("O ENDEREÇO", { x: 4.3, y: 2.12, w: 3, h: 0.22, fontSize: 9, bold: true, color: CH, fontFace: BF, charSpacing: 1.2, margin: 0 });
s.addText("ATRÁS DO ENDPOINT", { x: 8.35, y: 2.12, w: 3, h: 0.22, fontSize: 9, bold: true, color: MU, fontFace: BF, charSpacing: 1.2, margin: 0 });
const callers = [
  ["o cliente", "webchat · WhatsApp · voz · e-mail", GR, WH, LT, "EM OPERAÇÃO", TLP, "canais", MU],
  ["agente corporativo", "do tenant ou do parceiro: ERP, Copilot Studio, Gemini Enterprise…", TL, WH, TLT, "CANAL CRIADO · EXECUÇÃO ROADMAP", "BFE0E0", "A2A", TL],
  ["assistente pessoal", "do consumidor, com token que a própria pessoa gera após provar", WH, GR, GR2, "DIREÇÃO · ADR PRÓPRIO", MU, "MCP", GR2]
];
callers.forEach((c, i) => {
  const y = 2.42 + i * 0.95;
  s.addShape(pres.ShapeType.roundRect, { x: M, y: y, w: 3.0, h: 0.82, fill: { color: c[2] },
    line: i === 2 ? { color: GR2, width: 1.25, dashType: "dash" } : { color: c[2], width: 0 }, rectRadius: 0.06 });
  s.addText(c[0], { x: M + 0.15, y: y + 0.05, w: 2.7, h: 0.24, fontSize: 11.5, bold: true, color: c[3], fontFace: BF, margin: 0 });
  s.addText(c[1], { x: M + 0.15, y: y + 0.28, w: 2.75, h: 0.3, fontSize: 8.5, color: c[4], fontFace: BF, margin: 0, valign: "top" });
  s.addText(c[5], { x: M + 0.15, y: y + 0.58, w: 2.75, h: 0.18, fontSize: 7.5, bold: true, color: c[6], fontFace: BF, margin: 0 });
  s.addText(c[7], { x: 3.58, y: y + 0.08, w: 0.68, h: 0.22, fontSize: 9.5, bold: true, italic: i === 0, color: c[8], align: "center", fontFace: BF, margin: 0 });
  arrow(s, 3.58, y + 0.3, 0.68, c[8]);
});
s.addShape(pres.ShapeType.roundRect, { x: 4.3, y: 2.42, w: 3.45, h: 2.72, fill: { color: CH }, rectRadius: 0.08 });
s.addText("POOL", { x: 4.3, y: 2.5, w: 3.45, h: 0.42, fontSize: 22, bold: true, color: WH, fontFace: HF, align: "center", margin: 0 });
s.addText("a unidade endereçável", { x: 4.3, y: 2.9, w: 3.45, h: 0.22, fontSize: 9.5, color: PK, fontFace: BF, align: "center", margin: 0 });
s.addText("· cartão do agente = projeção do registro\n· versão = o deploy promovido\n· fila · capacidade · SLA\n· identidade e prova do titular\n· mascaramento por padrão\n· auditoria · avaliação",
  { x: 4.5, y: 3.2, w: 3.1, h: 1.55, fontSize: 10, color: WH, fontFace: BF, margin: 0, valign: "top", lineSpacingMultiple: 1.12 });
s.addText("o mesmo para os três chamadores", { x: 4.3, y: 4.8, w: 3.45, h: 0.24, fontSize: 9, italic: true, color: PK, fontFace: BF, align: "center", margin: 0 });
arrow(s, 7.75, 3.62, 0.55, MU);
const behind = [
  ["uma IA", "fluxo de agente, na versão que o pool tem promovida", TL, TLT, ""],
  ["uma pessoa", "na fila de gente, pelo Console, com capacidade e SLA do pool", GR2, LT, "ROADMAP · DEPOIS DO ADAPTADOR"],
  ["uma conferência", "a IA conduz e convoca a pessoa para o trecho que não deve decidir — o chamador nem percebe", GR, LT, ""]
];
behind.forEach((b, i) => {
  const y = 2.42 + i * 0.95;
  s.addShape(pres.ShapeType.roundRect, { x: 8.35, y: y, w: 4.43, h: 0.82, fill: { color: b[2] }, rectRadius: 0.06 });
  s.addText(b[0], { x: 8.5, y: y + 0.05, w: 4.1, h: 0.24, fontSize: 11.5, bold: true, color: WH, fontFace: BF, margin: 0 });
  s.addText(b[1], { x: 8.5, y: y + 0.29, w: 4.15, h: 0.32, fontSize: 9, color: b[3], fontFace: BF, margin: 0, valign: "top" });
  if (b[4]) s.addText(b[4], { x: 8.5, y: y + 0.6, w: 4.1, h: 0.18, fontSize: 7.5, bold: true, color: TLP, fontFace: BF, margin: 0 });
});
card(s, { x: C3[0], y: 5.32, w: W3, h: 1.58, fill: OW, head: "Por que é canal, e não API", headColor: CH, headGap: 0.32, size: 9,
  body: "Declarar o canal a2a é o opt-in. A métrica não se mistura: o contato de agente aparece com o próprio canal em toda bancada. Traduzir um menu para o que o chamador exibe é lógica de canal, e mora no adaptador." });
card(s, { x: C3[1], y: 5.32, w: W3, h: 1.58, fill: OW, head: "O cartão é projeção do registro", headColor: CH, headGap: 0.32, size: 9,
  body: "Montado do pool, do deploy promovido e do descritor de contrato. A versão é o momento do promote. Público: só pools descobríveis; estendido: os que aquele chamador alcança. Modos de mídia derivados, nunca declarados." });
card(s, { x: C3[2], y: 5.32, w: 3.89, h: 1.58, fill: TL, head: "Atrás pode haver um time", headColor: WH, bodyColor: TLT, headGap: 0.32, size: 9,
  body: "A tarefa percorre fila, capacidade, SLA, auditoria e amostragem de qualidade como qualquer contato. Sem recurso, vale a política do pool: recusar com aviso de quando tentar, ou esperar até o teto publicado." });
pageFoot(s, "Fronteira · dois públicos, um pool", 15);
s.addNotes("A tese em uma frase: a fronteira não se dissolve, ela se padroniza — não pedimos que o agente do cliente venha para dentro, pedimos que ele fale um protocolo aberto. A2A para assistentes corporativos (Copilot Studio, Gemini Enterprise, Agentforce, ServiceNow); MCP para os de consumo (ChatGPT, Claude), que chegam a terceiros por MCP e não por A2A.\n\nPor que vale: o protocolo virou catálogo — A2A v1.0 entrou numa fundação aberta em agosto de 2026 e já aparece em gateways de API e em plataformas concorrentes. Ter A2A não diferencia. O que diferencia é o que o endpoint alcança: fila de pessoas, capacidade, SLA, qualidade medida e auditoria que o chamador não desliga.\n\nHONESTIDADE, importante não escorregar: hoje existe só a fundação — o canal a2a e o descritor de contrato no registro (2026-10-01). Cartão publicado, credencial do chamador, artefato de resultado e adaptador são roadmap. NENHUM agente de fora executa tarefa por esse canal hoje. A linha 'uma pessoa' é posterior ao adaptador.\n\nSe perguntarem 'por que canal e não API': o canal é filtro duro de roteamento, então ele é o opt-in de graça — reusar o webchat faria todo pool de webchat atender agente de fora sem ter escolhido. E a métrica: contato de agente não pode entrar no TMA e no SLA de cliente.");

/* ---------------- 16 · fronteira — o contrato ---------------- */
s = pres.addSlide();
kicker(s, "FRONTEIRA · O CONTRATO");
title(s, "O modelo de tarefa do A2A já existe aqui, com outro nome");
lead(s, "O canal de agentes é representação externa de superfície que já existe, e não um motor novo: roteador, motor de fluxo, admissão, capacidade e modelo de sessão não mudam de comportamento.", 1.4, null, 12.0, 12.5);
tbl(s, [
  ["A2A", "PlugHub", "Consequência"],
  ["Cartão do agente", "pool + deploy promovido + descritor", "Editar o cartão é editar o pool; o contrato versiona junto com o deploy."],
  ["Tarefa", "sessão", "Auditoria, cota, qualidade e cobrança já são da sessão. Nenhuma entidade nova."],
  ["Contexto", "jornada", "Continuar o assunto dias depois é sessão nova na mesma jornada (30 dias, publicado)."],
  ["Pede dado ao chamador", "menu aguardando resposta", "Vira pedido estruturado, interface declarativa ou texto numerado, conforme o chamador."],
  ["Pede autenticação", "fluxo exige evidência ausente", "Link fora de banda: a pessoa prova, o agente vê o resultado."],
  ["Concluída / falhou", "sessão encerrada com desfecho / motivo", "Cancelamento pelo chamador tem motivo próprio e não infla o abandono."],
  ["Enfileirada / atendendo", "na fila / alocada", "Posição de fila vai como mensagem de status; o teto de espera é do pool, publicado."]
], { y: 1.98, colW: [2.35, 3.2, 6.68], rowH: 0.36, fontSize: 9.5 });
card(s, { x: M, y: 5.1, w: W2, h: 1.78, fill: CH, headColor: WH, bodyColor: PK, size: 9.5, headGap: 0.34,
  head: "O novo de verdade é o artefato, não o protocolo",
  body: "O caminho programático de hoje nasceu para chamador interno: devolve só o identificador, e a consulta de estado responde “encerrada” para uma sessão que nunca existiu. Antes do adaptador vêm o resultado terminal gravado e validado contra o esquema de saída do descritor, e “não sei” separado de “terminou”." });
card(s, { x: C2, y: 5.1, w: W2, h: 1.78, fill: OW, headColor: CH, size: 9.5, headGap: 0.34,
  head: "A mesma portabilidade, pedida por um agente",
  body: "O assistente do cliente pede a portabilidade ao cartão do pool → a tarefa nasce como sessão, uma IA conduz → o fluxo exige prova: link, e o cliente prova no navegador → documento e conta vão por link fora de banda, nunca pelo agente → no dia 3 o assistente recebe o artefato no esquema declarado. Se houve exceção, uma pessoa negociou dentro da sessão." });
pageFoot(s, "Fronteira · o contrato", 16);
s.addNotes("Para arquiteto, este é o slide de credibilidade: não estamos construindo um motor de tarefas novo, estamos dando nome externo ao que já existe. Task = sessão, contextId = jornada (root_session_id), messageId = entrada no stream canônico, input-required = menu aguardando ou suspend com token, auth-required = evidência exigida e ausente. Não se cria tabela nem ciclo de vida de task — este projeto já removeu duas entidades criadas para fatos que eram sessão (WorkflowInstance e Journey), e uma terceira repetiria o erro.\n\nO cartão vermelho é o ponto honesto que o arquiteto valoriza: o 'net-new' do arco é o artefato. O caminho webhook de hoje é fire-and-forget e o status responde 'closed' quando a chave não existe — num contrato externo isso é valor plausível escondendo ausência.\n\nEspera, no vocabulário do protocolo: enfileirada = submitted; sem recurso com política de recusa = 503 com Retry-After ou rejected na criação; teto excedido = failed (foi aceita e expirou), nunca rejected; cota esgotada = a tarefa nem nasce. O timeout de espera NÃO é parâmetro de chamada: é do pool, publicado no descritor.\n\nO cartão da direita é o caso do slide 8 contado do lado do agente. Vale perguntar: 'quem, na sua base, já chega por assistente?'");

/* ---------------- 17 · fronteira — quem chama, em nome de quem ---------------- */
s = pres.addSlide();
kicker(s, "FRONTEIRA · IDENTIDADE E GOVERNANÇA");
title(s, "Quem chama, em nome de quem — e com que mandato");
lead(s, "Toda chamada de agente carrega dois fatos que nunca dividem um campo: o principal chamador (que software, com que cota, para quais pools) e o titular (em nome de qual cliente, com que prova).", 1.4, null, 12.0, 12.5);
card(s, { x: M, y: 2.0, w: W2, h: 1.3, fill: TL, head: "Principal de parceiro", headColor: WH, bodyColor: TLT, headGap: 0.34, size: 9.5,
  body: "Emitido pelo admin do tenant para o ERP, o orquestrador ou o parceiro, com a lista de pools que alcança. Sem titular fixo: cliente declarado por ele é sempre alegado, e o fluxo que exige prova a pede à pessoa." });
card(s, { x: C2, y: 2.0, w: W2, h: 1.3, fill: GR, head: "Principal do agente do cliente", headColor: WH, bodyColor: LT, headGap: 0.34, size: 9.5,
  body: "Emitido ao próprio cliente, por autosserviço, depois de uma prova — OTP ou login federado: token pessoal, revogável, de validade curta e cota baixa, preso ao cliente com o nível e a idade da prova." });
const gov = [
  ["Tenant só da credencial", "Nunca do corpo da chamada. Por isso a credencial vem antes do adaptador, sem negociação."],
  ["Mascarado, sem opção", "O agente de fora não vê o original. Entrada mascarada vai por link; anexo do cliente não volta."],
  ["Mandato e confirmação", "Consultar sem contratar. Ação de risco pede confirmação da pessoa pelo mesmo link."],
  ["Cota por principal", "Agente reenvia em loop, de graça. Cota menor que a capacidade do pool, com recusa explícita."],
  ["Contexto só de quem o recebeu", "Contexto alheio é recusado, nunca adotado: ninguém pendura tarefa na conversa de outro."],
  ["Rastro e qualidade", "O principal vai no segmento e na auditoria; a sessão entra na amostragem como qualquer contato."]
];
gov.forEach((g, i) => {
  const col = i % 3, row = Math.floor(i / 3);
  card(s, { x: C3[col], y: 3.42 + row * 1.05, w: col === 2 ? 3.89 : W3, h: 0.95, fill: OW, head: g[0], headColor: CH,
    headSize: 11, headGap: 0.3, body: g[1], size: 9 });
});
card(s, { x: M, y: 5.58, w: CW, h: 1.3, fill: GR, headColor: WH, bodyColor: LT, size: 9.5, headGap: 0.32,
  head: "A segunda face do mesmo pool: MCP, para assistentes de consumo  ·  direção",
  body: "Os assistentes corporativos chamam A2A; os de consumo chegam a terceiros por MCP. O mesmo pool ganha uma segunda representação pública — um servidor MCP remoto — com o mesmo principal, a mesma prova do titular e o mesmo mandato, como webchat e WhatsApp são dois canais da mesma sessão. Não é o servidor MCP interno da plataforma, que exige credencial de serviço e continua interno." });
pageFoot(s, "Fronteira · identidade e governança", 17);
s.addNotes("Este é o slide que separa a proposta de um 'conector A2A': governança sobre quem chama e em nome de quem.\n\nORDEM INEGOCIÁVEL, dizer em voz alta: credencial antes de execução (publicar execução antes do principal seria publicar um disparador anônimo de pools que contatam clientes e promovem deploy), e prova do titular antes do agente do consumidor (um token de consumidor emitido sem prova é cadastro declarado com credencial). Na rota programática interna de hoje o tenant ainda vem do corpo — aceitável enquanto é interna, e uma porta entre tenants no instante em que ficasse pública.\n\nProva do titular: login federado entra como mecanismo de evidência (o IdP do próprio tenant, ou login social que só vale se o e-mail é âncora cadastrada do cliente). Login social não é identidade civil e não cria cadastro. Access e refresh token são descartados depois de validar: a plataforma não age na conta do cliente.\n\nPor que a cota é obrigatória para o agente do consumidor: N consumidores, um pool. Um chamador A2A não é uma pessoa esperando — não abandona por tédio, reenvia em loop. Back-pressure explícito (503 com Retry-After) é requisito.\n\nFace MCP: é direção, com ADR próprio a escrever. Não confundir com o servidor MCP interno.");

/* ---------------- 18 · fronteira — ferramentas e convivência ---------------- */
s = pres.addSlide();
kicker(s, "FRONTEIRA · FERRAMENTAS E CONVIVÊNCIA");
title(s, "A fronteira se padroniza — ela não se dissolve");
lead(s, "Os slides anteriores tratam de agentes que chamam a plataforma. Este trata do resto da fronteira: os sistemas que ela chama, o agente que não vamos hospedar, a plataforma que o cliente já tem e o custo de sair.", 1.4, null, 12.0, 12.5);
card(s, { x: M, y: 2.0, w: W2, h: 1.95, fill: GR, head: "Ferramentas de negócio, por MCP", headColor: WH, bodyColor: LT, headGap: 0.36, size: 9.5,
  body: "A integração com o negócio é mediada por servidores de ferramenta MCP, governados pelo portão do slide 13. A mesma capacidade serve o agente de IA, o fluxo e o Console, com uma trilha só. O servidor MCP interno não é publicado a terceiros — exige credencial de serviço. Ao agente de fora se publica o pool, nunca as ferramentas cruas." });
chip(s, 4.95, 2.13, 1.45, "PORTÃO EM VIGOR", TLP);
card(s, { x: C2, y: 2.0, w: W2, h: 1.95, fill: CH, head: "Por que não hospedamos o seu agente", headColor: WH, bodyColor: PK, headGap: 0.36, size: 9.5,
  body: "Rodar código de terceiro como pool pediria à plataforma que garantisse capacidade, sinal de vida, pausa, contrato de conclusão e auditoria não-optável sobre código que ela não controla — e isso corrói a camada de governança que é o diferencial. Decisão de produto, não de custo. Se o seu agente precisar de uma pessoa, ela está atrás do mesmo endpoint." });
card(s, { x: M, y: 4.08, w: W2, h: 1.38, fill: OW, head: "Derivar para sistemas externos", headColor: CH, headGap: 0.34, size: 9.5,
  body: "O webhook é canal de primeira classe: um processo da plataforma aciona sistemas do cliente e é acionado por eles com o mesmo modelo de sessão, suspendendo e retomando por token. O gatilho aponta para um pool." });
card(s, { x: C2, y: 4.08, w: W2, h: 1.38, fill: TL, head: "Medir antes de migrar", headColor: WH, bodyColor: TLT, headGap: 0.34, size: 9.5,
  body: "O histórico de outro contact center entra por um leitor plugável, com mascaramento na entrada, e reidrata sessão, segmento e transcrição — o investimento já feito vira linha de base, sem migrar uma única chamada." });
card(s, { x: M, y: 5.6, w: CW, h: 1.28, fill: OW, head: "Não aprisionar", headColor: CH, headGap: 0.34, size: 9.5,
  body: "A lógica de atendimento é declarativa e portável, não código proprietário: extraível, versionada e verificável por linha de comando. Modelos trocáveis por configuração, operadoras e provedores como adaptadores, dado exportável. A2A torna os agentes alcançáveis, não extraíveis — a portabilidade é outra garantia, e continua separada." });
pageFoot(s, "Fronteira · ferramentas e convivência", 18);
s.addNotes("CORREÇÃO em relação à versão anterior deste deck: ela dizia 'MCP · EM OPERAÇÃO — a plataforma responde como servidor MCP; o agente do seu time chama, sem ser hospedado aqui'. Isso não é verdade hoje. O transporte do servidor MCP da plataforma exige credencial de serviço desde a CAP-10, é interno, e a borda de ferramenta não tem tráfego de terceiro. O que existe e é testado é o PORTÃO (permissão assinada, guarda de injeção, auditoria em três desfechos). A face pública para o agente de fora é o pool — por A2A, ou pela face MCP do pool (direção).\n\nSe perguntarem 'por que não rodar o meu agente aí dentro': importar pede que a plataforma garanta capacidade, encerramento e auditoria sobre código que ela não controla — corrói a camada de governança que é o diferencial. Padronizar a fronteira custa ao cliente apontar o agente dele para um cartão.\n\nO argumento comercial mais forte é o custo de experimentar: adotar não exige reescrever o orquestrador, migrar canal nem trocar o contact center instalado. E dá para avaliar a operação atual deles, com os nossos formulários, sem migrar uma única chamada.\n\nPor que importa mais em 2026: os protocolos abertos viraram infraestrutura. O que não virou commodity é identidade de quem chama e de quem é atendido, permissão e auditoria que o chamador não desliga, e pessoas e IA na mesma régua — é aí que os projetos agênticos travam na passagem para produção.");

/* ---------------- 19 · módulos figura ---------------- */
s = pres.addSlide();
kicker(s, "MÓDULOS");
title(s, "Como os módulos se relacionam — e o mesmo contato entre eles");
const pipe = [["canais", "chat · WA · voz · webhook · a2a", GR, M, 2.0], ["gateway de canais", "adaptadores · normalização", GR2, 2.68, 2.25],
  ["núcleo de sessão", "stream canônico · masking", GR, 5.16, 2.25], ["roteador", "fila · score · pull", "2C5560", 7.64, 1.95],
  ["Console", "agente humano", CH, 9.82, 1.42], ["motor de fluxo", "agente de IA", TL, 11.47, 1.31]];
pipe.forEach(p => {
  s.addShape(pres.ShapeType.roundRect, { x: p[3], y: 1.5, w: p[4], h: 0.78, fill: { color: p[2] }, rectRadius: 0.05 });
  s.addText(p[0], { x: p[3] + 0.06, y: 1.6, w: p[4] - 0.12, h: 0.28, fontSize: 10.5, bold: true, color: WH, align: "center", fontFace: BF, margin: 0 });
  s.addText(p[1], { x: p[3] + 0.05, y: 1.9, w: p[4] - 0.1, h: 0.3, fontSize: 8.5, color: "E4E4E4", align: "center", fontFace: BF, margin: 0 });
});
numDot(s, 2.32, 1.36, 1, 0.3); numDot(s, 4.82, 1.36, 2, 0.3);
numDot(s, 7.5, 1.36, 3, 0.3); numDot(s, 9.68, 1.36, 4, 0.3);
const supp = [["agenda · campanha · mailing", M, 2.0], ["identidade · âncoras · posse", 2.68, 2.25],
  ["registro · pools · skills · deploys", 5.16, 2.25], ["autenticação · perfis · escopo", 7.64, 1.95]];
supp.forEach(p => {
  s.addShape(pres.ShapeType.roundRect, { x: p[1], y: 2.42, w: p[2], h: 0.52, fill: { color: "8E9299" }, rectRadius: 0.05 });
  s.addText(p[0], { x: p[1] + 0.05, y: 2.42, w: p[2] - 0.1, h: 0.52, fontSize: 9, bold: true, color: WH, align: "center", valign: "middle", fontFace: BF, margin: 0 });
});
s.addShape(pres.ShapeType.roundRect, { x: 9.82, y: 2.42, w: 2.96, h: 0.52, fill: { color: TLD }, rectRadius: 0.05 });
s.addText("ferramentas MCP · gateway de IA · base vetorial", { x: 9.86, y: 2.42, w: 2.88, h: 0.52,
  fontSize: 8.5, bold: true, color: WH, align: "center", valign: "middle", fontFace: BF, margin: 0 });
numDot(s, 12.44, 2.28, 5, 0.3);
s.addShape(pres.ShapeType.roundRect, { x: M, y: 3.08, w: CW, h: 0.52, fill: { color: CH }, rectRadius: 0.05 });
s.addText([{ text: "BUS DE MENSAGENS E EVENTOS     ", options: { bold: true, color: WH, fontSize: 12 } },
  { text: "conversa · agente · fluxo · governança · qualidade · uso · operação", options: { color: "F3D6DA", fontSize: 10 } }],
  { x: M + 0.25, y: 3.08, w: 11.9, h: 0.52, fontFace: BF, valign: "middle", margin: 0 });
const pers = [["Redis", "stream · contexto · fila · capacidade", GR], ["PostgreSQL", "registro · auth · diálogo · identidade", GR],
  ["PostgreSQL vetorial", "base de conhecimento · calibração", GR2], ["ClickHouse", "série temporal · auditoria · sinais", TL],
  ["object storage", "gravações · anexos", "8E9299"]];
pers.forEach((p, i) => {
  const x = M + i * 2.48;
  s.addShape(pres.ShapeType.roundRect, { x: x, y: 3.76, w: 2.28, h: 0.78, fill: { color: p[2] }, rectRadius: 0.05 });
  s.addText(p[0], { x: x, y: 3.86, w: 2.28, h: 0.26, fontSize: 10.5, bold: true, color: WH, align: "center", fontFace: BF, margin: 0 });
  s.addText(p[1], { x: x + 0.06, y: 4.13, w: 2.16, h: 0.34, fontSize: 8, color: "E4E4E4", align: "center", fontFace: BF, margin: 0 });
});
const cons = [["Analytics", "monitor e relatórios"], ["Qualidade", "avaliar · revisar · contestar"], ["Bancada", "comparar lado a lado"],
  ["Survey", "voz do cliente por grão"], ["Auditoria", "acesso e chamadas"], ["WFM", "previsão · simulação"]];
cons.forEach((c, i) => {
  const x = M + i * 2.06;
  const road = i === 5;
  s.addShape(pres.ShapeType.roundRect, { x: x, y: 4.74, w: 1.88, h: 0.72, fill: { color: OW },
    line: { color: road ? "9AA0AA" : "DDDEE1", width: 1, dashType: road ? "dash" : "solid" }, rectRadius: 0.05 });
  s.addText(c[0], { x: x, y: 4.83, w: 1.88, h: 0.26, fontSize: 10, bold: true, color: road ? MU : GR, align: "center", fontFace: BF, margin: 0 });
  s.addText(c[1], { x: x + 0.05, y: 5.08, w: 1.78, h: 0.3, fontSize: 8, color: road ? "9AA0AA" : MU, align: "center", fontFace: BF, margin: 0 });
});
card(s, { x: M, y: 5.6, w: CW, h: 0.56, fill: OW, size: 10.5,
  body: "6 · O contato vira registro — jornada → sessão → segmento, com versão de deploy carimbada: é o que fecha o laço de volta para Qualidade, Bancada e Survey." });
card(s, { x: M, y: 6.26, w: CW, h: 0.56, fill: GR, bodyColor: LT, size: 10.5,
  body: "Fronteira padronizada — agente de automação chega pelo canal a2a ao mesmo roteador e pool · ferramentas pelo portão MCP · histórico de outra plataforma pelo leitor plugável." });
pageFoot(s, "Módulos · relacionamento", 19);
s.addNotes("Mesma numeração do slide 9: o mesmo contato, agora atravessando serviços. Cerca de trinta serviços, com dependências explícitas e sem ciclos.\n\nO canal a2a não cria serviço novo: é um adaptador no gateway de canais, como os outros. MCP é o único protocolo de integração interna; A2A é protocolo de borda, na mesma classe de WhatsApp e webchat.");

/* ---------------- 20 · módulos de operação ---------------- */
s = pres.addSlide();
kicker(s, "MÓDULOS DE OPERAÇÃO");
title(s, "Console, Analytics, Bancada, Qualidade, Survey e WFM");
const mods = [
  ["Console", GR, WH, LT, "Superfície de orquestração, não tela de atendimento. Participantes de IA em tempo real com passo do fluxo, convocar especialista, delegar tarefa com instrução e visibilidade, intervenção de supervisor, caixa de trabalho pull e um renderizador genérico de formulário."],
  ["Analytics", OW, GR, GR2, "Monitoria em tempo real e análise retrospectiva com detalhamento em três níveis — processo, contato e segmento — até o turno. Fila e SLA, disponibilidade e pausas por motivo, picos de ocupação registrados na transição, não por amostragem."],
  ["Bancada de agentes", TL, WH, TLT, "Comparação lado a lado, com humanos e agentes de IA na mesma régua. Dez lentes que atravessam módulos que num CCaaS são produtos diferentes: operação (resolução, escalação, TMA, ocupação, pausas), qualidade (nota, dimensão, deploy), voz do cliente (NPS) e wrap-up. Sobre a mesma seleção, o cross-cut põe os agentes escolhidos lado a lado pelos KPIs da lente. Reunir isso num eixo só é possível pelo substrato único: numa suíte viram quatro produtos e quatro identidades de agente."],
  ["Qualidade", OW, GR, GR2, "Formulários com critérios ponderados, parte calculada de forma determinística e somada à nota. Campanhas com amostragem, avaliadores de IA que pontuam com evidência, revisão humana, contestação por dimensão com histórico imutável e calibração do avaliador."],
  ["Survey", OW, GR, GR2, "CSAT, NPS, CES, PMF e FCR sobre o mesmo conteúdo de formulário, endereçáveis ao grão: segmento, sessão ou jornada. Três veículos — chat, encerramento e página web. Respeita o mesmo controle de fadiga e conhece o desfecho que avalia."],
  ["WFM  ·  roadmap", "8E9299", WH, "F0F0F0", "Previsão de demanda e simulação de dimensionamento. O substrato já existe e é o que costuma faltar: chegada e abandono por intervalo, TMA por segmento livre de pós-atendimento, pausas, picos por transição e capacidade por tipo de licença. Falta o motor de previsão e o de simulação."]
];
mods.forEach((m, i) => {
  const col = i % 3, row = Math.floor(i / 3);
  card(s, { x: M + col * 4.17, y: 1.55 + row * 2.55, w: 3.9, h: 2.35, fill: m[1],
    head: m[0], headColor: m[2], bodyColor: m[3], body: m[4], size: 10, headGap: 0.36 });
});
pageFoot(s, "Módulos · operação", 20);
s.addNotes("O WFM é o único roadmap desta página, e o argumento é o substrato: dimensionar incluindo agentes de IA na mesma conta de concorrência só é possível se o TMA já estiver limpo de wrap-up.\n\nA Bancada e a Qualidade valem também para o pool que atende agentes de automação: o contato de agente aparece com o próprio canal e entra na amostragem como produção.");

/* ---------------- 21 · da métrica à interpretação ---------------- */
s = pres.addSlide();
kicker(s, "ANÁLISE");
chip(s, 1.7, 0.33, 1.05, "ROADMAP", MU);
title(s, "Da métrica à interpretação — a análise que chega pronta");
lead(s, "Relatório entrega número; quem decide precisa de leitura. É o padrão que a qualidade já usa — um agente lê a sessão e entrega nota com evidência, não transcrição crua —, agora sobre os indicadores e as conversas.", 1.4, null, 12.0, 12.5);
card(s, { x: M, y: 2.05, w: CW, h: 0.95, fill: GR, headColor: WH, bodyColor: LT, size: 10, headGap: 0.32,
  head: "O que já existe por baixo  ·  em operação",
  body: "Catálogo de KPIs com definição única (fórmula, população, exclusões, fonte) · versão do deploy carimbada em cada segmento · trajetória real de cada fluxo persistida · desfecho declarado pela IA chegando ao segmento. É o que torna a interpretação conferível." });
const an = [
  ["Perguntas em linguagem natural", "A pergunta vira consulta no mesmo escopo das rotas de relatório de quem pergunta, nunca mais largo. A resposta traz a consulta executada e a população contada."],
  ["Briefing periódico", "Variação dos indicadores por pool, com marcadores de deploy, hipótese de causa e links para as conversas de evidência — sem ninguém pedir. Espera recontato e FCR medidos."],
  ["Diagnóstico de causa", "Agrupa escaladas, transferidas e mal avaliadas e aponta a folha, o step de qual versão, a lacuna de conhecimento ou a ferramenta. Por cima, mineração do caminho real sobre o desenhado."],
  ["Motivos fora da taxonomia", "Agrupa contatos cujo motivo não cai em folha declarada; grupo que cresce vira proposta de folha nova. Transcrição mascarada antes de ir a qualquer modelo."]
];
an.forEach((a, i) => {
  const col = i % 2, row = Math.floor(i / 2);
  card(s, { x: col === 0 ? M : C2, y: 3.12 + row * 1.2, w: W2, h: 1.1, fill: OW, head: a[0], headColor: CH,
    headSize: 12, headGap: 0.32, body: a[1], size: 9.5 });
});
card(s, { x: M, y: 5.55, w: W2, h: 1.32, fill: TL, headColor: WH, bodyColor: TLT, size: 9.5, headGap: 0.32,
  head: "Uma análise que só existe aqui",
  body: "Colaboração na sessão: em contatos conduzidos por IA, quantos chamaram uma pessoa, por quê, por quanto tempo, e se resolveram. Só é medível onde humano e IA dividem a sala." });
card(s, { x: C2, y: 5.55, w: W2, h: 1.32, fill: CH, headColor: WH, bodyColor: PK, size: 9.5, headGap: 0.32,
  head: "Interpretar número errado é pior que entregar o número",
  body: "Por isso as regras vêm antes do agente: uma definição por indicador, população mostrada, escopo de quem pergunta, hipótese sempre com evidência ligada, dado pessoal mascarado antes de qualquer modelo." });
pageFoot(s, "Análise · da métrica à interpretação", 21);
s.addNotes("Slide inteiro em ROADMAP — dizer isso antes de mostrar. Fichas: ANL-01 (perguntas em linguagem natural, aberta), KPI-08 (briefing, bloqueada pelo recontato/FCR, KPI-05), ANL-02 (diagnóstico, bloqueada pela ANL-01), PMN-01 (mineração de processo), KPI-07 (motivos fora da taxonomia), KPI-06 (colaboração).\n\nO argumento para arquiteto não é 'temos IA que lê relatório' — todo mundo terá. É o substrato: a interpretação só é conferível porque cada KPI tem uma definição só, a população é declarada, a versão do deploy está carimbada no atendimento e o desfecho da IA é verdadeiro (pré-condição que ficou de pé em 2026-09-25). Sem isso, a IA interpreta número errado com muita confiança.\n\nO mesmo padrão já roda na qualidade: o agente avaliador pontua com evidência, há revisão humana, contestação e calibração. A análise de KPIs herda esse desenho — inclusive a auditabilidade.\n\nDireção, sem ficha ainda (não prometer): publicar esse 'analista' como um pool, que os agentes de automação do próprio cliente consultariam pelo canal A2A — receberiam a interpretação, com a evidência, em vez do dado bruto. Se o arquiteto se interessar, é conversa de roadmap.");

/* ---------------- 22 · componentes ---------------- */
s = pres.addSlide();
kicker(s, "COMPONENTES");
title(s, "Mensageria e persistência");
s.addText("Cada tecnologia com uma responsabilidade única e declarada. O modelo de estado é explícito e auditável pelo time de arquitetura do cliente — não escondido atrás de serviço gerenciado proprietário.",
  { x: M, y: 1.4, w: 11.6, h: 0.35, fontSize: 12.5, color: GR2, fontFace: BF, margin: 0 });
tbl(s, [
  ["Componente", "Papel", "O que guarda"],
  ["Kafka", "Bus de mensagens e eventos", "Conversa, ciclo de vida de agente, união de jornadas, sinais de cliente, auditoria de ferramentas e de acesso a dado pessoal, alterações de registro e config, avaliação, uso, ocupação e fila, escalação por regra."],
  ["Redis", "Estado de tempo real", "Stream canônico da sessão, contexto de sessão, segmento e jornada, estado do fluxo a cada transição, filas por chegada, semáforo de capacidade, prazos da caixa de trabalho, tokens de retomada."],
  ["PostgreSQL", "Registro durável e relacional", "Pools, skills, deploys e descritores de agente; auth, perfis e grupos; formulários de diálogo; identidade e âncoras; agenda; audiência e campanhas; avaliação e contestação."],
  ["PostgreSQL vetorial", "Busca semântica", "Base de conhecimento para RAG, com extensão de vetores, consumida por servidor de ferramentas próprio. Guarda também as notas de calibração devolvidas ao avaliador."],
  ["ClickHouse", "Analítico e série temporal", "Sessões, segmentos, mensagens e linha do tempo; desempenho diário por agente; picos de ocupação; pausas; auditoria de chamadas e registro imutável de acesso; resultados de avaliação; sinais de cliente."],
  ["Object storage", "Mídia", "Anexos de contato de qualquer canal — conferidos na entrada (classe, tamanho, assinatura, antivírus com quarentena) e expirados por retenção. Gravações de áudio da chamada, quando a política do pool as liga."]
], { y: 1.95, colW: [1.95, 2.5, 7.78], rowH: 0.5, fontSize: 9.5 });
card(s, { x: M, y: 5.6, w: W2, h: 1.25, fill: OW, size: 10,
  body: "Série temporal:  vive no ClickHouse, não no PostgreSQL — volume de evento, consulta colunar por intervalo, dado imutável. Separar os papéis evita relatório pesado competindo com escrita transacional, e é o que torna viável o WFM sobre histórico real." });
card(s, { x: C2, y: 5.6, w: W2, h: 1.25, fill: OW, size: 10,
  body: "Procedência:  todo substrato carrega a origem — produção, importado de terceiro ou reavaliação interna — com filtro padrão para produção em relatório e amostragem. Trazer histórico externo não contamina a medição operacional." });
pageFoot(s, "Componentes", 22);
s.addNotes("Se houver arquiteto de dados na sala, esta é a página em que ele decide se o resto é sério.");

/* ---------------- 23 · limites ---------------- */
s = pres.addSlide();
s.background = { color: GR };
s.addShape(pres.ShapeType.ellipse, { x: 9.9, y: 4.4, w: 6.2, h: 6.2, fill: { color: GR2 } });
kicker(s, "ONDE ESTAMOS", PALE);
title(s, "Limites declarados e próximo passo", WH);
s.addText("Tudo que este material marcou como parcial ou roadmap, num lugar só. Preferimos dizer o que você descobriria de qualquer forma.",
  { x: M, y: 1.4, w: 11.4, h: 0.32, fontSize: 12.5, italic: true, color: LT, fontFace: BF, margin: 0 });
const lim = [
  ["Estágio e certificações", "Pronto em arquitetura e funcionalidade, validado em ambiente controlado e parte em atendimento real. Sem produção em cliente e sem certificação emitida — em andamento, com a evidência técnica já produzida pela arquitetura."],
  ["Canal para agentes (A2A)", "Fundação feita: canal e contrato do pool no registro desde 2026-10-01. Hoje nenhum agente de fora executa tarefa por ele. Ordem: cartão → credencial de parceiro → artefato → adaptador → validação com clientes reais → agente do consumidor → pessoas atrás do canal."],
  ["Ferramentas por MCP", "Portão em vigor no servidor MCP para a ferramenta de domínio, sem tráfego de terceiro ainda; os fluxos nativos não passam por ele (borda única proposta em ADR). A face MCP pública do pool é direção."],
  ["Áudio", "Navegador e telefone entrante (tronco SIP), com transcrição, voz de IA e gravação, validados com pessoas em ambiente controlado. Faltam chamada sainte e transferência para ramal externo."],
  ["Discador preditivo", "Não existe. Antes dele vem a chamada sainte. O que substituímos é a discagem de entrega de informação, não a voz ativa de venda e negociação."],
  ["Isolamento multi-tenant", "Fundação pervasiva, isolamento operacional completo em maturação. É o item nº 1 a validar em prova de conceito."],
  ["Login federado e análise", "Roadmap. Login do tenant e login social como prova de identidade; e a camada de interpretação sobre os KPIs (slide 21), cujo catálogo e base já existem."],
  ["Biometria e WFM", "Roadmap nos dois casos, com o substrato construído: escala de evidência na identidade, e série de fila, TMA e ocupação para o dimensionamento."],
  ["Fusão de cadastros", "Parcial. Resolução de identidade, âncoras progressivas e posse de canal em operação; referência externa a CRM e fusão de clientes são a fase seguinte."]
];
lim.forEach((l, i) => {
  const y = 1.8 + i * 0.47;
  numDot(s, M, y + 0.02, i + 1, 0.26);
  s.addText([{ text: l[0] + "  —  ", options: { bold: true, color: i === 1 ? "BFE0E0" : WH } }, { text: l[1], options: { color: LT } }],
    { x: M + 0.42, y: y, w: 9.4, h: 0.45, fontSize: 9, fontFace: BF, margin: 0, valign: "top", lineSpacingMultiple: 1.02 });
});
s.addShape(pres.ShapeType.roundRect, { x: M, y: 6.06, w: 9.82, h: 0.82, fill: { color: CH }, rectRadius: 0.06 });
s.addText([{ text: "Próximo passo  ", options: { bold: true, color: WH, fontSize: 12 } },
  { text: "uma sessão técnica com o seu time de arquitetura e de operação, sem custo — e, se agentes de automação estão no seu plano, com os seus casos de chamada na mesa. Saímos dela com escopo, pontos a validar em prova de conceito e números, ou com a conclusão honesta de que não é o momento.", options: { color: PK, fontSize: 10 } }],
  { x: M + 0.28, y: 6.06, w: 9.3, h: 0.82, fontFace: BF, valign: "middle", margin: 0, lineSpacingMultiple: 1.04 });
pageFoot(s, "PlugHub · 2026", 23, true);
s.addNotes("Declarar limite cedo qualifica rápido. Quem gosta de construir recebe influência sobre roadmap — inclusive sobre a ordem do canal de agentes — e acesso direto a quem constrói, e isso não se compra de um incumbente.\n\nUm princípio de projeto, para fechar se houver tempo: quando a conveniência de quem contata e a de quem é contatado entram em conflito, a plataforma expõe a escolha em vez de escondê-la. A política é do cliente; a regra explícita, nomeada em cada decisão e com trilha, é da plataforma.\n\nO item 2 é o que este material mais enfatiza e o mais fácil de vender além da conta. Dizer exatamente: a fundação existe, a execução não. Se o prospect quer piloto de agente de automação, o caminho é co-desenho sobre o roadmap, não demonstração.\n\nO item 4 mudou desde agosto: o plano de mídia foi reconstruído e o telefone entrante foi atendido com celular real. Mas se o prospect é operação de voz ATIVA, ele desqualifica hoje — dizer na primeira reunião.");

pres.writeFile({ fileName: "plughub-descritivo-tecnico.pptx" })
  .then(f => console.log("Gerado:", f));
