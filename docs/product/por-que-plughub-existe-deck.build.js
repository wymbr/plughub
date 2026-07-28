/**
 * Gerador do deck "Por que o PlugHub existe" (.pptx).
 *
 * Uso (no WSL, a partir da raiz do repo):
 *   npm i -D pptxgenjs                 # se ainda não estiver instalado
 *   node docs/product/por-que-plughub-existe-deck.build.js
 *
 * Saída: por-que-plughub-existe.pptx no diretório corrente.
 *
 * Conteúdo espelha docs/product/por-que-plughub-existe.md.
 * Alternativa sem Node: docs/product/por-que-plughub-existe-deck.html
 * (abre no navegador; ← → navega, N mostra notas, P exporta PDF).
 */
const pptxgen = require("pptxgenjs");

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.3 x 7.5
pres.author = "PlugHub";
pres.title = "Por que o PlugHub existe";

// ---- palette -------------------------------------------------------------
const GRAPHITE = "2E3138";
const GRAPHITE_2 = "3E434D";
const WHITE = "FFFFFF";
const OFFWHITE = "F2F2F2";
const CHERRY = "990011";
const TEAL = "046A6A";
const MUTED = "6B7280";
const LIGHTTXT = "D6D8DC";

const HFONT = "Cambria";
const BFONT = "Calibri";

const W = 13.3;
const M = 0.7; // margin

// ---- helpers -------------------------------------------------------------
function darkSlide() {
  const s = pres.addSlide();
  s.background = { color: GRAPHITE };
  return s;
}

function lightSlide(titleText, kicker) {
  const s = pres.addSlide();
  s.background = { color: WHITE };
  if (kicker) {
    s.addText(kicker.toUpperCase(), {
      x: M, y: 0.42, w: W - 2 * M, h: 0.26,
      fontFace: BFONT, fontSize: 11, bold: true, color: CHERRY,
      charSpacing: 1.4, margin: 0,
    });
  }
  s.addText(titleText, {
    x: M, y: kicker ? 0.72 : 0.55, w: W - 2 * M, h: 0.82,
    fontFace: HFONT, fontSize: 33, bold: true, color: GRAPHITE, margin: 0,
  });
  return s;
}

// numbered circle motif
function marker(slide, x, y, label, fill) {
  slide.addShape(pres.ShapeType.ellipse, {
    x, y, w: 0.46, h: 0.46, fill: { color: fill || CHERRY },
  });
  slide.addText(label, {
    x, y, w: 0.46, h: 0.46,
    fontFace: BFONT, fontSize: 15, bold: true, color: WHITE,
    align: "center", valign: "middle", margin: 0,
  });
}

function card(slide, opts) {
  const { x, y, w, h, title, body, accent } = opts;
  slide.addShape(pres.ShapeType.roundRect, {
    x, y, w, h, rectRadius: 0.06,
    fill: { color: OFFWHITE },
  });
  slide.addText(title, {
    x: x + 0.28, y: y + 0.24, w: w - 0.56, h: 0.4,
    fontFace: BFONT, fontSize: 15, bold: true,
    color: accent || GRAPHITE, margin: 0,
  });
  slide.addText(body, {
    x: x + 0.28, y: y + 0.68, w: w - 0.56, h: h - 0.92,
    fontFace: BFONT, fontSize: 12.5, color: GRAPHITE_2,
    margin: 0, lineSpacingMultiple: 1.14,
  });
}

// =========================================================================
// 1 — TITLE
// =========================================================================
{
  const s = darkSlide();
  s.addShape(pres.ShapeType.ellipse, {
    x: -1.6, y: -1.9, w: 5.6, h: 5.6, fill: { color: GRAPHITE_2 },
  });
  s.addText("Por que o PlugHub existe", {
    x: M, y: 2.28, w: 10.4, h: 1.1,
    fontFace: HFONT, fontSize: 50, bold: true, color: WHITE, margin: 0,
  });
  s.addText("O argumento de existência: por que construir mais um produto neste espaço", {
    x: M, y: 3.44, w: 9.6, h: 0.5,
    fontFace: BFONT, fontSize: 17, color: LIGHTTXT, margin: 0,
  });
  s.addShape(pres.ShapeType.rect, {
    x: M, y: 4.22, w: 1.5, h: 0.045, fill: { color: CHERRY },
  });
  s.addText("Documento de posicionamento estratégico  ·  uso interno  ·  julho 2026", {
    x: M, y: 4.5, w: 9, h: 0.34,
    fontFace: BFONT, fontSize: 12, color: MUTED, margin: 0,
  });
  s.addNotes(
    "Deck interno. Objetivo: alinhar time/sócios sobre por que o produto existe — não é roteiro de venda.\n\n" +
    "Abrir dizendo: 'não vou listar funcionalidades. Vou defender por que este produto deveria existir, e admitir o que nele não justifica nada.'"
  );
}

// =========================================================================
// 2 — A PERGUNTA
// =========================================================================
{
  const s = darkSlide();
  s.addText("A pergunta cética", {
    x: M, y: 0.72, w: 8, h: 0.4,
    fontFace: BFONT, fontSize: 11, bold: true, color: CHERRY,
    charSpacing: 1.4, margin: 0,
  });
  s.addText("“Por que mais um produto deste tipo,\nse ele resolve problemas que já são resolvidos?”", {
    x: M, y: 1.35, w: 11.6, h: 1.7,
    fontFace: HFONT, fontSize: 31, italic: true, color: WHITE,
    margin: 0, lineSpacingMultiple: 1.16,
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 3.6, w: 11.9, h: 2.35, rectRadius: 0.06,
    fill: { color: GRAPHITE_2 },
  });
  s.addText("A resposta curta", {
    x: M + 0.45, y: 3.9, w: 10, h: 0.32,
    fontFace: BFONT, fontSize: 12, bold: true, color: CHERRY,
    charSpacing: 1.2, margin: 0,
  });
  s.addText("Os problemas não estão resolvidos — estão contornados.", {
    x: M + 0.45, y: 4.26, w: 11, h: 0.55,
    fontFace: HFONT, fontSize: 28, bold: true, color: WHITE, margin: 0,
  });
  s.addText(
    "Cada contorno existe porque a arquitetura embaixo não comporta a solução direta. " +
    "E a limitação é fundacional, não funcional.",
    {
      x: M + 0.45, y: 4.95, w: 11, h: 0.75,
      fontFace: BFONT, fontSize: 15, color: LIGHTTXT,
      margin: 0, lineSpacingMultiple: 1.2,
    }
  );
  s.addNotes(
    "Este é o eixo do deck inteiro. Repetir a frase 'contornados, não resolvidos' — é a que fica.\n\n" +
    "Se a limitação fosse funcional (falta de feature), a resposta certa seria esperar o incumbente lançar. E seria mesmo. " +
    "O argumento só se sustenta se a limitação for fundacional."
  );
}

// =========================================================================
// 3 — O TESTE DE JUSTIFICAÇÃO
// =========================================================================
{
  const s = lightSlide("O teste de justificação", "Como filtrar");
  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 1.72, w: 11.9, h: 1.05, rectRadius: 0.06,
    fill: { color: GRAPHITE },
  });
  s.addText("“Por que a Genesys não poderia lançar isto no trimestre que vem?”", {
    x: M + 0.4, y: 1.72, w: 11.1, h: 1.05,
    fontFace: HFONT, fontSize: 24, bold: true, color: WHITE,
    valign: "middle", margin: 0,
  });
  s.addText(
    "Se a resposta honesta for “poderia”, aquela capacidade não justifica o projeto. " +
    "Pode ser necessária, pode ser melhor que a do incumbente — mas não é razão para o produto existir.",
    {
      x: M, y: 2.95, w: 11.9, h: 0.6,
      fontFace: BFONT, fontSize: 14, color: GRAPHITE_2, margin: 0,
      lineSpacingMultiple: 1.15,
    }
  );

  const cw = 3.72, gap = 0.37;
  card(s, {
    x: M, y: 3.75, w: cw, h: 2.5, accent: CHERRY,
    title: "Fundacional",
    body: "Decisão de arquitetura que o incumbente não pode replicar sem reescrever.\n\nÉ a razão de existir.",
  });
  card(s, {
    x: M + cw + gap, y: 3.75, w: cw, h: 2.5, accent: TEAL,
    title: "Consequente",
    body: "Capacidade que só é possível porque a decisão fundacional foi tomada.\n\nÉ a prova de que a fundação é real.",
  });
  card(s, {
    x: M + 2 * (cw + gap), y: 3.75, w: cw, h: 2.5, accent: MUTED,
    title: "Custo de entrada",
    body: "Necessário para o produto ser utilizável; replicável por qualquer um.\n\nJustifica instrumentalmente, nunca em si.",
  });
  s.addNotes(
    "Aplicar esse filtro com rigor elimina a maior parte da superfície do produto. Isso é BOM.\n\n" +
    "A credibilidade da lista curta que sobra depende de admitir a lista longa que cai. " +
    "Um pitch que apresenta canais, console e dashboards como diferencial destrói a própria tese."
  );
}

// =========================================================================
// 4 — A PREMISSA QUE NINGUÉM QUESTIONA
// =========================================================================
{
  const s = lightSlide("A premissa que ninguém questiona", "A aposta fundacional");

  const boxes = ["cliente", "bot tenta", "bot falha", "transfere", "humano resolve"];
  const bw = 2.16, bh = 0.92, bgap = 0.29;
  let bx = M;
  boxes.forEach((label, i) => {
    const isHuman = i === 4;
    s.addShape(pres.ShapeType.roundRect, {
      x: bx, y: 1.95, w: bw, h: bh, rectRadius: 0.06,
      fill: { color: isHuman ? CHERRY : OFFWHITE },
    });
    s.addText(label, {
      x: bx, y: 1.95, w: bw, h: bh,
      fontFace: BFONT, fontSize: 14, bold: true,
      color: isHuman ? WHITE : GRAPHITE,
      align: "center", valign: "middle", margin: 0,
    });
    if (i < boxes.length - 1) {
      s.addText("→", {
        x: bx + bw, y: 1.95, w: bgap, h: bh,
        fontFace: BFONT, fontSize: 17, color: MUTED,
        align: "center", valign: "middle", margin: 0,
      });
    }
    bx += bw + bgap;
  });

  s.addText("A IA é uma etapa anterior ao atendimento. O humano é o anteparo.", {
    x: M, y: 3.15, w: 11.9, h: 0.45,
    fontFace: HFONT, fontSize: 21, bold: true, color: GRAPHITE, margin: 0,
  });
  s.addText(
    "Modelo mental compartilhado por Genesys, NICE, Five9, Talkdesk, Agentforce, Gemini, Fin, Sierra e Decagon — " +
    "nove produtos com arquiteturas radicalmente diferentes.",
    {
      x: M, y: 3.62, w: 11.9, h: 0.5,
      fontFace: BFONT, fontSize: 13.5, color: MUTED, margin: 0,
    }
  );

  const cw = 2.72, gap = 0.33;
  const items = [
    ["Modelo de sessão", "Tem “conversa com o bot” e “conversa com o agente” como fases distintas."],
    ["Roteador", "Só conhece recursos humanos; o bot é pré-processamento."],
    ["Licenciamento", "Assento humano e consumo de IA em moedas diferentes."],
    ["Relatório", "Contenção do bot e AHT do humano como universos separados."],
  ];
  items.forEach((it, i) => {
    card(s, {
      x: M + i * (cw + gap), y: 4.35, w: cw, h: 1.95,
      title: it[0], body: it[1],
    });
  });
  s.addNotes(
    "Ponto crítico: a assimetria NÃO é uma escolha de produto que eles revisam numa sprint. " +
    "Está distribuída por toda a stack — os quatro cartões mostram onde.\n\n" +
    "Desfazer isso é reescrever, não estender. É exatamente aí que nasce a justificativa para um produto novo."
  );
}

// =========================================================================
// 5 — A INVERSÃO
// =========================================================================
{
  const s = darkSlide();
  s.addText("A INVERSÃO", {
    x: M, y: 0.75, w: 8, h: 0.34,
    fontFace: BFONT, fontSize: 11, bold: true, color: CHERRY,
    charSpacing: 1.4, margin: 0,
  });
  s.addText("Humano e IA são duas implementações\nda mesma interface.", {
    x: M, y: 1.2, w: 11.6, h: 1.5,
    fontFace: HFONT, fontSize: 36, bold: true, color: WHITE,
    margin: 0, lineSpacingMultiple: 1.1,
  });
  s.addText("A sessão é uma sala de conferência, não uma fila de passagem.", {
    x: M, y: 2.72, w: 11.6, h: 0.42,
    fontFace: BFONT, fontSize: 16, italic: true, color: LIGHTTXT, margin: 0,
  });

  const rows = [
    ["Mesma competência", "Pool, canais, skills, score. O roteador não sabe se aloca uma pessoa ou uma instância de skill-flow."],
    ["Mesma alocação", "Canal como filtro duro, SLA, senioridade, performance. Os dois disputam os mesmos slots da mesma fila."],
    ["Mesma sessão", "Vários participantes simultâneos — primário, especialista, supervisor, avaliador — com visibilidade por participante."],
  ];
  rows.forEach((r, i) => {
    const y = 3.55 + i * 1.02;
    marker(s, M, y, String(i + 1), CHERRY);
    s.addText(r[0], {
      x: M + 0.72, y: y - 0.03, w: 2.9, h: 0.36,
      fontFace: BFONT, fontSize: 15, bold: true, color: WHITE, margin: 0,
    });
    s.addText(r[1], {
      x: M + 3.55, y: y - 0.05, w: 8.35, h: 0.62,
      fontFace: BFONT, fontSize: 13, color: LIGHTTXT, margin: 0,
      lineSpacingMultiple: 1.12,
    });
  });
  s.addNotes(
    "Consequência conceitual mais importante: o HANDOFF DEIXA DE SER O PRIMITIVO. " +
    "Vira caso particular — e raro — de algo mais geral: participantes entrando e saindo de uma sala que persiste.\n\n" +
    "Se alguém perguntar 'isso não é só multiagente com humano no loop?': não. No multiagente o humano aprova ou intervém; " +
    "aqui ele é um recurso roteável indistinguível pelo motor de alocação."
  );
}

// =========================================================================
// 6 — POR QUE AGORA
// =========================================================================
{
  const s = lightSlide("Por que a inversão vale o custo agora", "Tese de mercado");

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 1.78, w: 5.75, h: 2.1, rectRadius: 0.06,
    fill: { color: OFFWHITE },
  });
  s.addText("NÍVEL 1  ·  contato isolado", {
    x: M + 0.32, y: 2.02, w: 5.1, h: 0.3,
    fontFace: BFONT, fontSize: 11.5, bold: true, color: MUTED, charSpacing: 1, margin: 0,
  });
  s.addText("Commodity", {
    x: M + 0.32, y: 2.36, w: 5.1, h: 0.5,
    fontFace: HFONT, fontSize: 25, bold: true, color: MUTED, margin: 0,
  });
  s.addText(
    "Todo mundo tem bot classe-Fin. Automatizar centro de custo tem teto de ROI. Converge para o custo do token.",
    {
      x: M + 0.32, y: 2.92, w: 5.1, h: 0.85,
      fontFace: BFONT, fontSize: 13, color: GRAPHITE_2, margin: 0, lineSpacingMultiple: 1.14,
    }
  );

  s.addShape(pres.ShapeType.roundRect, {
    x: M + 6.15, y: 1.78, w: 5.75, h: 2.1, rectRadius: 0.06,
    fill: { color: TEAL },
  });
  s.addText("NÍVEL 2  ·  processo inteiro", {
    x: M + 6.47, y: 2.02, w: 5.1, h: 0.3,
    fontFace: BFONT, fontSize: 11.5, bold: true, color: "BFE3E3", charSpacing: 1, margin: 0,
  });
  s.addText("Onde mora a margem", {
    x: M + 6.47, y: 2.36, w: 5.1, h: 0.5,
    fontFace: HFONT, fontSize: 25, bold: true, color: WHITE, margin: 0,
  });
  s.addText(
    "Cobrança, retenção, onboarding, crédito. O ROI toca receita e capital de giro — não minutos.",
    {
      x: M + 6.47, y: 2.92, w: 5.1, h: 0.85,
      fontFace: BFONT, fontSize: 13, color: "E4F2F2", margin: 0, lineSpacingMultiple: 1.14,
    }
  );

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 4.18, w: 11.9, h: 2.05, rectRadius: 0.06,
    fill: { color: GRAPHITE },
  });
  s.addText("O dial do anteparo", {
    x: M + 0.45, y: 4.42, w: 10.8, h: 0.34,
    fontFace: BFONT, fontSize: 12, bold: true, color: CHERRY, charSpacing: 1.2, margin: 0,
  });
  s.addText(
    "O humano começa no comando do processo, a IA assume pedaços, e a cada pedaço que a avaliação prova " +
    "confiável o humano recua — medido, auditado, reversível.",
    {
      x: M + 0.45, y: 4.78, w: 11, h: 0.82,
      fontFace: HFONT, fontSize: 18, color: WHITE, margin: 0, lineSpacingMultiple: 1.14,
    }
  );
  s.addText(
    "Processo exige humano e IA alternando o comando — não um passando a bola ao outro. " +
    "Um produto deflection-first não consegue nem representar esse arranjo.",
    {
      x: M + 0.45, y: 5.66, w: 11, h: 0.42,
      fontFace: BFONT, fontSize: 13, color: LIGHTTXT, margin: 0,
    }
  );
  s.addNotes(
    "A inversão só vale o custo se esta tese estiver certa. Deixar isso explícito — é uma aposta, não um fato.\n\n" +
    "Cuidado de discurso: 'fazemos processo' está lotado (Sierra, Decagon, Fin com Procedures). " +
    "Não nos diferenciamos pela alegação de fazer processo, e sim pelo substrato: processo que se confia e audita."
  );
}

// =========================================================================
// 7 — O QUE DECORRE (overview)
// =========================================================================
{
  const s = lightSlide("O que decorre da inversão", "Consequências, não features");
  s.addText(
    "Não são capacidades adicionadas — são consequências da decisão fundacional. " +
    "Por isso formam um conjunto coerente em vez de um catálogo, e por isso um concorrente não adota uma sem adotar a fundação.",
    {
      x: M, y: 1.68, w: 11.9, h: 0.6,
      fontFace: BFONT, fontSize: 14, color: GRAPHITE_2, margin: 0, lineSpacingMultiple: 1.15,
    }
  );

  const items = [
    ["Especialista como participante real", "Convocado pelo mesmo roteador — não é sugestão em barra lateral. Mesmo artefato para robô e humano."],
    ["Visibilidade por participante × campo × role", "Delegar dado sensível: ver o progresso, não ver o dado. Escopo PCI cai sem tirar o cliente da linha."],
    ["Ciclo de vida em três camadas", "Contato ≠ segmento ≠ conferência. O wrap-up destaca e o AHT vira verdade."],
    ["Processo como entidade operacional", "O roteador conhece a jornada; SLA corre por etapa; contexto atravessa contatos."],
    ["Licença por concorrência", "Humanos e IA na mesma unidade. Modelo de negócio caindo da arquitetura."],
  ];
  const cw = 3.72, gap = 0.37;
  items.slice(0, 3).forEach((it, i) => {
    card(s, { x: M + i * (cw + gap), y: 2.45, w: cw, h: 1.85, title: it[0], body: it[1] });
  });
  items.slice(3).forEach((it, i) => {
    card(s, { x: M + i * (cw + gap), y: 4.48, w: cw, h: 1.85, title: it[0], body: it[1] });
  });
  s.addShape(pres.ShapeType.roundRect, {
    x: M + 2 * (cw + gap), y: 4.48, w: cw, h: 1.85, rectRadius: 0.06,
    fill: { color: GRAPHITE },
  });
  s.addText("As três próximas\nvalem um slide cada.", {
    x: M + 2 * (cw + gap) + 0.28, y: 4.48, w: cw - 0.56, h: 1.85,
    fontFace: HFONT, fontSize: 16, italic: true, color: LIGHTTXT,
    valign: "middle", margin: 0,
  });
  s.addNotes(
    "Slide de mapa. Não explicar cada um aqui — só mostrar que são cinco e que derivam da mesma decisão.\n" +
    "Vamos aprofundar em três: dado sensível, wrap-up e processo."
  );
}

// =========================================================================
// 8 — DADO SENSÍVEL
// =========================================================================
{
  const s = lightSlide("Captura de dado sensível", "Consequência 1");

  s.addText("O contorno de hoje", {
    x: M, y: 1.72, w: 5.75, h: 0.35,
    fontFace: BFONT, fontSize: 12, bold: true, color: CHERRY, charSpacing: 1.2, margin: 0,
  });
  const bad = [
    "Transfere para URA — e perde parte dos clientes no caminho",
    "Pausa a gravação — e cria um buraco na auditoria",
    "O agente ouve o número — e a operação inteira entra no escopo PCI",
  ];
  bad.forEach((t, i) => {
    const y = 2.18 + i * 0.86;
    s.addShape(pres.ShapeType.roundRect, {
      x: M, y, w: 5.75, h: 0.72, rectRadius: 0.06, fill: { color: OFFWHITE },
    });
    s.addText(t, {
      x: M + 0.28, y, w: 5.2, h: 0.72,
      fontFace: BFONT, fontSize: 12.5, color: GRAPHITE_2,
      valign: "middle", margin: 0, lineSpacingMultiple: 1.1,
    });
  });

  s.addText("O que a inversão permite", {
    x: M + 6.15, y: 1.72, w: 5.75, h: 0.35,
    fontFace: BFONT, fontSize: 12, bold: true, color: TEAL, charSpacing: 1.2, margin: 0,
  });
  s.addShape(pres.ShapeType.roundRect, {
    x: M + 6.15, y: 2.18, w: 5.75, h: 2.32, rectRadius: 0.06, fill: { color: TEAL },
  });
  s.addText("O humano delega, vê o progresso e não vê o dado.", {
    x: M + 6.47, y: 2.44, w: 5.15, h: 0.8,
    fontFace: HFONT, fontSize: 19, bold: true, color: WHITE, margin: 0, lineSpacingMultiple: 1.1,
  });
  s.addText(
    "Etapa atual, status de validação e tempo decorrido visíveis. Pode retomar o controle a qualquer momento. " +
    "Ao concluir, recebe só o resultado — o dado bruto nunca passou pela tela dele.",
    {
      x: M + 6.47, y: 3.3, w: 5.15, h: 1.05,
      fontFace: BFONT, fontSize: 12.5, color: "E4F2F2", margin: 0, lineSpacingMultiple: 1.14,
    }
  );

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 4.95, w: 11.9, h: 1.32, rectRadius: 0.06, fill: { color: GRAPHITE },
  });
  s.addText("Resolve simultaneamente:", {
    x: M + 0.42, y: 5.14, w: 3, h: 0.32,
    fontFace: BFONT, fontSize: 12, bold: true, color: CHERRY, margin: 0,
  });
  s.addText(
    "escopo PCI-DSS reduzido (o operador não acessa o PAN)  ·  LGPD (minimização por role)  ·  " +
    "SOX (trilha de quem viu o quê)  —  e o cliente não percebe transferência alguma.",
    {
      x: M + 0.42, y: 5.5, w: 11.1, h: 0.62,
      fontFace: BFONT, fontSize: 13.5, color: LIGHTTXT, margin: 0, lineSpacingMultiple: 1.14,
    }
  );
  s.addNotes(
    "Este é o caso que vende sozinho. Numa primeira conversa, abrir com a pergunta: " +
    "'quando o cliente vai passar o cartão, o que acontece hoje?' — a resposta é sempre constrangedora e eles sabem.\n\n" +
    "Ponto estrutural: um concorrente que só tem handoff NÃO TEM ONDE ENCAIXAR isto. " +
    "Não existe 'dois participantes simultâneos com visões diferentes do mesmo conteúdo' no modelo dele."
  );
}

// =========================================================================
// 9 — WRAP-UP / AHT
// =========================================================================
{
  const s = lightSlide("O wrap-up e a verdade do AHT", "Consequência 2");
  s.addText("Três coisas que a indústria trata como uma só:", {
    x: M, y: 1.68, w: 11.9, h: 0.34,
    fontFace: BFONT, fontSize: 14, color: GRAPHITE_2, margin: 0,
  });

  const layers = [
    ["Contato", "A perspectiva do cliente", "Termina quando o cliente vai embora — estatísticas congelam aqui", 6.4, CHERRY],
    ["Segmento", "A janela de cada participante", "Termina em agent_done — o recurso do pool é liberado aqui", 8.2, GRAPHITE],
    ["Conferência", "A sala (infraestrutura)", "Termina quando o último participante sai", 10.0, TEAL],
  ];
  layers.forEach((l, i) => {
    const y = 2.2 + i * 0.92;
    s.addShape(pres.ShapeType.roundRect, {
      x: M, y, w: l[3], h: 0.72, rectRadius: 0.06, fill: { color: l[4] },
    });
    s.addText(l[0], {
      x: M + 0.28, y, w: 1.7, h: 0.72,
      fontFace: BFONT, fontSize: 14, bold: true, color: WHITE, valign: "middle", margin: 0,
    });
    s.addText(l[1], {
      x: M + 2.0, y, w: 2.5, h: 0.72,
      fontFace: BFONT, fontSize: 12, color: "E8E8E8", valign: "middle", margin: 0,
    });
    s.addText(l[2], {
      x: M + 4.55, y, w: l[3] - 4.75, h: 0.72,
      fontFace: BFONT, fontSize: 11.5, italic: true, color: "E8E8E8", valign: "middle", margin: 0,
    });
  });

  s.addText(
    "Colapsá-las é a causa de uma dor universal e normalizada: o wrap-up infla o AHT. " +
    "O cliente já foi embora, mas o contato só fecha quando o agente termina a disposição — e o agente fica bloqueado.",
    {
      x: M, y: 5.0, w: 11.9, h: 0.6,
      fontFace: BFONT, fontSize: 13.5, color: GRAPHITE_2, margin: 0, lineSpacingMultiple: 1.15,
    }
  );
  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 5.72, w: 11.9, h: 0.72, rectRadius: 0.06, fill: { color: OFFWHITE },
  });
  s.addText(
    "Separadas: o contato fecha quando o cliente sai (AHT vira verdade) e a disposição vira item de fila do próprio agente.",
    {
      x: M + 0.38, y: 5.72, w: 11.2, h: 0.72,
      fontFace: BFONT, fontSize: 13.5, bold: true, color: GRAPHITE, valign: "middle", margin: 0,
    }
  );
  s.addNotes(
    "Implementado e validado com atendimento real (CHANGELOG 2026-07-27).\n\n" +
    "É o exemplo mais limpo de 'a separação era real, não diagrama' — a correção só foi possível " +
    "porque as camadas já estavam separadas no modelo. Se alguém duvidar que arquitetura vira operação, este é o caso."
  );
}

// =========================================================================
// 10 — PROCESSO
// =========================================================================
{
  const s = lightSlide("O processo não tem dono", "Consequência 3");

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 1.72, w: 11.9, h: 1.62, rectRadius: 0.06, fill: { color: GRAPHITE },
  });
  s.addText(
    "“Seu FCR é 80%? Então 20% voltam. O cliente que voltou quatro vezes pelo mesmo problema aparece " +
    "como quatro contatos e três falhas de FCR — ou como um processo que levou doze dias?”",
    {
      x: M + 0.45, y: 1.72, w: 11, h: 1.62,
      fontFace: HFONT, fontSize: 19, italic: true, color: WHITE,
      valign: "middle", margin: 0, lineSpacingMultiple: 1.16,
    }
  );
  s.addText(
    "O FCR é a métrica que quase mede processo e falha: conta a repetição como fracasso do contato, " +
    "não como duração do processo.",
    {
      x: M, y: 3.48, w: 11.9, h: 0.42,
      fontFace: BFONT, fontSize: 13.5, color: GRAPHITE_2, margin: 0,
    }
  );

  const cw = 3.72, gap = 0.37;
  card(s, {
    x: M, y: 4.05, w: cw, h: 2.22, accent: CHERRY,
    title: "Sem dono",
    body: "Cada área cumpre o próprio SLA enquanto o cliente espera doze dias. Problema órfão por definição.",
  });
  card(s, {
    x: M + cw + gap, y: 4.05, w: cw, h: 2.22, accent: CHERRY,
    title: "Sem custo",
    body: "Todos sabem o custo por contato. Ninguém sabe quanto custou resolver a portabilidade do início ao fim.",
  });
  card(s, {
    x: M + 2 * (cw + gap), y: 4.05, w: cw, h: 2.22, accent: TEAL,
    title: "O que muda",
    body: "Roteador ciente da jornada, SLA por etapa, contexto atravessando contatos — e inbound/outbound no mesmo motor.",
  });
  s.addNotes(
    "Journey analytics existe (Pointillist na Genesys, Adobe CJA) — mas é retrospectivo, sem amarração ao roteador. " +
    "Case existe no CRM — mas fora do motor de atendimento. Aqui é operacional E analítico.\n\n" +
    "Dimensão organizacional: inbound/outbound é divisão de TIMES antes de ser de tecnologia. " +
    "Quem liga de volta não sabe o que aconteceu no inbound. Motor único não é elegância — é o processo sobreviver à travessia.\n\n" +
    "Honestidade: journey no produto é PARCIAL (espinha e merge validados; drill N3 e Cliente 360 pendentes)."
  );
}

// =========================================================================
// 11 — LICENCIAMENTO
// =========================================================================
{
  const s = lightSlide("Modelo de negócio caindo da arquitetura", "Consequência 4");
  s.addText(
    "Se humano e IA disputam os mesmos slots da mesma fila, a métrica natural é agentes simultâneos logados — " +
    "os dois na mesma unidade. Não é decisão comercial colada por cima.",
    {
      x: M, y: 1.68, w: 11.9, h: 0.58,
      fontFace: BFONT, fontSize: 14, color: GRAPHITE_2, margin: 0, lineSpacingMultiple: 1.15,
    }
  );

  const rows = [
    [
      { text: "Produto", options: { bold: true, color: WHITE, fill: { color: GRAPHITE } } },
      { text: "Variáveis de custo", options: { bold: true, color: WHITE, fill: { color: GRAPHITE } } },
      { text: "Previsibilidade", options: { bold: true, color: WHITE, fill: { color: GRAPHITE } } },
    ],
    ["Agentforce", "Flex Credits (US$ 0,10/ação; US$ 0,15/ação de voz) ou ~US$ 2/conversa; EE obrigatória", "Baixa"],
    ["Gemini Enterprise", "US$ 21–60/usuário + tokens + compute + indexação", "Muito baixa"],
    ["Genesys", "US$ 75–155/seat + AI tokens por consumo, com overage em arrears", "Média"],
    ["NICE Mpower", "US$ 71–249/seat + uso por sessão de Autopilot/Copilot + add-ons", "Média"],
    ["Fin / Sierra / Decagon", "Por outcome ou por conversa — a fatura cresce conforme a IA melhora", "Baixa"],
    [
      { text: "PlugHub", options: { bold: true, color: WHITE, fill: { color: GRAPHITE } } },
      { text: "Licenças simultâneas (humanos + IA)", options: { bold: true, color: WHITE, fill: { color: GRAPHITE } } },
      { text: "Alta", options: { bold: true, color: WHITE, fill: { color: GRAPHITE } } },
    ],
  ];
  s.addTable(rows, {
    x: M, y: 2.42, w: 11.9,
    colW: [2.65, 6.85, 2.4],
    fontFace: BFONT, fontSize: 12, color: GRAPHITE_2,
    border: { type: "solid", color: "DDDDDD", pt: 1 },
    fill: { color: WHITE },
    rowH: 0.42,
    valign: "middle",
    margin: 0.08,
  });
  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 5.62, w: 11.9, h: 0.82, rectRadius: 0.06, fill: { color: OFFWHITE },
  });
  s.addText(
    "Inversão de incentivo: no modelo por outcome, quanto melhor a IA fica, mais o cliente paga. " +
    "Por capacidade, o ganho de eficiência fica com o cliente.",
    {
      x: M + 0.38, y: 5.62, w: 11.2, h: 0.82,
      fontFace: BFONT, fontSize: 13.5, bold: true, color: GRAPHITE, valign: "middle", margin: 0,
    }
  );
  s.addNotes(
    "Dados de pricing verificados em julho/2026 — são preço de tabela; TCO real costuma ser 2–3×.\n\n" +
    "'Bill shock' é o principal problema de adoção documentado do Agentforce, que passou por três overhauls de pricing em 18 meses. " +
    "Aqui é impossível por design."
  );
}

// =========================================================================
// 12 — JUSTIFICATIVAS INDEPENDENTES
// =========================================================================
{
  const s = lightSlide("Justificativas independentes da inversão", "Não derivam da co-presença");
  const cw = 3.72, gap = 0.37;
  card(s, {
    x: M, y: 1.72, w: cw, h: 2.5, accent: CHERRY,
    title: "Guard de MCP por invariante",
    body: "Incumbentes protegem antes do LLM (Trust Layer, Model Armor). Aqui é em cada chamada de ferramenta: permissão, injeção, audit — política na ferramenta, o chamador não pode optar por sair.",
  });
  card(s, {
    x: M + cw + gap, y: 1.72, w: cw, h: 2.5, accent: CHERRY,
    title: "Avaliação amarrada ao deploy",
    body: "Monitoria amostra 2–5%, é subjetiva e não responde “a qualidade caiu depois que mexemos no bot?”. Exige carimbo no substrato — quem não carimbou não reconstrói depois.",
  });
  card(s, {
    x: M + 2 * (cw + gap), y: 1.72, w: cw, h: 2.5, accent: CHERRY,
    title: "Motor único declarativo",
    body: "Incumbentes têm 3–5 motores com config, billing e times separados. A prova aqui: o arco de outbound foi entregue sobre o mesmo motor, sem stack paralela.",
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 4.42, w: 11.9, h: 1.9, rectRadius: 0.06, fill: { color: GRAPHITE },
  });
  s.addText("Por que isto ficou MAIS relevante em 2026, não menos", {
    x: M + 0.45, y: 4.62, w: 11, h: 0.34,
    fontFace: BFONT, fontSize: 12, bold: true, color: CHERRY, charSpacing: 1.1, margin: 0,
  });
  const stats = [
    ["11–14%", "dos pilotos de MCP chegam à produção — travados por identidade, auditabilidade e lock-in"],
    [">40%", "dos projetos de IA agêntica podem ser cancelados até 2027 por governança fraca (Gartner)"],
    ["02/08/2026", "EU AI Act com obrigações de alto risco exigíveis; gateways MCP sobre dado regulado no escopo"],
  ];
  stats.forEach((st, i) => {
    const x = M + 0.45 + i * 3.72;
    s.addText(st[0], {
      x, y: 5.02, w: 3.5, h: 0.46,
      fontFace: HFONT, fontSize: 26, bold: true, color: WHITE, margin: 0,
    });
    s.addText(st[1], {
      x, y: 5.5, w: 3.5, h: 0.72,
      fontFace: BFONT, fontSize: 11, color: LIGHTTXT, margin: 0, lineSpacingMultiple: 1.1,
    });
  });
  s.addNotes(
    "IMPORTANTE: 'temos MCP nativo' MORREU como diferencial. ~97M downloads/mês, +10.000 servidores, Linux Foundation. " +
    "Qualquer material que ainda diga isso queima credibilidade — corrigir onde aparecer.\n\n" +
    "O que não virou commodity é o guard obrigatório por chamada. O mercado está indo para " +
    "'tool governance e observability como table-stakes' — que é exatamente esta posição."
  );
}

// =========================================================================
// 13 — O QUE NÃO JUSTIFICA
// =========================================================================
{
  const s = lightSlide("O que não justifica — e vale admitir", "Honestidade estrutural");

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 1.75, w: 5.4, h: 2.6, rectRadius: 0.06, fill: { color: OFFWHITE },
  });
  s.addText("~70%", {
    x: M + 0.4, y: 2.05, w: 4.6, h: 0.95,
    fontFace: HFONT, fontSize: 64, bold: true, color: MUTED, margin: 0,
  });
  s.addText("do esforço de engenharia", {
    x: M + 0.4, y: 3.02, w: 4.6, h: 0.34,
    fontFace: BFONT, fontSize: 15, bold: true, color: GRAPHITE, margin: 0,
  });
  s.addText(
    "Canais e voz, console, dashboards, relatórios, ABAC, agenda, formulários, i18n, importadores.",
    {
      x: M + 0.4, y: 3.42, w: 4.6, h: 0.72,
      fontFace: BFONT, fontSize: 12.5, color: GRAPHITE_2, margin: 0, lineSpacingMultiple: 1.14,
    }
  );

  s.addText("Justificam instrumentalmente:", {
    x: M + 6.15, y: 1.82, w: 5.75, h: 0.34,
    fontFace: BFONT, fontSize: 12, bold: true, color: TEAL, charSpacing: 1.1, margin: 0,
  });
  const inst = [
    "Sem console e canais reais, não se prova a co-presença",
    "Sem relatório, não se prova que o AHT ficou verdadeiro",
  ];
  inst.forEach((t, i) => {
    const y = 2.24 + i * 0.82;
    s.addShape(pres.ShapeType.roundRect, {
      x: M + 6.15, y, w: 5.75, h: 0.68, rectRadius: 0.06, fill: { color: OFFWHITE },
    });
    s.addText(t, {
      x: M + 6.43, y, w: 5.2, h: 0.68,
      fontFace: BFONT, fontSize: 12.5, color: GRAPHITE_2, valign: "middle", margin: 0,
    });
  });
  s.addText(
    "São o custo de entrada da aposta — não a aposta.",
    {
      x: M + 6.15, y: 3.95, w: 5.75, h: 0.4,
      fontFace: HFONT, fontSize: 17, bold: true, color: GRAPHITE, margin: 0,
    }
  );

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 4.68, w: 11.9, h: 1.6, rectRadius: 0.06, fill: { color: CHERRY },
  });
  s.addText("A regra que sai daqui", {
    x: M + 0.45, y: 4.9, w: 11, h: 0.32,
    fontFace: BFONT, fontSize: 12, bold: true, color: "F7D8DC", charSpacing: 1.1, margin: 0,
  });
  s.addText(
    "Nenhum destes aparece como diferencial em material de posicionamento. Listá-los é o erro que faz uma tese " +
    "forte parecer mais um CCaaS — e sinaliza que quem apresenta não distingue fundação de acabamento.",
    {
      x: M + 0.45, y: 5.26, w: 11, h: 0.85,
      fontFace: BFONT, fontSize: 14.5, color: WHITE, margin: 0, lineSpacingMultiple: 1.16,
    }
  );
  s.addNotes(
    "Slide contra-intuitivo e o mais importante do deck depois da inversão.\n\n" +
    "Admitir os 70% é o que torna crível a lista curta que justifica. Numa plateia técnica cética, " +
    "este slide compra mais credibilidade que todos os anteriores somados."
  );
}

// =========================================================================
// 14 — A RESPOSTA EM UMA PÁGINA
// =========================================================================
{
  const s = lightSlide("Os problemas estão contornados, não resolvidos", "A resposta em uma página");
  const rows = [
    [
      { text: "Dor", options: { bold: true, color: WHITE, fill: { color: GRAPHITE } } },
      { text: "Contorno normalizado hoje", options: { bold: true, color: WHITE, fill: { color: GRAPHITE } } },
      { text: "O que a inversão permite", options: { bold: true, color: WHITE, fill: { color: GRAPHITE } } },
    ],
    ["Dado sensível", "Transfere para URA, pausa gravação ou o agente ouve o PAN", "Delegação com progresso visível e dado invisível"],
    ["Wrap-up", "Infla o AHT e bloqueia o agente", "Contato fecha quando o cliente sai; disposição vira item de fila"],
    ["Qualidade", "Amostra 2%, subjetiva, contestação sem trilha", "Cobertura ampla, critérios determinísticos na nota, lente por deploy"],
    ["Processo", "Seis contatos, seis registros, nenhum dono", "SLA por etapa, contexto atravessando contatos, roteador ciente"],
    ["Copilot", "Barra lateral que o agente ignora", "Especialista que participa; mesmo artefato para robô e humano"],
    ["Inbound × outbound", "Dois times, dois sistemas, contexto perdido", "Mesmo motor, mesmo primitivo, mesma licença"],
    ["Governança", "Guard antes do LLM, audit opcional", "Guard por chamada, audit não-optável, vale para agente externo"],
    ["Custo", "Consumo opaco; fatura cresce conforme a IA melhora", "Licença por concorrência; eficiência fica com o cliente"],
  ];
  s.addTable(rows, {
    x: M, y: 1.72, w: 11.9,
    colW: [2.2, 4.85, 4.85],
    fontFace: BFONT, fontSize: 11.5, color: GRAPHITE_2,
    border: { type: "solid", color: "DDDDDD", pt: 1 },
    fill: { color: WHITE },
    rowH: 0.5,
    valign: "middle",
    margin: 0.08,
  });
  s.addText(
    "Cada linha do meio existe porque a arquitetura embaixo não comporta a solução direta.",
    {
      x: M, y: 6.44, w: 11.9, h: 0.36,
      fontFace: HFONT, fontSize: 16, bold: true, color: GRAPHITE, margin: 0,
    }
  );
  s.addNotes(
    "Se o tempo apertar, este slide sozinho carrega o argumento. É o resumo executivo visual.\n\n" +
    "Fechar com a frase: cria-se produto novo quando a limitação é fundacional, não funcional."
  );
}

// =========================================================================
// 15 — COMO A APOSTA PODE ESTAR ERRADA
// =========================================================================
{
  const s = darkSlide();
  s.addText("CONTRA-ARGUMENTOS", {
    x: M, y: 0.62, w: 8, h: 0.32,
    fontFace: BFONT, fontSize: 11, bold: true, color: CHERRY, charSpacing: 1.4, margin: 0,
  });
  s.addText("Como a aposta pode estar errada", {
    x: M, y: 1.0, w: 11.9, h: 0.66,
    fontFace: HFONT, fontSize: 33, bold: true, color: WHITE, margin: 0,
  });

  const risks = [
    ["A assimetria pode não importar tanto", "Se a maioria das operações se satisfaz com deflexão simples e handoff limpo, a co-presença é engenharia cara para ganho marginal. O teste que decide: o mercado compra processo ou deflexão barata?"],
    ["Os incumbentes podem refazer a fundação primeiro", "Têm capital, distribuição e base instalada; falta-lhes vontade de reescrever e o custo de canibalizar SKUs. Janela estimada: 12–18 meses. É estreita."],
  ];
  risks.forEach((r, i) => {
    const y = 2.0 + i * 1.28;
    marker(s, M, y, String(i + 1), CHERRY);
    s.addText(r[0], {
      x: M + 0.72, y: y - 0.06, w: 11.1, h: 0.36,
      fontFace: BFONT, fontSize: 16, bold: true, color: WHITE, margin: 0,
    });
    s.addText(r[1], {
      x: M + 0.72, y: y + 0.32, w: 11.1, h: 0.75,
      fontFace: BFONT, fontSize: 13, color: LIGHTTXT, margin: 0, lineSpacingMultiple: 1.14,
    });
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 4.62, w: 11.9, h: 2.05, rectRadius: 0.06, fill: { color: CHERRY },
  });
  s.addText("E o risco que não é da tese — é de estágio", {
    x: M + 0.45, y: 4.84, w: 11, h: 0.36,
    fontFace: BFONT, fontSize: 13, bold: true, color: "F7D8DC", margin: 0,
  });
  s.addText(
    "Os concorrentes são produtos em produção com milhares de clientes. O PlugHub está em desenvolvimento ativo, " +
    "validado por smoke tests e ambiente demo — sem deployment enterprise e sem certificações. " +
    "Toda comparação de capacidade aqui é real; nenhuma comparação de tração favorece o PlugHub hoje.",
    {
      x: M + 0.45, y: 5.24, w: 11, h: 1.2,
      fontFace: BFONT, fontSize: 14, color: WHITE, margin: 0, lineSpacingMultiple: 1.18,
    }
  );
  s.addNotes(
    "Não pular este slide, mesmo internamente — especialmente internamente.\n\n" +
    "Duas implicações práticas: (1) certificações (SOC 2, ISO 27001, LGPD) são PRÉ-REQUISITO literal, não roadmap — " +
    "todo o discurso de 'auditável' depende delas e o EU AI Act as torna condição de operação. " +
    "(2) A imaturidade vira ativo se for declarada: o comprador que gosta de construir recebe influência sobre o roadmap " +
    "e atenção que não compraria da NICE. Declarar cedo qualifica rápido."
  );
}

// =========================================================================
// 16 — REGRAS DERIVADAS / FECHO
// =========================================================================
{
  const s = darkSlide();
  s.addShape(pres.ShapeType.ellipse, {
    x: 9.4, y: 3.6, w: 6.2, h: 6.2, fill: { color: GRAPHITE_2 },
  });
  s.addText("Três regras que saem deste documento", {
    x: M, y: 0.85, w: 10.5, h: 0.68,
    fontFace: HFONT, fontSize: 31, bold: true, color: WHITE, margin: 0,
  });

  const rules = [
    ["Toda nova capacidade passa pelo teste", "Se a Genesys pode lançar no trimestre que vem, construa se for necessário — mas não a apresente como razão de existir."],
    ["Nunca abrir por lista de features", "A ordem é: dor normalizada → por que é contorno → qual decisão de arquitetura permite a solução direta."],
    ["Declarar o custo de entrada", "Admitir os 70% que não justificam é o que torna crível a lista curta que justifica."],
  ];
  rules.forEach((r, i) => {
    const y = 2.1 + i * 1.3;
    marker(s, M, y, String(i + 1), CHERRY);
    s.addText(r[0], {
      x: M + 0.72, y: y - 0.06, w: 8.3, h: 0.36,
      fontFace: BFONT, fontSize: 16, bold: true, color: WHITE, margin: 0,
    });
    s.addText(r[1], {
      x: M + 0.72, y: y + 0.32, w: 8.3, h: 0.8,
      fontFace: BFONT, fontSize: 13, color: LIGHTTXT, margin: 0, lineSpacingMultiple: 1.14,
    });
  });

  s.addShape(pres.ShapeType.rect, {
    x: M, y: 6.15, w: 1.5, h: 0.045, fill: { color: CHERRY },
  });
  s.addText("Cria-se produto novo quando a limitação é fundacional, não funcional.", {
    x: M, y: 6.4, w: 11, h: 0.42,
    fontFace: HFONT, fontSize: 18, italic: true, color: WHITE, margin: 0,
  });
  s.addNotes(
    "Fechar aqui. Não é roteiro de venda — é o argumento de existência, e serve para alinhar time, " +
    "sustentar decisão de roadmap e responder à pergunta cética.\n\n" +
    "Detalhamento completo em docs/product/por-que-plughub-existe.md."
  );
}

pres.writeFile({ fileName: "por-que-plughub-existe.pptx" }).then(() => {
  console.log("OK: por-que-plughub-existe.pptx");
});
