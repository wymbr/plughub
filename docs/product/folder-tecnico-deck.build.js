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
 * Conteúdo espelha docs/product/folder-tecnico-plughub.html (18 páginas A4).
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

function kicker(s, t, c, y) {
  s.addText(t, { x: M, y: y === undefined ? 0.32 : y, w: 9, h: 0.26, fontSize: 10, bold: true,
    color: c || CH, fontFace: BF, charSpacing: 2, margin: 0 });
}
function title(s, t, c, size, y) {
  s.addText(t, { x: M, y: y === undefined ? 0.58 : y, w: CW, h: 0.8, fontSize: size || 29, bold: true,
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
function arrow(s, x, y, w) {
  s.addText("→", { x: x, y: y, w: w, h: 0.3, fontSize: 15, color: CH,
    align: "center", valign: "middle", fontFace: BF, margin: 0 });
}
function tbl(s, rows, opt) {
  const head = rows[0].map(t => ({ text: t, options: { bold: true, color: WH, fill: { color: GR } } }));
  s.addTable([head].concat(rows.slice(1)), Object.assign({
    x: M, w: CW, fontSize: 10, fontFace: BF, color: GR2, fill: { color: WH },
    border: { type: "solid", color: "DDDEE1", pt: 0.75 }, valign: "middle", margin: 0.08
  }, opt));
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
s.addText("Sessão é sala de conferência, não fila de passagem · a competência entra na conversa em vez de o cliente ser passado adiante · licença por capacidade simultânea",
  { x: M, y: 4.95, w: 8.6, h: 0.9, fontSize: 13, color: LT, fontFace: BF, margin: 0, lineSpacingMultiple: 1.15 });
s.addText("Descritivo técnico · 2026", { x: M, y: 6.6, w: 6, h: 0.3, fontSize: 10.5, color: "8A9099", fontFace: BF, margin: 0 });
s.addNotes("Abrir dizendo que não é apresentação de funcionalidades: é o argumento de arquitetura, com os limites declarados. 30–40 min, perguntas ao longo.");

/* ---------------- 2 · a herança ---------------- */
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
pageFoot(s, "A herança", 2);
s.addNotes("Frase de fecho, para dizer em voz alta: premissa de arquitetura não se conserta com módulo novo — e costura entre produtos não se conserta com conector.");

/* ---------------- 3 · as sete perguntas ---------------- */
s = pres.addSlide();
kicker(s, "AS PERGUNTAS");
title(s, "Sete perguntas sobre o curso natural de um atendimento");
s.addText("Nenhuma é sobre funcionalidade faltando. Todas descrevem algo que a operação aprendeu a contornar — e o contorno virou processo, treinamento e indicador.",
  { x: M, y: 1.4, w: 11.6, h: 0.35, fontSize: 12.5, color: GR2, fontFace: BF, margin: 0 });
const qs = [
  ["“Trazer um especialista exige transferir o cliente?”", "Não é falta de roteamento por competência: é que ela mora em outra fila, e alcançá-la significa passar o cliente adiante."],
  ["“A necessidade do cliente sempre termina em um atendimento?”", "Quatro conversas sobre o mesmo caso viram quatro registros e três falhas de FCR."],
  ["“Se a resposta não existe agora, como se dá continuidade?”", "Protocolo, planilha ou campanha montada depois. O caso fica parado esperando alguém lembrar dele."],
  ["“Por que se disca para milhares de pessoas — e quem soma a pressão sobre cada uma?”", "Boa parte dessas ligações só entrega uma informação. E ninguém soma entre discador, SMS e WhatsApp."],
  ["“Quando quem atende é uma IA, o que ela consultou e com que autorização?”", "A pergunta sobre quem vê o dado é antiga. Esta quase nunca tem resposta."],
  ["“Como você compara uma pessoa e uma IA que fazem o mesmo trabalho?”", "Contenção do bot de um lado; TMA, monitoria e pesquisa do outro. Universos separados, em sistemas diferentes."],
  ["“Quanto vai custar colocar mais IA na operação no mês que vem?”", "Cobrança por ação, conversa, resolução ou token: fatura imprevisível e incentivo invertido."]
];
qs.forEach((q, i) => {
  const col = i % 2, row = Math.floor(i / 2);
  card(s, { x: col === 0 ? M : 6.83, y: 1.9 + row * 1.28, w: 5.95, h: 1.14,
    fill: OW, head: q[0], headColor: CH, headSize: 11.5, headItalic: true, headGap: 0.46,
    body: q[1], size: 9.5 });
});
card(s, { x: 6.83, y: 5.74, w: 5.95, h: 1.14, fill: CH, headColor: WH, bodyColor: "FBE4E7",
  head: "A pergunta que vira o documento", headSize: 11.5, headGap: 0.4, size: 10,
  body: "Como seria o atendimento se ele seguisse o curso natural da necessidade do cliente, e não o que a ferramenta permite?" });
pageFoot(s, "As perguntas", 3);
s.addNotes("Perguntar ao vivo qual delas dói mais. As duas últimas costumam ser as que ninguém tinha formulado assim.");

/* ---------------- 4 · o que é ---------------- */
s = pres.addSlide();
kicker(s, "O QUE É");
title(s, "Plataforma única de atendimento e processos, feita para agentes — humanos e de IA", null, 25);
s.addText("A unidade de recurso não é o assento humano nem a licença de bot: é o agente, com pool, canais, competências, disponibilidade e score. O roteador não sabe qual dos dois está alocando. E o atendimento é feito para seguir o curso da necessidade: sem transferir para capturar um dado, sem encerrar porque a resposta não veio hoje, sem recomeçar porque o canal mudou.",
  { x: M, y: 1.55, w: 11.9, h: 0.9, fontSize: 13, color: GR2, fontFace: BF, margin: 0, lineSpacingMultiple: 1.12 });
card(s, { x: M, y: 2.6, w: CW, h: 1.85, fill: CH, headColor: WH, bodyColor: "FBE4E7", size: 11.5,
  head: "A interface de integração é o agente especialista, não a transferência", headGap: 0.42,
  body: "Onde o CCaaS passa o cliente para outra fila quando precisa de uma competência, aqui o orquestrador convoca um especialista para dentro da sessão que já está acontecendo — e o cliente não percebe movimento nenhum.\n\nO especialista serve os dois tipos de orquestrador: o humano, pelo Console, e o de IA, pelo fluxo. Nos incumbentes essa capacidade é construída duas vezes — copilot para a pessoa de um lado, fluxo de bot do outro, dois artefatos e dois times. Aqui é construída uma vez e rende nos dois." });
card(s, { x: M, y: 4.6, w: 3.9, h: 2.05, fill: OW, head: "Uma superfície", headColor: CH,
  body: "Receptivo, ativo, automação de processo, pesquisa, qualidade e conformidade no mesmo produto — não módulos licenciados à parte, com configuração, billing e times próprios." });
card(s, { x: 4.72, y: 4.6, w: 3.9, h: 2.05, fill: TL, head: "Uma unidade de custo", headColor: WH, bodyColor: "DDEDED",
  body: "Licença por concorrência configurada. Humanos e IA na mesma moeda; o ganho de eficiência da IA fica com o cliente, não com o fornecedor." });
card(s, { x: 8.89, y: 4.6, w: 3.89, h: 2.05, fill: GR, head: "Um substrato", headColor: WH, bodyColor: LT,
  body: "Todo contato — humano, de IA, receptivo, ativo ou importado de terceiro — produz o mesmo dado de sessão, segmento e evento. Um só pipeline de qualidade e de analytics: é o que permite pôr operação, qualidade e voz do cliente no mesmo eixo, e uma pessoa ao lado de um agente de IA." });
pageFoot(s, "O que é", 4);
s.addNotes("O cartão vermelho é o slide inteiro: a diferença é de interface, não de recurso. Construir a capacidade uma vez, em vez de duas, é o argumento que o avaliador leva para o financeiro dele.");

/* ---------------- 5 · como é (figura) ---------------- */
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
  { text: "primary · specialist · supervisor · evaluator · reviewer — fato do par (participante, sessão).      ", options: { color: GR2 } },
  { text: "Visibilidade por mensagem ", options: { bold: true, color: GR } },
  { text: "todos (inclui o cliente), só agentes, ou lista explícita de participantes.", options: { color: GR2 } }
], { x: M, y: 5.95, w: CW, h: 0.7, fontSize: 11, fontFace: BF, margin: 0, lineSpacingMultiple: 1.15 });
pageFoot(s, "Como é", 5);
s.addNotes("Se perguntarem se isso é multiagente com humano no loop: não. Ali o humano aprova ou intervém; aqui ele é recurso roteável indistinguível pelo motor de alocação.");

/* ---------------- 6 · o dial ---------------- */
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
pageFoot(s, "O dial", 6, true);
s.addNotes("Não prometer aprendizado automático. A honestidade aqui é o que diferencia de quem vende auto-melhoria mágica — e o laço supervisionado é auditável, que é o que o comprador regulado quer.");

/* ---------------- 7 · caso ponta a ponta ---------------- */
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
card(s, { x: M, y: 5.8, w: CW, h: 1.0, fill: CH, headColor: WH, bodyColor: "FBE4E7", size: 10.5,
  head: "Nenhuma ligação foi feita. Nenhum agente ficou bloqueado esperando. Nada foi repetido pelo cliente.", headGap: 0.34,
  body: "É este o caso que substitui boa parte da discagem — não porque discar seja proibido, mas porque a informação chegou sozinha, no canal certo, no instante em que passou a existir." });
pageFoot(s, "Um caso inteiro", 7);
s.addNotes("Se o tempo apertar, este slide sozinho carrega o argumento operacional. Vale perguntar antes: como esse mesmo caso corre hoje na operação deles.");

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

/* ---------------- 10 · canais ---------------- */
s = pres.addSlide();
kicker(s, "CAMADA 3");
title(s, "Abstração de canais — e contato ativo sem discador");
s.addText("webchat · WhatsApp · SMS · e-mail · voz (PSTN) · WebRTC · Instagram · Telegram · webhook",
  { x: M, y: 1.42, w: 11.9, h: 0.3, fontSize: 12, bold: true, color: MU, fontFace: BF, margin: 0 });
card(s, { x: M, y: 1.85, w: 5.95, h: 1.5, fill: OW, head: "Canal × meio", headColor: CH, headGap: 0.36, size: 10,
  body: "Canal é filtro duro de roteamento; meio (voz, vídeo, mensagem) é fator de score. Toda lógica específica vive no adaptador: um menu de botões vira lista numerada onde botão não existe — decisão do adaptador, nunca do fluxo." });
card(s, { x: 6.83, y: 1.85, w: 5.95, h: 1.5, fill: OW, head: "Ativo pelo mesmo motor", headColor: CH, headGap: 0.36, size: 10,
  body: "Audiência + campanha + entrega, endereçando um pool — nunca um fluxo específico. Disparo por agenda recorrente, distribuição em paralelo, importação com rejeição por linha. Sem stack de outbound paralela." });
card(s, { x: M, y: 3.5, w: CW, h: 1.95, fill: CH, headColor: WH, bodyColor: "FBE4E7", size: 10.5, headGap: 0.38,
  head: "Contato ativo sem discador — a premissa que estamos recusando",
  body: "O pacing do discador preditivo é função da disponibilidade do agente: ele disca N linhas porque M operadores ficarão livres em T segundos. A prova documental é o teto regulatório de taxa de abandono — a indústria admitindo, por escrito, que o modelo transfere custo para quem atende.\n\nA inversão: mensagem com motivo e assunto, e a decisão de quando conversar volta para quem foi procurado. Sobe a conversão por tentativa, cai o custo por contato útil, e o volume absoluto de conversas pode cair — é a troca, feita de propósito. O diferencial não é o link (callback existe há vinte anos): é o processo já ter estado do outro lado, então a conversa continua de onde parou.\n\nO limite: cobrança de valor relevante, prospecção fria e urgência regulatória seguem exigindo voz ativa em volume — e aí não temos discador preditivo." });
card(s, { x: M, y: 5.6, w: CW, h: 1.25, fill: TL, headColor: WH, bodyColor: "DDEDED", size: 10.5, headGap: 0.38,
  head: "Governança de contato — um portão só, inclusive para pesquisa",
  body: "Antes de qualquer abordagem: opt-out global (salvo transacional) → janela de calendário → fadiga (frequência, quarentena, teto por canal) → supressão de mailing. A decisão sempre nomeia a regra, e o registro é gravado na mesma transação. Por ser genérico, a pesquisa de satisfação passa pelo mesmo portão: convite para avaliar conta como abordagem." });
pageFoot(s, "Arquitetura · canais", 10);
s.addNotes("Amarrar com o slide 7: o caso da portabilidade é a demonstração deste slide. E a pesquisa passando pelo portão de fadiga costuma surpreender — quase ninguém trata pesquisa como contato.");

/* ---------------- 11 · identidade ---------------- */
s = pres.addSlide();
kicker(s, "CAMADA 4");
title(s, "Identificação do usuário — três classes de evidência");
lead(s, "A identidade é resolvida por âncoras e construída de forma progressiva. O que distingue esta camada não é a resolução, e sim a escala de evidência: cada âncora carrega como foi obtida, e a autorização lê essa classe, nunca só o valor.", 1.42);
const ev = [
  ["Alegada", "o que o cliente diz  ·  declaração no atendimento", "Identificar e personalizar. Nunca autoriza retomar processo em outro canal nem revelar contexto pendente — a consulta responde “verificação necessária” sem confirmar que há algo.", GR2, LT],
  ["Comprovada", "o canal que ele possui  ·  OTP · em operação", "Desafio e verificação de código, com limite de tentativas e código guardado só como hash. É a única via para esta classe: nenhum agente escreve a marcação. Libera retomada cross-canal.", CH, "FBE4E7"],
  ["Inerente", "o que ele é  ·  biometria · roadmap", "Biometria de voz ou comportamental como terceira classe, com confiança e validade próprias. A plataforma consome o veredito de um provedor e o registra com proveniência — ela não é o motor biométrico.", "6B7280", "EFEFEF"]
];
ev.forEach((e, i) => {
  const x = M + i * 4.17;
  s.addShape(pres.ShapeType.roundRect, { x: x, y: 2.25, w: 3.9, h: 2.2, fill: { color: e[3] }, rectRadius: 0.06 });
  s.addText(e[0], { x: x + 0.22, y: 2.38, w: 3.46, h: 0.3, fontSize: 14, bold: true, color: WH, fontFace: BF, margin: 0 });
  s.addText(e[1], { x: x + 0.22, y: 2.7, w: 3.46, h: 0.28, fontSize: 9.5, italic: true, color: e[4], fontFace: BF, margin: 0 });
  s.addText(e[2], { x: x + 0.22, y: 3.02, w: 3.46, h: 1.3, fontSize: 10, color: e[4], fontFace: BF, margin: 0, lineSpacingMultiple: 1.08 });
});
card(s, { x: M, y: 4.7, w: 5.95, h: 1.75, fill: OW, head: "A postura, e por que a biometria não a muda", headColor: CH, headGap: 0.38,
  body: "A plataforma é autoridade sobre posse de canal, não sobre identidade de registro — esta continua no CRM do cliente. A biometria não desloca essa fronteira justamente porque entra como evidência de terceiro registrada com proveniência, e não como veredito próprio da plataforma." });
card(s, { x: 6.83, y: 4.7, w: 5.95, h: 1.75, fill: OW, head: "Consequência para LGPD", headColor: CH, headGap: 0.38,
  body: "Dado biométrico é dado pessoal sensível: exige base legal e consentimento específicos, retenção própria e trilha de acesso. Por isso o desenho guarda o veredito e a proveniência, e não o template biométrico — que permanece no provedor. É por conformidade, e não por esforço de código, que a biometria é roadmap." });
pageFoot(s, "Arquitetura · identidade", 11);
s.addNotes("Aqui entra a resposta à pergunta de continuidade do slide 3: retomada cross-canal só é liberada com âncora comprovada, nunca com âncora alegada.");

/* ---------------- 12 · roteador + agentes ---------------- */
s = pres.addSlide();
kicker(s, "CAMADAS 5 E 6");
title(s, "Roteamento e agentes — árbitro único, runtime único");
card(s, { x: M, y: 1.5, w: 5.95, h: 2.15, fill: OW, head: "5 · Regras de alocação e fila", headColor: CH, headGap: 0.36, size: 10,
  body: "Canal e pausa são filtros duros; gateway sem sinal de vida é excluído; empate de score decide pela fila mais curta; performance entra com peso configurável e volume mínimo.\n\nA ordem da fila é chegada, nunca prioridade armazenada — prioridade e envelhecimento são recalculados na leitura, então quem espera mais sobe." });
card(s, { x: 6.83, y: 1.5, w: 5.95, h: 2.15, fill: GR, head: "Dois modos de despacho", headColor: WH, bodyColor: LT, headGap: 0.36, size: 10,
  body: "Push: o roteador entrega ao agente. Pull: o item fica numa caixa de trabalho e o agente reivindica, com reivindicação atômica, prazo e devolução à fila.\n\nO pull direcionado reserva o item a um agente específico e transborda para o pool por idade — é o “ramal” sem quebrar a regra de que a unidade endereçável é o pool." });
card(s, { x: M, y: 3.85, w: 5.95, h: 1.5, fill: TL, head: "Capacidade é do recurso, não do pool", headColor: WH, bodyColor: "DDEDED", headGap: 0.36, size: 10,
  body: "A ocupação é derivada de um semáforo por recurso, não de contador. Uma pessoa com três vagas presente em três pools rende três, não nove. Licença humana e de IA nunca são somadas — inclusive no portão de admissão." });
card(s, { x: 6.83, y: 3.85, w: 5.95, h: 1.5, fill: OW, head: "6 · Contrato único de agente", headColor: CH, headGap: 0.36, size: 10,
  body: "Autenticar → pronto → ocupado → concluir, com desfecho e status de tratativa obrigatórios, igual para humano e IA. Nenhum agente acessa sistema de negócio diretamente: toda chamada passa por servidores de ferramenta autorizados." });
card(s, { x: M, y: 5.55, w: CW, h: 1.3, fill: CH, headColor: WH, bodyColor: "FBE4E7", size: 11, headGap: 0.36,
  head: "Em cada chamada de ferramenta, abaixo de um milissegundo",
  body: "Validação de permissão — a lista enviada ao modelo já vem filtrada pelo perfil — · guarda de injeção por heurística · registro de auditoria. A política é declarada na ferramenta, não na chamada: o chamador não pode optar por sair. Agente nativo é interceptado no próprio processo; agente de terceiro, por sidecar de proxy em loopback. É a resposta à pergunta “o que a IA consultou e com que autorização”." });
pageFoot(s, "Arquitetura · roteamento e agentes", 12);
s.addNotes("A linha de capacidade costuma ser o momento em que arquiteto de CCaaS presta atenção: somar licenças de tipos diferentes é o erro que gera outage com agente ocioso.");

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
title(s, "Jornada, sessão e segmento — o registro que sobra do caso");
s.addShape(pres.ShapeType.roundRect, { x: M, y: 1.45, w: CW, h: 0.82, fill: { color: "0E4E4E" }, rectRadius: 0.05 });
s.addText("JORNADA · o processo do cliente", { x: M + 0.25, y: 1.55, w: 6, h: 0.3, fontSize: 12, bold: true, color: WH, fontFace: BF, margin: 0 });
s.addText("componente conexa de sessões — identidade pela raiz canônica, união por alias, resolvida na leitura · SLA por etapa · contexto compartilhado por 30 dias",
  { x: M + 0.25, y: 1.85, w: 11.7, h: 0.32, fontSize: 10, color: "CDE3E3", fontFace: BF, margin: 0 });
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
card(s, { x: M, y: 4.1, w: 5.95, h: 1.35, fill: OW, head: "Segmento — a janela de cada participante", headColor: CH, headGap: 0.34,
  body: "Carimba pool, papel, tipo de agente, segmento pai, sequência, duração, desfecho, canal e versão de deploy. É o grão da avaliação de qualidade.", size: 10.5 });
card(s, { x: 6.83, y: 4.1, w: 5.95, h: 1.35, fill: OW, head: "Sessão — a perspectiva do cliente", headColor: CH, headGap: 0.34,
  body: "Ativa, suspensa, encerrada ou abandonada, com domínio fechado de motivos. Suspensa é primeira classe: o processo que espera dias é a mesma sessão.", size: 10.5 });
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
  body: "Fronteira aberta — agente externo entra pelo sidecar de proxy com a mesma governança · histórico de outra plataforma entra pelo leitor plugável e reidrata sessão e transcrição." });
pageFoot(s, "Módulos · relacionamento", 15);
s.addNotes("Mesma numeração do slide 8: o mesmo contato, agora atravessando serviços. Cerca de trinta serviços, com dependências explícitas e sem ciclos.");

/* ---------------- 16 · módulos de operação ---------------- */
s = pres.addSlide();
kicker(s, "MÓDULOS DE OPERAÇÃO");
title(s, "Console, Analytics, Bancada, Qualidade, Survey e WFM");
const mods = [
  ["Console", GR, WH, LT, "Superfície de orquestração, não tela de atendimento. Participantes de IA em tempo real com passo do fluxo, convocar especialista, delegar tarefa com instrução e visibilidade, intervenção de supervisor, caixa de trabalho pull e um renderizador genérico de formulário."],
  ["Analytics", OW, GR, GR2, "Monitoria em tempo real e análise retrospectiva com detalhamento em três níveis — processo, contato e segmento — até o turno. Fila e SLA, disponibilidade e pausas por motivo, picos de ocupação registrados na transição, não por amostragem."],
  ["Bancada de agentes", TL, WH, "DDEDED", "Comparação lado a lado, com humanos e agentes de IA na mesma régua. Dez lentes que atravessam módulos que num CCaaS são produtos diferentes: operação (resolução, escalação, TMA, ocupação, pausas), qualidade (nota, dimensão, deploy), voz do cliente (NPS) e wrap-up. Sobre a mesma seleção, o cross-cut põe os agentes escolhidos lado a lado pelos KPIs da lente. Reunir isso num eixo só é possível pelo substrato único: numa suíte viram quatro produtos e quatro identidades de agente."],
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
s.addNotes("O WFM é o único roadmap desta página, e o argumento é o substrato: dimensionar incluindo agentes de IA na mesma conta de concorrência só é possível se o TMA já estiver limpo de wrap-up.");

/* ---------------- 17 · componentes ---------------- */
s = pres.addSlide();
kicker(s, "COMPONENTES");
title(s, "Mensageria e persistência");
s.addText("Cada tecnologia com uma responsabilidade única e declarada. O modelo de estado é explícito e auditável pelo time de arquitetura do cliente — não escondido atrás de serviço gerenciado proprietário.",
  { x: M, y: 1.4, w: 11.6, h: 0.35, fontSize: 12.5, color: GR2, fontFace: BF, margin: 0 });
tbl(s, [
  ["Componente", "Papel", "O que guarda"],
  ["Kafka", "Bus de mensagens e eventos", "Conversa, ciclo de vida de agente, workflow e coleta, união de jornadas, sinais de cliente, auditoria de ferramentas, alterações de registro e config, avaliação, uso, ocupação e fila."],
  ["Redis", "Estado de tempo real", "Stream canônico da sessão, contexto de sessão, segmento e jornada, estado do fluxo a cada transição, filas por chegada, semáforo de capacidade, prazos da caixa de trabalho, tokens de retomada."],
  ["PostgreSQL", "Registro durável e relacional", "Agentes, pools, skills e deploys; auth, perfis e grupos; workflow; formulários de diálogo; identidade e âncoras; agenda; audiência e campanhas; avaliação e contestação."],
  ["PostgreSQL vetorial", "Busca semântica", "Base de conhecimento para RAG, com extensão de vetores, consumida por servidor de ferramentas próprio. Guarda também as notas de calibração devolvidas ao avaliador."],
  ["ClickHouse", "Analítico e série temporal", "Sessões, segmentos, mensagens e linha do tempo; desempenho diário por agente; picos de ocupação; pausas; auditoria de chamadas e registro imutável de acesso; resultados de avaliação; sinais de cliente."],
  ["Object storage", "Mídia", "Gravações de voz e WebRTC, anexos de webchat com expiração em duas fases."]
], { y: 1.95, colW: [1.95, 2.5, 7.78], rowH: 0.5, fontSize: 9.5 });
card(s, { x: M, y: 5.6, w: 5.95, h: 1.25, fill: OW, size: 10,
  body: "Série temporal:  vive no ClickHouse, não no PostgreSQL — volume de evento, consulta colunar por intervalo, dado imutável. Separar os papéis evita relatório pesado competindo com escrita transacional, e é o que torna viável o WFM sobre histórico real." });
card(s, { x: 6.83, y: 5.6, w: 5.95, h: 1.25, fill: OW, size: 10,
  body: "Procedência:  todo substrato carrega a origem — produção, importado de terceiro ou reavaliação interna — com filtro padrão para produção em relatório e amostragem. Trazer histórico externo não contamina a medição operacional." });
pageFoot(s, "Componentes", 17);
s.addNotes("Se houver arquiteto de dados na sala, esta é a página em que ele decide se o resto é sério.");

/* ---------------- 18 · limites ---------------- */
s = pres.addSlide();
s.background = { color: GR };
s.addShape(pres.ShapeType.ellipse, { x: 9.9, y: 4.4, w: 6.2, h: 6.2, fill: { color: GR2 } });
kicker(s, "ONDE ESTAMOS", PALE);
title(s, "Limites declarados e próximo passo", WH);
s.addText("Tudo que este material marcou como parcial ou roadmap, num lugar só. Preferimos dizer o que você descobriria de qualquer forma.",
  { x: M, y: 1.45, w: 11.4, h: 0.35, fontSize: 13, italic: true, color: LT, fontFace: BF, margin: 0 });
const lim = [
  ["Estágio e certificações", "Pronto em arquitetura e funcionalidade, validado em ambiente controlado e parte em atendimento real. Sem produção em cliente e sem certificação emitida — em andamento, com a evidência técnica já produzida pela arquitetura."],
  ["Discador preditivo", "Não existe. Pacing, guarda de abandono e listas restritivas são roadmap. O que substituímos é a discagem de entrega de informação, não a voz ativa de venda e negociação."],
  ["Isolamento multi-tenant", "Fundação pervasiva, isolamento operacional completo em maturação. É o item nº 1 a validar em prova de conceito."],
  ["Biometria e WFM", "Roadmap nos dois casos, com o substrato já construído: escala de evidência na identidade, e série temporal de fila, TMA e ocupação para o dimensionamento."],
  ["Fusão de cadastros", "Parcial. Resolução de identidade, âncoras progressivas e posse de canal em operação; referência externa a CRM e fusão de clientes são a fase seguinte."]
];
lim.forEach((l, i) => {
  const y = 1.92 + i * 0.68;
  numDot(s, M, y + 0.02, i + 1, 0.3);
  s.addText([{ text: l[0] + "  —  ", options: { bold: true, color: WH } }, { text: l[1], options: { color: LT } }],
    { x: M + 0.44, y: y, w: 8.9, h: 0.64, fontSize: 10.5, fontFace: BF, margin: 0, valign: "top", lineSpacingMultiple: 1.06 });
});
s.addText([{ text: "Um princípio de projeto:  ", options: { bold: true, color: WH } },
  { text: "quando a conveniência de quem contata e a de quem é contatado entram em conflito, a plataforma expõe a escolha em vez de escondê-la. A política é do cliente; a regra explícita, nomeada em cada decisão e com trilha, é da plataforma.", options: { color: LT, italic: true } }],
  { x: M, y: 5.42, w: 9.1, h: 0.6, fontSize: 10.5, fontFace: BF, margin: 0, valign: "top", lineSpacingMultiple: 1.06 });
s.addShape(pres.ShapeType.roundRect, { x: M, y: 6.1, w: 9.1, h: 0.78, fill: { color: CH }, rectRadius: 0.06 });
s.addText([{ text: "Próximo passo  ", options: { bold: true, color: WH, fontSize: 12 } },
  { text: "uma sessão técnica com o seu time de arquitetura e de operação, sem custo. Saímos dela com um escopo possível, os pontos a validar em prova de conceito e números — ou com a conclusão honesta de que não é o momento.", options: { color: "FBE4E7", fontSize: 10.5 } }],
  { x: M + 0.28, y: 6.1, w: 8.55, h: 0.78, fontFace: BF, valign: "middle", margin: 0, lineSpacingMultiple: 1.04 });
pageFoot(s, "PlugHub · 2026", 18, true);
s.addNotes("Declarar limite cedo qualifica rápido. Quem gosta de construir recebe influência sobre roadmap e acesso direto a quem constrói — e isso não se compra de um incumbente.");

pres.writeFile({ fileName: "plughub-descritivo-tecnico.pptx" })
  .then(f => console.log("Gerado:", f));
