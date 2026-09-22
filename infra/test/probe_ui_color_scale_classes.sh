#!/usr/bin/env bash
# probe_ui_color_scale_classes.sh — PUI-03 (2026-09-22)
#
# PERGUNTA: o platform-ui usa alguma classe de cor que NÃO GERA CSS?
#
# O `tailwind.config.ts` redefine famílias da paleta padrão (hoje gray, red, green) como cor
# ÚNICA, o que apaga a escala: `bg-gray-900` não existe no CSS construído. O elemento fica sem a
# cor e nada fica vermelho — duas vezes foi um controle branco no branco com a área de clique
# intacta (Save draft do DialogForms, 2026-08-28; controles de mídia do Console, WCH-07).
#
# As famílias são DERIVADAS do config (`_ui_color_scale_census.py`). Estático: não precisa de
# stack de pé.
#
# SAÍDA: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO
# Bateria de mutação: infra/test/mut_ui_color_scale_classes.sh
set -u
cd "$(dirname "$0")/../.." || exit 2
echo "════════════════════════════════════════════════════════════════════"
echo " classes de cor que nao geram CSS no platform-ui (PUI-03)"
echo "════════════════════════════════════════════════════════════════════"
if ! command -v python3 >/dev/null 2>&1; then
  echo "  INCONCL python3 ausente"; echo " INCONCLUSIVO"; exit 2
fi
python3 infra/test/_ui_color_scale_census.py
rc=$?
case $rc in
  0) echo " VERDE" ;;
  1) echo " VERMELHO" ;;
  *) echo " INCONCLUSIVO"; rc=2 ;;
esac
exit $rc
