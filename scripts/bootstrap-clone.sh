#!/usr/bin/env bash
#
# bootstrap-clone.sh — config de git que NAO cabe num commit.
#
# Roda uma vez por CLONE (nao por maquina), logo apos o `git clone`. Idempotente:
# rodar de novo so confere e informa.
#
# ── POR QUE ESTE SCRIPT EXISTE, e por que ele nao e o conserto ideal ──────────
#
# O `.gitattributes` resolve finais de linha para qualquer git, em qualquer
# sistema, porque e CONTEUDO: o git o le sozinho. Nao existe equivalente para
# config de repositorio — `core.fileMode` e `safe.directory` sao por clone, e
# nenhum arquivo versionado as carrega. Entao este script e uma PROMESSA
# (alguem precisa lembrar de roda-lo), nao um mecanismo, e o cabecalho diz isso
# em vez de fingir o contrario.
#
# **O conserto de verdade e nao operar um clone do WSL com git de Windows.** Se o
# git rodar so dentro do WSL: `core.fileMode=true` fica correto, `safe.directory`
# fica desnecessario, e CRLF nao acontece. Tudo aqui e COMPENSACAO para a
# realidade de duas toolchains dividindo um clone.
#
# ── O QUE MOTIVOU (medido em 2026-08-28) ─────────────────────────────────────
#
# Repositorio no WSL, operado por binarios de Windows sobre `\wsl.localhost`:
#
#   · o git de Windows NAO enxerga o bit +x neste mount — das 33 mudancas de modo
#     pendentes, 100% eram `755 => 644` e nenhuma no sentido inverso. Ate o
#     `run_gates.sh`, que o `ls` do Git Bash mostra como `-rwxr-xr-x`, o git
#     reporta como nao-executavel NA MESMA SESSAO. Um commit qualquer teria
#     tirado o +x dos 33;
#   · `git init` DE DENTRO do WSL grava `core.fileMode = true`, e la isso esta
#     CERTO (ext4 tem o bit de verdade). Por isso este script MEDE em vez de
#     gravar `false` fixo: gravar `false` num clone que so e usado do WSL
#     desligaria um rastreamento que funciona.
#
# ── COMO A MEDICAO FUNCIONA ──────────────────────────────────────────────────
#
# Cria um repositorio git DESCARTAVEL no mesmo sistema de arquivos, poe nele um
# arquivo com `chmod +x`, e le o modo que o git de fato GRAVOU no index. E o
# unico teste que responde a pergunta que importa — "qual modo este git vai
# gravar aqui?" — em vez de um proxy (`ls`, que discorda dele).
#
# ── A REGRA DE DECISAO E ASSIMETRICA, e essa e a parte que importa ───────────
#
# A primeira versao deste script fazia "mede e aplica", e estava ERRADA. Medido
# no MESMO clone, no mesmo instante:
#
#     de dentro do WSL          -> git grava 100755  (ele ve o bit)
#     por \wsl.localhost (Win)  -> git grava 100644  (nao ve)
#
# Uma execucao so observa o SEU lado. Aplicar o que ela mede faria o script,
# rodado do WSL, devolver `core.fileMode=true` — e religar a bomba: no proximo
# commit feito do Windows, 33 arquivos perderiam o +x.
#
# Para um clone tocado pelos DOIS lados, so `false` e seguro, independentemente
# do que cada lado mede isoladamente. Dai a assimetria:
#
#   · mediu que NAO ve o bit  -> grava `false`. Seguro para todo mundo.
#   · mediu que VE o bit, e ja esta `false` -> **nao mexe**, e explica. Este lado
#     nao tem como saber se outro toca o clone, e so quem sabe disso e uma
#     pessoa.
#   · mediu que VE o bit, e nao esta `false` -> deixa como esta (`true` correto).
#
# E a mesma forma do `resolve_scope` no `plughub_authz`: **o restritivo vence**,
# porque o permissivo degrada em silencio.
#
# Uso:  bash scripts/bootstrap-clone.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

GRN=$'\e[32m'; YLW=$'\e[33m'; RED=$'\e[31m'; BLD=$'\e[1m'; RST=$'\e[0m'
ok()   { echo "  ${GRN}v${RST} $*"; }
mud()  { echo "  ${YLW}~${RST} $*"; }
bad()  { echo "  ${RED}x${RST} $*"; }
info() { echo "    $*"; }

echo "${BLD}bootstrap-clone — config de git que nao viaja no commit${RST}"
echo "  repo: $ROOT"
echo

# ── 1. safe.directory ────────────────────────────────────────────────────────
#
# O git recusa operar num repositorio cujo dono ele nao reconhece — o que
# acontece sempre que o Windows olha um caminho do WSL. A forma canonica do
# caminho (`%(prefix)///wsl.localhost/...`) e chata de montar a mao, entao
# extraimos a linha que o PROPRIO git sugere no erro, em vez de adivinhar.
echo "${BLD}1. safe.directory${RST}"
ERRO="$(git -C "$ROOT" rev-parse --git-dir 2>&1 >/dev/null)"
if printf '%s' "$ERRO" | grep -q "dubious ownership"; then
  SUGERIDO="$(printf '%s' "$ERRO" | sed -n "s/.*--add safe.directory '\(.*\)'.*/\1/p" | head -1)"
  if [ -z "$SUGERIDO" ]; then
    bad "o git recusou o repo por dono, mas nao consegui extrair o caminho sugerido"
    info "Rode 'git -C \"$ROOT\" status' e siga a linha que ele imprime."
  else
    git config --global --add safe.directory "$SUGERIDO"
    mud "adicionado: $SUGERIDO"
    info "(e config GLOBAL — vale para este caminho, em qualquer clone dele)"
  fi
else
  ok "o git ja opera este repo sem reclamar de dono"
fi

# ── 2. core.fileMode — MEDIDO, nunca suposto ─────────────────────────────────
echo
echo "${BLD}2. core.fileMode (medido neste sistema de arquivos)${RST}"
SONDA="$(mktemp -d "$ROOT/.bootstrap-clone.XXXXXX" 2>/dev/null)"
if [ -z "$SONDA" ] || [ ! -d "$SONDA" ]; then
  bad "nao consegui criar diretorio temporario em $ROOT — pulando a medicao"
  info "Sem medir, nao gravo nada: um palpite aqui desliga rastreamento que funciona."
else
  # `-c safe.directory=*` vale SO para estas chamadas; nada e escrito em config.
  G=(git -c "safe.directory=*" -C "$SONDA")
  "${G[@]}" init -q . 2>/dev/null
  printf '#!/bin/sh\nexit 0\n' > "$SONDA/sonda.sh"
  chmod +x "$SONDA/sonda.sh"
  "${G[@]}" add sonda.sh 2>/dev/null
  MODO="$("${G[@]}" ls-files -s sonda.sh 2>/dev/null | awk '{print $1}')"
  rm -rf "$SONDA"

  ATUAL="$(git -C "$ROOT" config --get core.fileMode 2>/dev/null)"
  case "$MODO" in
    100755)
      info "o git GRAVOU 100755 num arquivo com +x -> ele enxerga o bit aqui"
      ALVO="true" ;;
    100644)
      info "o git gravou 100644 num arquivo com +x -> ele NAO enxerga o bit aqui"
      info "(e a assinatura da ponte 9P: \`ls\` mostra +x e o git nao ve)"
      ALVO="false" ;;
    *)
      bad "medicao INCONCLUSIVA (modo lido: '${MODO:-vazio}') — nada foi gravado"
      info "Inconclusivo nao vira default: um palpite aqui custa 33 bits +x."
      ALVO="" ;;
  esac

  # ⚠️ ASSIMETRICO de proposito — ver o cabecalho. `false` vence; NUNCA se volta
  # de `false` para `true` automaticamente, porque este lado nao tem como saber
  # se outro toca o mesmo clone.
  if [ "$ALVO" = "false" ]; then
    if [ "$ATUAL" = "false" ]; then
      ok "core.fileMode ja e false"
    else
      git -C "$ROOT" config core.fileMode false
      mud "core.fileMode: ${ATUAL:-nao-gravado} -> false"
      info "Consequencia aceita: dar +x a um script NOVO passa a exigir"
      info "  git update-index --chmod=+x <arquivo>"
      info "Barato aqui: 209 dos 234 .sh do repo ja sao 100644 e rodam como"
      info "\`bash script.sh\`."
    fi
  elif [ "$ALVO" = "true" ]; then
    if [ "$ATUAL" = "false" ]; then
      ok "core.fileMode fica em false — NAO vou religar"
      info "Este lado enxerga o bit, mas o \`false\` so aparece num clone se alguem"
      info "(ou este script, de outro lado) constatou que ALGUM lado nao enxerga."
      info "Voltar para \`true\` faria o proximo commit vindo do outro lado tirar o"
      info "+x de todo arquivo 100755 do repo."
      info "Se este clone e usado SO daqui, e a decisao e sua:"
      info "  git -C \"$ROOT\" config core.fileMode true"
    else
      ok "core.fileMode = ${ATUAL:-true} (este sistema de arquivos rastreia o bit)"
    fi
  fi
fi

# ── 3. testemunha: a metade VERSIONADA esta valendo? ─────────────────────────
#
# Nao e config — e conferencia. Se o `.gitattributes` nao estiver sendo aplicado,
# os dois primeiros passos consertam metade do problema e o relatorio mentiria
# por omissao.
echo
echo "${BLD}3. .gitattributes (a metade que VIAJA no commit)${RST}"
if [ ! -f "$ROOT/.gitattributes" ]; then
  bad ".gitattributes AUSENTE — finais de linha ficam a merce do core.autocrlf da maquina"
else
  EOL_SH="$(git -C "$ROOT" check-attr eol -- infra/test/run_gates.sh 2>/dev/null | awk '{print $NF}')"
  EOL_PS1="$(git -C "$ROOT" check-attr eol -- scripts/set-env.ps1 2>/dev/null | awk '{print $NF}')"
  if [ "$EOL_SH" = "lf" ] && [ "$EOL_PS1" = "crlf" ]; then
    ok ".sh -> lf   e   .ps1 -> crlf   (as duas pontas, nao so uma)"
    info "Um .sh com CRLF nao roda sob WSL: 'syntax error near unexpected token'."
  else
    bad "regras inesperadas: .sh -> '${EOL_SH:-?}' , .ps1 -> '${EOL_PS1:-?}'"
  fi
fi

echo
echo "${BLD}pendencias que isto NAO resolve${RST}"
info "Ferramenta de Windows que abra arquivo em modo TEXTO continua gravando"
info "CRLF na arvore (Python: use newline=\"\"). O .gitattributes conserta no"
info "commit, mas o arquivo quebra ANTES disso se for um .sh executado no WSL."
echo
