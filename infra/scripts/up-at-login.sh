#!/usr/bin/env bash
# infra/scripts/up-at-login.sh
#
# BOO-01 (2026-09-23) — sobe a stack demo depois de reiniciar a máquina, pelo MESMO
# caminho da subida manual: espera o Docker Engine responder e chama o `up.sh`.
#
# POR QUE EXISTE
# ==============
# Depois de reiniciar, nada volta sozinho, e isso foi medido: o Docker Desktop tem
# AutoStart desligado, e mesmo aberto o dockerd só religa pela política de restart
# — 24 containers da stack (toda a base) não têm política, e os `on-failure` não
# religam depois de uma saída limpa. Religar pelo daemon, ou por `compose start`,
# ignora `depends_on`/health; foi assim que o agent-registry morreu em 2026-09-23.
# O único caminho que respeita a ordem do compose é o `up -d` do `up.sh`.
#
# COMO É CHAMADO
# ==============
# Por uma tarefa agendada do Windows no logon do usuário, registrada por
# `infra/scripts/windows/register-up-at-login.ps1` (que também a remove). Rodar na
# mão também funciona, e é equivalente a rodar o `up.sh` depois que o Docker subir.
#
# O QUE ELE NÃO FAZ
# =================
# Não abre o Docker Desktop: o AutoStart é configuração do dono da máquina. Com ele
# desligado, este script ESPERA (até UP_AT_LOGIN_WAIT_S, padrão 1800 s) — abrir o
# Docker Desktop dentro do prazo basta para a stack subir sozinha. Esgotado o prazo,
# o log diz por quê; nada é tentado pela metade.
#
# Log: .logs/up-at-login-<data>.log (o `up.sh` grava o dele à parte, como sempre).
# Saída: a do `up.sh` (0 verde · 1 vermelho · 2 inconclusivo) · 4 = Docker não respondeu.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOG_DIR="$REPO_ROOT/.logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/up-at-login-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

WAIT_S="${UP_AT_LOGIN_WAIT_S:-1800}"
echo "── up-at-login $(date '+%F %T') — esperando o Docker Engine (até ${WAIT_S}s)"

deadline=$(( $(date +%s) + WAIT_S ))
until docker info >/dev/null 2>&1; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "❌ o Docker Engine não respondeu em ${WAIT_S}s — a stack NÃO foi subida."
    echo "   O Docker Desktop está aberto? (AutoStart desligado = ele não abre no logon.)"
    echo "   Abra-o e rode:  bash $REPO_ROOT/infra/scripts/up.sh"
    exit 4
  fi
  sleep 10
done

echo "── Docker respondeu $(date '+%T'); chamando o up.sh"
bash "$REPO_ROOT/infra/scripts/up.sh"
RC=$?
echo "── up-at-login terminou $(date '+%T') com exit ${RC}"
exit "$RC"
