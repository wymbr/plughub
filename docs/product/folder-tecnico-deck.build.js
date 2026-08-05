/**
 * Gerador do deck técnico do PlugHub (.pptx) — 18 slides.
 *
 * Uso (no WSL, a partir da raiz do repo):
 *   npm i -D pptxgenjs                 # se ainda não estiver instalado
 *   node docs/product/folder-tecnico-deck.build.js
 *
 * Saída: plughub-descritivo-tecnico.pptx no diretório corrente.
 *
 * Público-alvo: avaliador técnico de cliente prospectivo. Reunião de 30–40 min.
 * Conteúdo espelha docs/product/folder-tecnico-plughub.html (15 páginas A4).
 * Notas do apresentador em todos os slides (painel de notas do PowerPoint).
 */
const pptxgen = require("pptxgenjs");

const GR = "2E3138", GR2 = "3E434D", CH = "990011", TL = "046A6A";
const OW = "F2F2F2", MU = "6B7280", WH = "FFFFFF", PALE = "E39AA4", LT = "C9CDD4";
const HF = "Cambria", BF = "Calibri";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";           // 13.33 x 7.5
pres.author = "PlugHub";
pres.title = "PlugHub — descritivo técnico";

const M = 0.55, CW = 13.33 - 2 * 0.55;

function kicker(s, t, c) {
  s.addText(t, { x: M, y: 0.32, w: 9, h: 0.26, fontSize: 10, bold: true,
    color: c || CH, fontFace: BF, charSpacing: 2, margin: 0 });
}
function title(s, t, c, size) {
  s.addText(t, { x: M, y: 0.58, w: CW, h: 0.8, fontSize: size || 29, bold: true,
    color: c || GR, fontFace: HF, margin: 0, valign: "top" });
}
function lead(s, t, y, c, w) {
  s.addText(t, { x: M, y: y, w: w || 11.4, h: 0.62, fontSize: 13.5,
    color: c || GR2, fontFace: BF, margin: 0, valign: "top", lineSpacingMultiple: 1.12 });
}
function card(s, o) {
  s.addShape(pres.ShapeType.roundRect, { x: o.x, y: o.y, w: o.w, h: o.h,
    fill: { color: o.fill || OW }, line: { color: o.fill || OW, width: 0 }, rectRadius: 0.06 });
  let ty = o.y + 0.13;
  if (o.head) {
    s.addText(o.head, { x: o.x + 0.22, y: ty, w: o.w - 0.44, h: 0.34, fontSize: o.headSize || 13,
      bold: true, color: o.headColor || GR, fontFace: BF, margin: 0, valign: "top" });
    ty += (o.headGap || 0.4);
  }
  if (o.body) {
    s.addText(o.body, { x: o.x + 0.22, y: ty, w: o.w - 0.44, h: o.y + o.h - ty - 0.12,
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
function arrow(s, x, y, w) {
  s.addText("→", { x: x, y: y, w: w, h: 0.3, fontSize: 15, color: CH,
    align: "center", valign: "middle", fontFace: BF, margin: 0 });
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
s.addText("Sessão é sala de conferência, não fila de passagem · motor único para receptivo, ativo, processo e qualidade · licença por capacidade simultânea",
  { x: M, y: 4.95, w: 8.6, h: 0.9, fontSize: 13, color: LT, fontFace: BF, margin: 0, lineSpacingMultiple: 1.15 });
s.addText("Descritivo técnico · 2026", { x: M, y: 6.6, w: 6, h: 0.3, fontSize: 10.5, color: "8A9099", fontFace: BF, margin: 0 });
s.addNotes("Abrir dizendo que não é apresentação de funcionalidades: é o argumento de arquitetura, com os limites declarados. 30–40 min, perguntas ao longo.");

/* ---------------- 2 · o teste ---------------- */
s = pres.addSlide();
kicker(s, "A PERGUNTA CÉTICA");
title(s, "Por que mais uma plataforma de atendimento?");
lead(s, "O mercado tem plataformas maduras, com décadas de estrada. O filtro que aplicamos a cada capacidade nossa é um só:", 1.5);
s.addShape(pres.ShapeType.roundRect, { x: M, y: 2.25, w: CW, h: 0.95, fill: { color: GR }, rectRadius: 0.06 });
s.addText("“Por que o incumbente não poderia lançar isto no próximo trimestre?”", {
  x: M + 0.35, y: 2.25, w: CW - 0.7, h: 0.95, fontSize: 21, bold: true, color: WH,
  fontFace: HF, valign: "middle", margin: 0 });
lead(s, "Se a resposta honesta é “poderia”, a capacidade pode ser necessária — mas não justifica um produto novo. Cerca de 70% do nosso esforço de engenharia cai nessa categoria, e não aparece como diferencial em lugar nenhum.", 3.45);
card(s, { x: M, y: 4.45, w: 3.9, h: 1.85, fill: OW, head: "Fundacional", headColor: CH,
  body: "Decisão de arquitetura que o incumbente não replica sem reescrever. É a razão de existir." });
card(s, { x: 4.72, y: 4.45, w: 3.9, h: 1.85, fill: OW, head: "Consequente", headColor: TL,
  body: "Capacidade que só é possível porque a decisão fundacional foi tomada. É a prova de que a fundação é real." });
card(s, { x: 8.89, y: 4.45, w: 3.89, h: 1.85, fill: GR, head: "Custo de entrada", headColor: WH, bodyColor: LT,
  body: "Necessário para o produto ser usável; replicável por qualquer um. Justifica instrumentalmente, nunca em si." });
pageFoot(s, "O teste de justificação", 2);
s.addNotes("Admitir os 70% cedo compra credibilidade com plateia técnica. As seis dores do próximo slide são as que passam no teste.");

/* ---------------- 3 · seis dores ---------------- */
s = pres.addSlide();
kicker(s, "AS DORES");
title(s, "Seis coisas que ninguém resolveu");
lead(s, "Nenhuma é funcionalidade faltando. Todas viraram “é assim mesmo”.", 1.46);
const qs = [
  ["Dado sensível", "Transferir para a URA e perder cliente, pausar a gravação e furar a auditoria, ou o agente ouve o PAN — e a operação inteira entra no escopo PCI."],
  ["TMA com pós-atendimento", "Se o wrap-up entra no TMA, você não sabe quanto tempo o cliente é atendido — e o agente segue bloqueado depois que ele saiu."],
  ["Contatos do mesmo caso", "Protocolo que o cliente precisa guardar, dedução do atendente ou planilha. A resposta honesta é: não relacionam."],
  ["Cobertura de monitoria", "2 a 5%, subjetiva, contestação resolvida em reunião sem trilha — e sem responder se a qualidade caiu após uma mudança específica."],
  ["Pressão sobre o cliente", "Discador, SMS e WhatsApp em sistemas diferentes. Cada um sabe se falou com aquele registro; nenhum sabe quanto aquela pessoa recebeu."],
  ["Pesquisa de satisfação", "Roda em ferramenta separada, com mailing próprio, fora de qualquer controle de fadiga — e sem saber o que aconteceu no atendimento que avalia."]
];
qs.forEach((q, i) => {
  const col = i % 2, row = Math.floor(i / 2);
  card(s, { x: col === 0 ? M : 6.83, y: 1.98 + row * 1.62, w: 5.95, h: 1.45,
    fill: OW, head: q[0], headColor: CH, headSize: 13, headGap: 0.36, body: q[1], size: 10.5 });
});
pageFoot(s, "As dores", 3);
s.addNotes("Perguntar ao vivo qual delas dói mais na operação deles. A sexta costuma ser a que ninguém tinha pensado como contato.");

/* ---------------- 4 · contornados ---------------- */
s = pres.addSlide();
s.background = { color: CH };
kicker(s, "O DIAGNÓSTICO", PALE);
title(s, "Nenhum desses problemas está sem solução.\nTodos estão contornados.", WH, 27);
s.addText("Ninguém desenhou a operação assim porque quis. Fez porque a plataforma embaixo não comporta a solução direta — e a limitação vem de duas fontes ao mesmo tempo.",
  { x: M, y: 2.05, w: 11.6, h: 0.7, fontSize: 14, color: "FBE4E7", fontFace: BF, margin: 0, lineSpacingMultiple: 1.12 });
card(s, { x: M, y: 2.95, w: 5.95, h: 2.6, fill: "7A1220", head: "1 · A premissa de arquitetura", headColor: WH, bodyColor: "F7D8DC",
  body: "A IA modelada como etapa anterior ao atendimento e o humano como anteparo. O contato isolado como unidade de medida. Assento humano e consumo de IA em moedas diferentes.\n\nPremissa de arquitetura não se conserta com módulo novo." });
card(s, { x: 6.83, y: 2.95, w: 5.95, h: 2.6, fill: "7A1220", head: "2 · A costura entre produtos", headColor: WH, bodyColor: "F7D8DC",
  body: "O que se chama de plataforma costuma ser uma suíte: CCaaS de um fornecedor, WFM de outro, QM de um terceiro, discador, bot, pesquisa e CRM de mais alguns — boa parte reunida por aquisição.\n\nO cliente da pesquisa não é o cliente do discador; o agente do WFM não é o agente do QM; a jornada não existe porque nenhuma peça sozinha a enxerga." });
s.addText("A costura é o que a operação mantém todo dia — e o que quebra a cada atualização de qualquer uma das partes.",
  { x: M, y: 5.85, w: 11.6, h: 0.5, fontSize: 15, italic: true, bold: true, color: WH, fontFace: HF, margin: 0 });
pageFoot(s, "O diagnóstico", 4, true);
s.addNotes("Este slide é o eixo. A frase que fica: contornados, não resolvidos. O segundo cartão costuma gerar reconhecimento imediato em quem opera.");

/* ---------------- 5 · o que é ---------------- */
s = pres.addSlide();
kicker(s, "O QUE É");
title(s, "Plataforma única de atendimento e processos, com\norquestração de agentes humanos e de IA", null, 26);
lead(s, "A unidade de recurso não é o assento humano nem a licença de bot: é o agente — humano ou de IA — com pool, canais, competências, disponibilidade e score. O roteador não sabe qual dos dois está alocando.", 1.9);
card(s, { x: M, y: 3.0, w: 3.9, h: 2.15, fill: OW, head: "Uma superfície", headColor: CH,
  body: "Receptivo, ativo, automação de processo, pesquisa, qualidade e conformidade no mesmo produto — não módulos licenciados à parte, com config, billing e times próprios." });
card(s, { x: 4.72, y: 3.0, w: 3.9, h: 2.15, fill: TL, head: "Uma unidade de custo", headColor: WH, bodyColor: "DDEDED",
  body: "Licença por concorrência configurada. Humanos e IA na mesma moeda; o ganho de eficiência da IA fica com o cliente, não com o fornecedor." });
card(s, { x: 8.89, y: 3.0, w: 3.89, h: 2.15, fill: GR, head: "Um substrato", headColor: WH, bodyColor: LT,
  body: "Todo contato — humano, de IA, receptivo, ativo ou importado de terceiro — produz o mesmo dado de sessão, segmento e evento. Um só pipeline de qualidade e analytics." });
s.addShape(pres.ShapeType.roundRect, { x: M, y: 5.45, w: CW, h: 1.1, fill: { color: WH },
  line: { color: CH, width: 1 }, rectRadius: 0.06 });
s.addText([
  { text: "Estágio, dito de propósito.  ", options: { bold: true, color: CH } },
  { text: "Pronta em arquitetura e funcionalidade, validada em ambiente controlado e parte em atendimento real — e ainda não em produção em cliente. Sem certificações emitidas; estão em andamento, e a arquitetura já produz a evidência que elas exigem.", options: { color: GR2 } }
], { x: M + 0.25, y: 5.45, w: CW - 0.5, h: 1.1, fontSize: 11.5, fontFace: BF, valign: "middle", margin: 0, lineSpacingMultiple: 1.1 });
pageFoot(s, "O que é", 5);
s.addNotes("Declarar o estágio aqui, cedo, qualifica rápido. Quem não pode comprar pré-produção diz agora, e não depois de 40 minutos.");

/* ---------------- 6 · como é (figura) ---------------- */
s = pres.addSlide();
kicker(s, "COMO É");
title(s, "O recurso básico é o agente — e o papel de orquestrador é cambiável");
lead(s, "Toda sessão tem um orquestrador, que conduz e decide o que delegar. Em torno dele entram e saem especialistas, convocados pelo mesmo roteador. O papel pode ser ocupado por uma pessoa ou por uma instância de fluxo, sem que nada mais na sessão mude.", 1.42);
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
s.addText("Console", { x: 3.18, y: 4.03, w: 1.42, h: 0.28, fontSize: 9.5, color: "FBE4E7", align: "center", fontFace: BF, margin: 0 });
s.addShape(pres.ShapeType.roundRect, { x: 4.72, y: 3.5, w: 1.28, h: 1.08, fill: { color: TL }, rectRadius: 0.06 });
s.addText("IA", { x: 4.72, y: 3.7, w: 1.28, h: 0.3, fontSize: 12, bold: true, color: WH, align: "center", fontFace: BF, margin: 0 });
s.addText("fluxo de agente", { x: 4.72, y: 4.03, w: 1.28, h: 0.28, fontSize: 9, color: "DDEDED", align: "center", fontFace: BF, margin: 0 });
s.addText("mesmo pool · mesmo roteador · mesma sessão", { x: 2.98, y: 4.85, w: 3.2, h: 0.3,
  fontSize: 9.5, italic: true, color: MU, align: "center", fontFace: BF, margin: 0 });
arrow(s, 6.22, 3.72, 0.5);
s.addText("delega", { x: 6.12, y: 3.42, w: 0.7, h: 0.25, fontSize: 9, color: MU, align: "center", fontFace: BF, margin: 0 });
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
  { text: "primary · specialist · supervisor · evaluator · reviewer — fato do par (participante, sessão).      ", options: { color: GR2 } },
  { text: "Visibilidade por mensagem ", options: { bold: true, color: GR } },
  { text: "todos (inclui o cliente), só agentes, ou lista explícita de participantes.", options: { color: GR2 } }
], { x: M, y: 5.95, w: CW, h: 0.7, fontSize: 11, fontFace: BF, margin: 0, lineSpacingMultiple: 1.15 });
pageFoot(s, "Como é", 6);
s.addNotes("Se perguntarem se isso é multiagente com humano no loop: não. Ali o humano aprova ou intervém; aqui ele é recurso roteável indistinguível pelo motor de alocação.");

/* ---------------- 7 · o dial ---------------- */
s = pres.addSlide();
s.background = { color: GR };
kicker(s, "O DIAL DO ANTEPARO", PALE);
title(s, "E ele gira nos dois sentidos", WH);
s.addText("O humano começa no comando do processo e a IA assume pedaços; a cada pedaço que a avaliação prova confiável, o humano recua. Mas o inverso também é regime normal.",
  { x: M, y: 1.5, w: 11.6, h: 0.66, fontSize: 14, color: LT, fontFace: BF, margin: 0, lineSpacingMultiple: 1.12 });
card(s, { x: M, y: 2.35, w: 5.95, h: 1.65, fill: GR2, head: "Humano orquestra, IA assume", headColor: WH, bodyColor: LT,
  body: "A IA entra como especialista em trechos delimitados: consulta, captura de dado sensível, cálculo, redação. Cada avanço é medido antes do próximo." });
card(s, { x: 6.83, y: 2.35, w: 5.95, h: 1.65, fill: TL, head: "IA orquestra, humano assume", headColor: WH, bodyColor: "DDEDED",
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
s.addNotes("Não prometer aprendizado automático. A honestidade aqui é o que diferencia de quem vende auto-melhoria mágica — e o laço supervisionado é auditável, que é o que o comprador regulado quer.");

/* ---------------- 8 · camadas ---------------- */
s = pres.addSlide();
kicker(s, "ARQUITETURA");
title(s, "Sete camadas de abstração — e o caminho de um contato por elas");
s.addText("Cada camada só conhece o contrato da vizinha. A leitura das camadas é top-down, por nível de abstração; o caminho do contato é outro.",
  { x: M, y: 1.4, w: 11.6, h: 0.32, fontSize: 12.5, color: GR2, fontFace: BF, margin: 0 });
const layers = [
  ["1 · Bus interno", "sinalização, eventos e dados num único barramento, com contrato versionado e stream canônico por sessão", CH, "F3D6DA", "∞", "transversal"],
  ["2 · Fluxos de agente", "workflows declarativos em três níveis: negocial · acesso · entrada e saída", "7A2E38", "EBD5D8", "5", "executa o processo"],
  ["3 · Canais", "normalização de entrada, renderização de saída, degradação por capacidade — receptivo e ativo", GR, LT, "1", "o contato entra"],
  ["4 · Identificação", "âncoras, posse de canal comprovada, pendências em aberto, política de retomada", GR2, LT, "2", "quem é, e o que retoma"],
  ["5 · Roteamento", "competência, canal, disponibilidade, SLA e performance — fila própria, despacho push ou pull", "2C5560", "CFE0E4", "3", "a quem vai"],
  ["6 · Agentes", "runtime único para humano e IA · capacidade compartilhada · ferramentas mediadas e auditadas", TL, "CFE7E7", "4", "quem atende"],
  ["7 · Contato", "jornada → sessão → segmento, com métrica e avaliação em cada grão", "0E4E4E", "CDE3E3", "6", "vira registro"]
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
  ["5 · fluxo", "e sistemas", "7A2E38"], ["6 · registro", "jornada", "0E4E4E"]];
trail.forEach((t, i) => {
  const x = M + i * 2.07;
  s.addShape(pres.ShapeType.roundRect, { x: x, y: 6.05, w: 1.72, h: 0.7, fill: { color: t[2] }, rectRadius: 0.05 });
  s.addText(t[0], { x: x, y: 6.14, w: 1.72, h: 0.26, fontSize: 10.5, bold: true, color: WH, align: "center", fontFace: BF, margin: 0 });
  s.addText(t[1], { x: x, y: 6.4, w: 1.72, h: 0.26, fontSize: 9, color: LT, align: "center", fontFace: BF, margin: 0 });
  if (i < 5) arrow(s, x + 1.74, 6.28, 0.33);
});
pageFoot(s, "Arquitetura · mapa", 8);
s.addNotes("Ponto do slide: a ordem de abstração e a ordem do contato são diferentes. O bus é transversal, não uma etapa.");

/* ---------------- 9 · bus + fluxos ---------------- */
s = pres.addSlide();
kicker(s, "CAMADAS 1 E 2");
title(s, "Bus interno padronizado e fluxos em três níveis");
card(s, { x: M, y: 1.5, w: 5.95, h: 2.35, fill: GR, head: "1 · Bus interno", headColor: WH, bodyColor: LT,
  body: "Nenhum componente lê o banco de outro; nenhum integra por chamada interna ad-hoc. Todo evento que cruza fronteira de pacote tem contrato declarado e validado antes da escrita, e consumidores críticos têm retry e fila de mensagens mortas.\n\nFamílias: conversa · ciclo de vida do agente · fluxo e processo · governança · qualidade e uso · operação." });
card(s, { x: 6.83, y: 1.5, w: 5.95, h: 2.35, fill: OW, head: "Stream canônico por sessão", headColor: CH,
  body: "Além do bus, cada sessão tem a sua única fonte de verdade de eventos, com escritor único e validação de esquema na escrita.\n\nMensagem carrega conteúdo mascarado e conteúdo original em campos distintos: só papéis autorizados alcançam o segundo, e todo acesso deixa registro imutável. Mascarar deixa de ser filtro de tela e vira propriedade do dado." });
s.addText("2 · Fluxos de agente — quinze tipos de passo, um interpretador, três níveis de direito de saber",
  { x: M, y: 4.02, w: 11.6, h: 0.35, fontSize: 14, bold: true, color: GR, fontFace: BF, margin: 0 });
card(s, { x: M, y: 4.45, w: 3.9, h: 1.85, fill: CH, head: "a · Negocial", headColor: WH, bodyColor: "FBE4E7",
  body: "O processo em si: portabilidade, cobrança, onboarding, crédito. Espera em horário útil, aprovação humana, chamada de sistema. Não sabe por onde o cliente chegou." });
card(s, { x: 4.72, y: 4.45, w: 3.9, h: 1.85, fill: GR, head: "b · Acesso", headColor: WH, bodyColor: LT,
  body: "Quem é o cliente e o que ele pode retomar. É o nível que o CCaaS tradicional não tem como primeira classe — vira código repetido dentro de cada fluxo." });
card(s, { x: 8.89, y: 4.45, w: 3.89, h: 1.85, fill: TL, head: "c · Entrada e saída", headColor: WH, bodyColor: "DDEDED",
  body: "Formulário de diálogo versionado, renderizado em quatro superfícies — chat, encerramento, página web e Console — a partir do mesmo conteúdo." });
pageFoot(s, "Arquitetura · bus e fluxos", 9);
s.addNotes("Dois perfis de execução com passos mutuamente exclusivos: workflow suspende mas não fala com o cliente; agente fala mas não suspende. Validado no parse e no motor.");

/* ---------------- 10 · canais + identidade ---------------- */
s = pres.addSlide();
kicker(s, "CAMADAS 3 E 4");
title(s, "Canais e identificação do usuário");
card(s, { x: M, y: 1.5, w: 5.95, h: 2.2, fill: OW, head: "3 · Abstração de canais", headColor: CH,
  body: "webchat · WhatsApp · SMS · e-mail · voz · WebRTC · Instagram · Telegram · webhook\n\nCanal é filtro duro de roteamento; meio (voz, vídeo, mensagem) é fator de score. Toda lógica específica vive no adaptador: um menu de botões vira lista numerada onde botão não existe — decisão do adaptador, nunca do fluxo." });
card(s, { x: 6.83, y: 1.5, w: 5.95, h: 2.2, fill: TL, head: "Governança de contato — inclusive para pesquisa", headColor: WH, bodyColor: "DDEDED",
  body: "Antes de qualquer abordagem, um motor agnóstico decide em ordem: opt-out global → janela de calendário → fadiga (teto por frequência, quarentena, teto por canal) → supressão de mailing. A decisão sempre nomeia a regra.\n\nPor ser genérico, a pesquisa de satisfação passa pelo mesmo portão das campanhas: convite para avaliar conta como abordagem." });
s.addText("4 · Identificação — três classes de evidência, e a autorização lê a classe, nunca só o valor",
  { x: M, y: 3.87, w: 11.6, h: 0.35, fontSize: 14, bold: true, color: GR, fontFace: BF, margin: 0 });
const ev = [
  ["Alegada", "o que o cliente diz  ·  declaração no atendimento", "Identificar e personalizar. Nunca autoriza retomar processo em outro canal nem revelar contexto pendente — a consulta responde “verificação necessária” sem confirmar que há algo.", GR2, LT],
  ["Comprovada", "o canal que ele possui  ·  OTP · em operação", "Desafio e verificação de código, com limite de tentativas e código guardado só como hash. É a única via para esta classe: nenhum agente escreve a marcação. Libera retomada cross-canal.", CH, "FBE4E7"],
  ["Inerente", "o que ele é  ·  biometria · roadmap", "Biometria de voz ou comportamental como terceira classe, com confiança e validade próprias. A plataforma consome o veredito de um provedor e o registra com proveniência — ela não é o motor biométrico.", "6B7280", "EFEFEF"]
];
ev.forEach((e, i) => {
  const x = M + i * 4.17;
  s.addShape(pres.ShapeType.roundRect, { x: x, y: 4.3, w: 3.9, h: 2.0, fill: { color: e[3] }, rectRadius: 0.06 });
  s.addText(e[0], { x: x + 0.22, y: 4.42, w: 3.46, h: 0.3, fontSize: 13, bold: true, color: WH, fontFace: BF, margin: 0 });
  s.addText(e[1], { x: x + 0.22, y: 4.72, w: 3.46, h: 0.28, fontSize: 9.5, italic: true, color: e[4], fontFace: BF, margin: 0 });
  s.addText(e[2], { x: x + 0.22, y: 5.03, w: 3.46, h: 1.15, fontSize: 10, color: e[4], fontFace: BF, margin: 0, lineSpacingMultiple: 1.08 });
});
pageFoot(s, "Arquitetura · canais e identidade", 10);
s.addNotes("A biometria é roadmap por custo de conformidade, não de código: dado biométrico é sensível e exige base legal própria. Guardamos veredito e proveniência, nunca o template biométrico.");

/* ---------------- 11 · roteador ---------------- */
s = pres.addSlide();
kicker(s, "CAMADA 5");
title(s, "Roteador — árbitro único de alocação");
lead(s, "Nenhum componente encaminha conversa sem passar por ele. Trata pessoa e instância de IA pela mesma interface.", 1.42);
card(s, { x: M, y: 2.0, w: 5.95, h: 2.1, fill: OW, head: "Regras de alocação", headColor: CH,
  body: "· Canal é filtro duro: agente que não suporta é proibido\n· Pausa é filtro duro\n· Gateway sem sinal de vida recente é excluído\n· Empate de score decide pela fila mais curta\n· Performance entra com peso configurável e exige volume mínimo para significância" });
card(s, { x: 6.83, y: 2.0, w: 5.95, h: 2.1, fill: OW, head: "Fila com estatística própria", headColor: CH,
  body: "· A ordem é chegada, nunca prioridade armazenada\n· Prioridade e envelhecimento são recalculados na leitura — quem espera mais sobe\n· Violação de SLA avaliada na cabeça da fila; excedido o tempo máximo, encerra com motivo próprio\n· Posição de fila publicada como evento" });
card(s, { x: M, y: 4.28, w: 5.95, h: 2.0, fill: GR, head: "Dois modos de despacho", headColor: WH, bodyColor: LT,
  body: "Push: o roteador entrega. Pull: o item fica numa caixa de trabalho e o agente reivindica, com reivindicação atômica, prazo e devolução à fila.\n\nO pull direcionado reserva o item a um agente específico e transborda para o pool por idade — é o “ramal” sem quebrar a regra de que a unidade endereçável é o pool." });
card(s, { x: 6.83, y: 4.28, w: 5.95, h: 2.0, fill: TL, head: "Capacidade é do recurso, não do pool", headColor: WH, bodyColor: "DDEDED",
  body: "A ocupação é derivada de um semáforo por recurso, não de contador incrementado. Uma pessoa com três vagas presente em três pools rende três, não nove.\n\nLicença humana e de IA são moedas distintas e nunca são somadas — inclusive no portão de admissão de novas sessões." });
pageFoot(s, "Arquitetura · roteamento", 11);
s.addNotes("A linha de capacidade costuma ser o momento em que arquiteto de CCaaS presta atenção: somar licenças de tipos diferentes é o erro que gera outage com agente ocioso.");

/* ---------------- 12 · agentes + guard ---------------- */
s = pres.addSlide();
kicker(s, "CAMADA 6");
title(s, "Agentes — runtime único e ferramentas mediadas");
lead(s, "O contrato de ciclo de vida é o mesmo para humano e IA: autenticar → pronto → ocupado → concluir, com desfecho e status de tratativa obrigatórios. Nenhum agente acessa sistema de negócio diretamente.", 1.42);
s.addTable([
  [{ text: "Tipo de agente", options: { bold: true, color: WH, fill: { color: GR } } },
   { text: "Mecanismo de interceptação", options: { bold: true, color: WH, fill: { color: GR } } },
   { text: "Salto de rede", options: { bold: true, color: WH, fill: { color: GR } } }],
  ["Nativo (SDK da plataforma)", "Interceptador dentro do próprio processo", "nenhum"],
  ["Externo (framework de terceiro)", "Sidecar de proxy local", "apenas loopback"]
], { x: M, y: 2.15, w: CW, colW: [3.9, 5.3, 3.03], fontSize: 11.5, fontFace: BF,
  color: GR2, fill: { color: WH }, border: { type: "solid", color: "DDDEE1", pt: 0.75 },
  rowH: 0.34, valign: "middle", margin: 0.08 });
card(s, { x: M, y: 3.5, w: 7.6, h: 1.55, fill: CH, head: "Em cada chamada, abaixo de um milissegundo", headColor: WH, bodyColor: "FBE4E7",
  body: "Validação de permissão — a lista de ferramentas enviada ao modelo já vem filtrada pelo perfil · guarda de injeção por heurística · registro de auditoria.\n\nA política de auditoria é declarada na ferramenta, não na chamada: o chamador não pode optar por sair." });
card(s, { x: 8.32, y: 3.5, w: 4.46, h: 1.55, fill: OW, head: "Por que isto ainda é diferencial", headColor: CH,
  body: "Protocolo aberto de ferramentas virou infraestrutura. O que não virou commodity é a governança por chamada — e é exatamente onde os projetos agênticos travam ao ir para produção." });
card(s, { x: M, y: 5.25, w: CW, h: 1.1, fill: GR, bodyColor: LT, size: 11,
  body: "Inferência agnóstica de modelo:  o gateway de IA é estritamente sem estado — um turno por chamada. Múltiplas contas por provedor com seleção por menor ocupação, marcação de contas limitadas e queda para provedor alternativo. Perfis de modelo (rápido, equilibrado, potente, avaliação) são configuração por tenant, e a carga de avaliação roda isolada da de atendimento. Sem lock-in de fornecedor de modelo." });
pageFoot(s, "Arquitetura · agentes", 12);
s.addNotes("Não vender MCP nativo como diferencial — queima credibilidade com avaliador informado. O diferencial é o guard obrigatório por chamada, válido também para agente que não escrevemos.");

/* ---------------- 13 · fronteira ---------------- */
s = pres.addSlide();
kicker(s, "FRONTEIRA");
title(s, "Agentes externos, portabilidade e convivência com o que já existe");
lead(s, "Uma plataforma que se propõe a orquestrar agentes não pode presumir que todos os agentes sejam dela. A fronteira é aberta nas duas direções.", 1.42);
card(s, { x: M, y: 2.0, w: 5.95, h: 2.05, fill: GR, head: "Incorporar agentes de terceiros", headColor: WH, bodyColor: LT,
  body: "Um agente de outro framework participa da mesma sessão: entra pelo mesmo roteador, ocupa vaga do mesmo pool, produz segmento e é avaliado pelo mesmo formulário. O que ele não escolhe é a governança — mesmas permissões, mesma guarda, mesma auditoria não-optável." });
card(s, { x: 6.83, y: 2.0, w: 5.95, h: 2.05, fill: OW, head: "Derivar para sistemas externos", headColor: CH,
  body: "Webhook é canal de primeira classe: um processo aciona sistemas do cliente e é acionado por eles com o mesmo modelo de sessão, suspendendo e retomando por token. A integração é sempre mediada — a mesma capacidade fica disponível para o agente de IA, o fluxo e o Console, com uma trilha só." });
card(s, { x: M, y: 4.23, w: 5.95, h: 2.05, fill: TL, head: "Conviver com a plataforma instalada", headColor: WH, bodyColor: "DDEDED",
  body: "O histórico de outro contact center entra no pipeline de qualidade por um leitor plugável, que reidrata sessão, segmento e transcrição como se tivessem nascido aqui. Serve para avaliar a operação atual antes de migrar qualquer tráfego — e o caminho inverso reavalia a própria base com um formulário novo." });
card(s, { x: 6.83, y: 4.23, w: 5.95, h: 2.05, fill: OW, head: "Não aprisionar", headColor: CH,
  body: "A lógica de atendimento é declarativa e portável, não código proprietário: extraível, versionada e verificável por linha de comando quanto a contrato de execução e isolamento de dependências. Modelos trocáveis por configuração, canais como adaptadores, dado exportável.\n\nO custo de sair é uma decisão de projeto, não uma consequência." });
pageFoot(s, "Fronteira aberta", 13);
s.addNotes("O leitor plugável é um bom gancho comercial: dá para avaliar a operação atual deles, com os nossos formulários, sem migrar uma única chamada.");

/* ---------------- 14 · grãos ---------------- */
s = pres.addSlide();
kicker(s, "CAMADA 7");
title(s, "Jornada, sessão e segmento — e as três camadas de ciclo de vida");
s.addShape(pres.ShapeType.roundRect, { x: M, y: 1.45, w: CW, h: 0.82, fill: { color: "0E4E4E" }, rectRadius: 0.05 });
s.addText("JORNADA · o processo do cliente", { x: M + 0.25, y: 1.55, w: 6, h: 0.3, fontSize: 12, bold: true, color: WH, fontFace: BF, margin: 0 });
s.addText("componente conexa de sessões — identidade pela raiz canônica, união por alias, resolvida na leitura · SLA por etapa · contexto compartilhado por 30 dias",
  { x: M + 0.25, y: 1.85, w: 11.7, h: 0.32, fontSize: 10, color: "CDE3E3", fontFace: BF, margin: 0 });
const sess = [["SESSÃO · contato", "WhatsApp · dia 1", M, 4.0], ["SESSÃO · contato", "voz · dia 4", 4.72, 3.9],
  ["SESSÃO · ativo", "retorno da campanha · dia 12", 8.78, 4.0]];
sess.forEach(v => {
  s.addShape(pres.ShapeType.roundRect, { x: v[2], y: 2.4, w: v[3], h: 0.72, fill: { color: GR }, rectRadius: 0.05 });
  s.addText(v[0], { x: v[2] + 0.2, y: 2.48, w: v[3] - 0.4, h: 0.28, fontSize: 11, bold: true, color: WH, fontFace: BF, margin: 0 });
  s.addText(v[1], { x: v[2] + 0.2, y: 2.76, w: v[3] - 0.4, h: 0.26, fontSize: 9.5, color: LT, fontFace: BF, margin: 0 });
});
const segs = [["IA primária", CH, M, 1.9], ["humano", CH, 2.58, 1.9], ["humano", CH, 4.72, 1.9],
  ["especialista IA", TL, 6.74, 1.88], ["pós-atendimento destacado — fora do TMA", GR2, 8.78, 4.0]];
segs.forEach(v => {
  s.addShape(pres.ShapeType.roundRect, { x: v[2], y: 3.25, w: v[3], h: 0.66, fill: { color: v[1] }, rectRadius: 0.05 });
  s.addText("SEGMENTO", { x: v[2], y: 3.32, w: v[3], h: 0.24, fontSize: 9, bold: true, color: WH, align: "center", fontFace: BF, margin: 0 });
  s.addText(v[0], { x: v[2] + 0.08, y: 3.56, w: v[3] - 0.16, h: 0.26, fontSize: 9, color: "EFEFEF", align: "center", fontFace: BF, margin: 0 });
});
card(s, { x: M, y: 4.1, w: 5.95, h: 1.35, fill: OW, head: "Segmento — a janela de cada participante", headColor: CH, headGap: 0.34,
  body: "Carimba pool, papel, tipo de agente, segmento pai, sequência, duração, desfecho, canal e versão de deploy. É o grão da avaliação de qualidade.", size: 10.5 });
card(s, { x: 6.83, y: 4.1, w: 5.95, h: 1.35, fill: OW, head: "Sessão — a perspectiva do cliente", headColor: CH, headGap: 0.34,
  body: "Ativa, suspensa, encerrada ou abandonada, com domínio fechado de motivos de encerramento. Suspensa é primeira classe: o processo que espera dias é a mesma sessão.", size: 10.5 });
card(s, { x: M, y: 5.6, w: CW, h: 1.05, fill: CH, bodyColor: "FBE4E7", size: 11,
  body: "Três camadas que a indústria colapsa em uma:  contato (a perspectiva do cliente, cujas estatísticas congelam quando ele sai) · segmento (a janela de cada participante, cujo recurso do pool é liberado na conclusão) · conferência (a sala, que só termina quando o último participante sai). Separá-las é o que faz o pós-atendimento virar item de fila do próprio agente e o TMA voltar a ser verdade. Validado com atendimento real." });
pageFoot(s, "Arquitetura · grãos de contato", 14);
s.addNotes("Este é o exemplo mais concreto de arquitetura virando operação. Se duvidarem que separação de camadas gera resultado, é este o caso a contar.");

/* ---------------- 15 · módulos figura ---------------- */
s = pres.addSlide();
kicker(s, "MÓDULOS");
title(s, "Como os módulos se relacionam — e o mesmo contato entre eles");
const pipe = [["canais", "voz · chat · WA · webhook", GR, M, 2.0], ["gateway de canais", "adaptadores · normalização", GR2, 2.68, 2.25],
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
s.addShape(pres.ShapeType.roundRect, { x: 9.82, y: 2.42, w: 2.96, h: 0.52, fill: { color: "0E4E4E" }, rectRadius: 0.05 });
s.addText("ferramentas MCP · gateway de IA · base vetorial", { x: 9.86, y: 2.42, w: 2.88, h: 0.52,
  fontSize: 8.5, bold: true, color: WH, align: "center", valign: "middle", fontFace: BF, margin: 0 });
numDot(s, 12.44, 2.28, 5, 0.3);
s.addShape(pres.ShapeType.roundRect, { x: M, y: 3.08, w: CW, h: 0.52, fill: { color: CH }, rectRadius: 0.05 });
s.addText([{ text: "BUS DE MENSAGENS E EVENTOS     ", options: { bold: true, color: WH, fontSize: 12 } },
  { text: "conversa · agente · fluxo · governança · qualidade · uso · operação", options: { color: "F3D6DA", fontSize: 10 } }],
  { x: M + 0.25, y: 3.08, w: 11.9, h: 0.52, fontFace: BF, valign: "middle", margin: 0 });
const pers = [["Redis", "stream · contexto · fila · capacidade", GR], ["PostgreSQL", "registro · auth · workflow · identidade", GR],
  ["PostgreSQL vetorial", "base de conhecimento · calibração", GR2], ["ClickHouse", "série temporal · auditoria · sinais", TL],
  ["object storage", "gravações · anexos", "8E9299"]];
pers.forEach((p, i) => {
  const x = M + i * 2.48;
  s.addShape(pres.ShapeType.roundRect, { x: x, y: 3.76, w: 2.28, h: 0.78, fill: { color: p[2] }, rectRadius: 0.05 });
  s.addText(p[0], { x: x, y: 3.86, w: 2.28, h: 0.26, fontSize: 10.5, bold: true, color: WH, align: "center", fontFace: BF, margin: 0 });
  s.addText(p[1], { x: x + 0.06, y: 4.13, w: 2.16, h: 0.34, fontSize: 8, color: "E4E4E4", align: "center", fontFace: BF, margin: 0 });
});
const cons = [["Analytics", "monitor e relatórios"], ["Qualidade", "avaliar · revisar · contestar"], ["Bancada", "comparar por versão"],
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
  body: "Fronteira aberta — agente externo entra pelo sidecar de proxy com a mesma governança · histórico de outra plataforma entra pelo leitor plugável e reidrata sessão e transcrição." });
pageFoot(s, "Módulos · relacionamento", 15);
s.addNotes("Mesma numeração do slide de camadas: o mesmo contato, agora atravessando serviços. Cerca de trinta serviços, sem dependência circular.");

/* ---------------- 16 · módulos de operação ---------------- */
s = pres.addSlide();
kicker(s, "MÓDULOS DE OPERAÇÃO");
title(s, "Console, Analytics, Bancada, Qualidade, Survey e WFM");
const mods = [
  ["Console", GR, WH, LT, "Superfície de orquestração, não tela de atendimento. Participantes de IA em tempo real com passo do fluxo, convocar especialista, delegar tarefa com instrução e visibilidade, intervenção de supervisor, caixa de trabalho pull e um renderizador genérico de formulário."],
  ["Analytics", OW, GR, GR2, "Monitoria em tempo real e análise retrospectiva com detalhamento em três níveis — processo, contato e segmento — até o turno. Fila e SLA, disponibilidade e pausas por motivo, picos de ocupação registrados na transição, não por amostragem."],
  ["Bancada de agentes", TL, WH, "DDEDED", "Comparação de agentes lado a lado com duas lentes: diária com marcadores de deploy, e por versão publicada. Responde “a qualidade caiu depois que mexemos neste agente?” — possível porque a versão foi carimbada no segmento no momento do atendimento."],
  ["Qualidade", OW, GR, GR2, "Formulários com critérios ponderados, parte calculada de forma determinística e somada à nota. Campanhas com amostragem, avaliadores de IA que pontuam com evidência, revisão humana, contestação por dimensão com histórico imutável e calibração do avaliador."],
  ["Survey", OW, GR, GR2, "CSAT, NPS, CES, PMF e FCR sobre o mesmo conteúdo de formulário, endereçáveis ao grão: segmento, sessão ou jornada. Três veículos — chat, encerramento e página web. Respeita o mesmo controle de fadiga e conhece o desfecho que avalia."],
  ["WFM  ·  roadmap", "8E9299", WH, "F0F0F0", "Previsão de demanda e simulação de dimensionamento. O substrato já existe e é o que costuma faltar: chegada e abandono por intervalo, TMA por segmento livre de pós-atendimento, pausas, picos por transição e capacidade por tipo de licença. Falta o motor de previsão e o de simulação."]
];
mods.forEach((m, i) => {
  const col = i % 3, row = Math.floor(i / 3);
  card(s, { x: M + col * 4.17, y: 1.55 + row * 2.55, w: 3.9, h: 2.35, fill: m[1],
    head: m[0], headColor: m[2], bodyColor: m[3], body: m[4], size: 10, headGap: 0.36 });
});
pageFoot(s, "Módulos · operação", 16);
s.addNotes("O WFM é o único roadmap desta página, e o argumento é o substrato: dimensionamento com agentes de IA na mesma conta de concorrência só é possível se o TMA já estiver limpo de wrap-up.");

/* ---------------- 17 · componentes ---------------- */
s = pres.addSlide();
kicker(s, "COMPONENTES");
title(s, "Mensageria e persistência");
lead(s, "Cada tecnologia com uma responsabilidade única e declarada. O modelo de estado é explícito e auditável pelo time de arquitetura do cliente — não escondido atrás de serviço gerenciado proprietário.", 1.42);
s.addTable([
  [{ text: "Componente", options: { bold: true, color: WH, fill: { color: GR } } },
   { text: "Papel", options: { bold: true, color: WH, fill: { color: GR } } },
   { text: "O que guarda", options: { bold: true, color: WH, fill: { color: GR } } }],
  ["Kafka", "Bus de mensagens e eventos", "Conversa, ciclo de vida de agente, workflow e coleta, união de jornadas, sinais de cliente, auditoria de ferramentas, alterações de registro e config, avaliação, uso, ocupação e fila."],
  ["Redis", "Estado de tempo real", "Stream canônico da sessão, contexto de sessão, segmento e jornada, estado do fluxo a cada transição, filas por chegada, semáforo de capacidade, prazos da caixa de trabalho, tokens de retomada."],
  ["PostgreSQL", "Registro durável e relacional", "Agentes, pools, skills e deploys; auth, perfis e grupos; workflow; formulários de diálogo; identidade e âncoras; agenda; audiência e campanhas; avaliação e contestação."],
  ["PostgreSQL vetorial", "Busca semântica", "Base de conhecimento para RAG, com extensão de vetores, consumida por servidor de ferramentas próprio. Guarda também as notas de calibração devolvidas ao avaliador."],
  ["ClickHouse", "Analítico e série temporal", "Sessões, segmentos, mensagens e linha do tempo; desempenho diário por agente; picos de ocupação; pausas; auditoria de chamadas e registro imutável de acesso; resultados de avaliação; sinais de cliente."],
  ["Object storage", "Mídia", "Gravações de voz e WebRTC, anexos de webchat com expiração em duas fases."]
], { x: M, y: 2.12, w: CW, colW: [1.95, 2.5, 7.78], fontSize: 9.5, fontFace: BF, color: GR2,
  fill: { color: WH }, border: { type: "solid", color: "DDDEE1", pt: 0.75 }, rowH: 0.5,
  valign: "middle", margin: 0.07 });
card(s, { x: M, y: 5.75, w: 5.95, h: 1.05, fill: OW, size: 10,
  body: "Série temporal:  vive no ClickHouse, não no PostgreSQL — volume de evento, consulta colunar por intervalo, dado imutável. Separar os papéis evita relatório pesado competindo com escrita transacional, e é o que torna viável o WFM sobre histórico real." });
card(s, { x: 6.83, y: 5.75, w: 5.95, h: 1.05, fill: OW, size: 10,
  body: "Procedência:  todo substrato carrega a origem — produção, importado de terceiro ou reavaliação interna — com filtro padrão para produção em relatório e amostragem. Trazer histórico externo não contamina a medição operacional." });
pageFoot(s, "Componentes", 17);
s.addNotes("Se houver arquiteto de dados na sala, esta é a página em que ele decide se o resto é sério.");

/* ---------------- 18 · fechamento ---------------- */
s = pres.addSlide();
s.background = { color: GR };
s.addShape(pres.ShapeType.ellipse, { x: 9.6, y: 3.6, w: 6.6, h: 6.6, fill: { color: GR2 } });
kicker(s, "ONDE ESTAMOS", PALE);
title(s, "Limites declarados e próximo passo", WH);
s.addText("Preferimos dizer aqui o que você descobriria de qualquer forma.", { x: M, y: 1.48, w: 9, h: 0.35,
  fontSize: 13.5, italic: true, color: LT, fontFace: BF, margin: 0 });
const lim = [
  ["Estágio", "Pronta em arquitetura e funcionalidade, validada em ambiente controlado e parte em atendimento real. Sem deployment enterprise e sem certificações emitidas — em andamento."],
  ["Discador preditivo", "Não existe. Modos power, progressivo e preview, guarda de taxa de abandono e listas restritivas são roadmap. Para voz ativa em massa, os incumbentes entregam hoje e nós não."],
  ["Multi-tenant", "A fundação de isolamento é pervasiva; o isolamento operacional completo está em maturação. É o item número 1 a validar em prova de conceito."],
  ["Biometria e WFM", "Roadmap declarado, com o substrato de dados já construído nos dois casos."]
];
lim.forEach((l, i) => {
  const y = 2.05 + i * 0.92;
  numDot(s, M, y + 0.02, i + 1, 0.32);
  s.addText([{ text: l[0] + "  —  ", options: { bold: true, color: WH } }, { text: l[1], options: { color: LT } }],
    { x: M + 0.48, y: y, w: 8.5, h: 0.85, fontSize: 11.5, fontFace: BF, margin: 0, valign: "top", lineSpacingMultiple: 1.1 });
});
s.addShape(pres.ShapeType.roundRect, { x: M, y: 5.85, w: 8.9, h: 1.0, fill: { color: CH }, rectRadius: 0.06 });
s.addText([{ text: "Próximo passo  ", options: { bold: true, color: WH, fontSize: 13 } },
  { text: "uma sessão técnica com o seu time de arquitetura e de operação, sem custo. Saímos dela com um escopo possível, a lista de pontos a validar em prova de conceito e números — ou com a conclusão honesta de que não é o momento.", options: { color: "FBE4E7", fontSize: 11.5 } }],
  { x: M + 0.3, y: 5.85, w: 8.3, h: 1.0, fontFace: BF, valign: "middle", margin: 0, lineSpacingMultiple: 1.1 });
pageFoot(s, "PlugHub · 2026", 18, true);
s.addNotes("Declarar limite cedo qualifica rápido. Quem gosta de construir recebe influência sobre roadmap e acesso direto a quem constrói — e isso não se compra de um incumbente.");

pres.writeFile({ fileName: "plughub-descritivo-tecnico.pptx" })
  .then(f => console.log("Gerado:", f));
