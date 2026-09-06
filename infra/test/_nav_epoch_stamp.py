# -*- coding: utf-8 -*-
"""Ramo E do `probe_orchestrator_tree_nav` — le o carimbo de epoca do produtor.

Vive em arquivo proprio porque o gate ja usa heredoc `PY` no ramo D, e um segundo
heredoc aninhado fecha o primeiro: o script sairia mudo, e ramo mudo e INCONCLUSIVO
que parece verde.
"""
import io, sys

try:
    import yaml
except ImportError:
    print("SEM_YAML"); raise SystemExit

d = yaml.safe_load(io.open(sys.argv[1], encoding="utf-8").read())
st = next((s for s in d["steps"] if s["id"] == "registrar_demanda"), None)
if not st:
    print("SEM_STEP"); raise SystemExit

tags = (st.get("input") or {}).get("tags") or {}
tem = "dialog_form_id" in tags and "dialog_form_version" in tags
# O carimbo tem de vir da SAIDA da tool, nunca de literal: um literal pode
# discordar da forma que o cliente realmente viu.
refs = all("$.pipeline_state.nivel." in str(tags.get(k, ""))
           for k in ("dialog_form_id", "dialog_form_version"))
print("%s %s %s" % ("TAGS_OK" if tem else "TAGS_FALTAM",
                    "REF_OK" if refs else "REF_LITERAL",
                    st.get("tool")))
