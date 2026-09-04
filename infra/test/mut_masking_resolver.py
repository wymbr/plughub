# -*- coding: utf-8 -*-
"""Bateria de mutacao do gemeo TS de `resolve_mask_for_audience` (CTX-01, 2026-09-04).

Prova que a metade `resolve_cases` de `probe_masking_apply_parity.sh` pode ficar
VERMELHA. Sem isto, um gemeo com os tres ramos trocados passaria: a fixture mede os
DOIS lados contra a mesma entrada, e duas implementacoes igualmente erradas concordam
perfeitamente.

A M1 nao e hipotetica. O gemeo NASCEU com `??` onde o original usa `or`, e os dois so
divergem quando a audiencia esta declarada com string VAZIA. O vetor
`r_customer_VAZIO_cai_no_operator` foi escrito depois de a divergencia aparecer, nao
antes — e e por isso que ele existe na fixture.

Cada mutacao ASSERTA que aplicou antes de rodar o gate. O contrario ja aconteceu neste
repositorio: um `str.replace` cuja ancora nao casava deixou a bateria inteira verde com
a mutacao "aplicada", e o desfecho era indistinguivel de um portao robusto.
"""
import io
import subprocess
import sys

ALVO = "packages/schemas/src/ctx-audience.ts"

MUTACOES = [
    ("M1 `||` volta a ser `??` (nullish x falsy)",
     'const m = byRole[audiencia] || byRole["operator"]',
     'const m = byRole[audiencia] ?? byRole["operator"]'),
    ("M2 `by_role` vazio cai no operator em vez de `plain`",
     'if (Object.keys(byRole).length === 0) return "plain"',
     'if (Object.keys(byRole).length === 0) return "full"'),
    ("M3 tipo ausente vira `plain` em vez de recusar alto",
     'if (!tipoEntry) return "full"',
     'if (!tipoEntry) return "plain"'),
]

orig = io.open(ALVO, encoding="utf-8").read()
falhas = 0
try:
    for nome, de, para in MUTACOES:
        if de not in orig:
            sys.exit("MUTACAO NAO APLICAVEL (ancora ausente): %s" % nome)
        io.open(ALVO, "w", encoding="utf-8", newline="").write(orig.replace(de, para, 1))
        conferido = io.open(ALVO, encoding="utf-8").read()
        if para not in conferido or de in conferido:
            sys.exit("MUTACAO NAO APLICADA: %s" % nome)
        r = subprocess.run(["bash", "infra/test/probe_masking_apply_parity.sh"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            print("  x VERDE COM MUTACAO — %s" % nome)
            falhas += 1
        else:
            linhas = [l for l in r.stdout.splitlines() if "name" in l or "C:" in l]
            print("  v reprovou — %s" % nome)
            for l in linhas[:4]:
                print("       " + l.strip()[:140])
finally:
    io.open(ALVO, "w", encoding="utf-8", newline="").write(orig)

print()
print("BATERIA REPROVADA (%d verdes indevidos)" % falhas if falhas else "BATERIA OK — as 3 mutacoes ficam vermelhas")
sys.exit(1 if falhas else 0)
