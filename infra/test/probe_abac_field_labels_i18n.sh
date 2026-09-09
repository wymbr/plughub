#!/usr/bin/env bash
# ==============================================================================
# probe_abac_field_labels_i18n.sh — os rótulos de campo do ABAC passam por t()
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
# Todo campo declarado em `infra/modules.yaml` tem rótulo traduzido nos DOIS
# locales do namespace `access`, e o formulário de permissões realmente resolve
# esse rótulo por `t()` em vez de renderizar o do catálogo cru.
#
# POR QUE ELE EXISTE (AUT-42, 2026-09-09)
# ---------------------------------------
# O `ModulePermissionForm` renderizava `{schema.label}` — o rótulo CRU da API,
# em português, vindo do `infra/modules.yaml`. Duas metades do mesmo cartão
# discordavam de idioma: o nome do MÓDULO já resolvia por i18n
# (`t('moduleNames.<id>', …)`), e os 41 rótulos de CAMPO logo abaixo apareciam em
# português numa tela em inglês. Reportado pelo dono, com a tela em EN.
#
# ⚠️ **A paridade EN × pt-BR não detectaria isto, e é por isso que este gate mede
# outro eixo.** `probe_i18n_contacts_parity.sh` compara os dois locales ENTRE SI:
# antes da correção, nenhum dos dois tinha `fieldNames`, então eles estavam em
# paridade perfeita sobre uma ausência total. E depois da correção a paridade
# seguiria verde no dia em que o catálogo ganhasse um campo novo que faltasse nos
# DOIS. O eixo aqui é CATÁLOGO → locale, não locale → locale.
# (É a regra de método do CLAUDE.md pela enésima vez: um censo desenhado para um
# eixo não prova nada sobre o eixo vizinho.)
#
# O QUE O DEIXARIA VERMELHO
# -------------------------
#   A  campo do catálogo sem chave em `en/access.json`
#   B  campo do catálogo sem chave em `pt-BR/access.json`
#   C  chave `fieldNames` órfã — campo que o catálogo não declara mais
#   D  o formulário volta a renderizar o rótulo CRU do catálogo
#   E  módulo do catálogo sem nome traduzido, ou nome de módulo órfão
#   F  papel sem rótulo traduzido, ou mapa hardcoded contornando o locale
#   G  rótulo do catálogo com `: ` fora de aspas — quebra o YAML no boot
#
# ⚠️ O ramo D é o que impede o gate DECORATIVO. Sem ele, os dois locales podiam
# estar completos e a tela continuar em português — cobertura de tradução não é
# evidência de que alguém a lê. É o mesmo par do controle positivo: o negativo
# sozinho passa pelo motivo errado.
#
# ⚠️ O que ele NÃO checa, de propósito: que o valor pt-BR seja IGUAL ao rótulo do
# YAML. O catálogo é fallback e documentação de quem edita a política; o locale é
# o texto da tela, e ele pode legitimamente ser redigido diferente. Exigir
# igualdade transformaria o locale numa cópia cerimonial. Não "conserte" isto
# apertando para igualdade — a proposição do gate é COBERTURA.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -u

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

CAT=infra/modules.yaml
EN=packages/platform-ui/src/i18n/locales/en/access.json
PT=packages/platform-ui/src/i18n/locales/pt-BR/access.json
FORM=packages/platform-ui/src/components/ModulePermissionForm.tsx
PAGE=packages/platform-ui/src/modules/access/AccessPage.tsx

for f in "$CAT" "$EN" "$PT" "$FORM" "$PAGE"; do
  [ -f "$f" ] || { echo "INCONCLUSIVO: $f ausente"; exit 2; }
done
command -v python3 >/dev/null || { echo "INCONCLUSIVO: python3 ausente"; exit 2; }

printf '\033[1mprobe: os rotulos de campo do ABAC passam por t()\033[0m\n'

python3 - "$CAT" "$EN" "$PT" "$FORM" "$PAGE" <<'PY'
import json, re, sys

cat_p, en_p, pt_p, form_p, page_p = sys.argv[1:6]
fail = 0

def ok(m):  print(f"  \033[32mOK\033[0m           {m}")
def bad(m):
    global fail
    print(f"  \033[31mFALHA\033[0m        {m}"); fail = 1

# ── leitor do catalogo sem PyYAML: so precisamos de module_id + chaves do schema
texto = open(cat_p, encoding="utf-8").read()
campos = set()
mod = None
dentro = False
for linha in texto.split("\n"):
    m = re.match(r"^\s*-\s*module_id:\s*(\S+)", linha)
    if m:
        mod, dentro = m.group(1), False
        continue
    if re.match(r"^\s*permission_schema:\s*$", linha):
        dentro = True
        continue
    if dentro and mod:
        f = re.match(r"^      ([a-z_][a-z0-9_]*):\s*$", linha)
        if f:
            campos.add(f"{mod}.{f.group(1)}")
        elif re.match(r"^  \S", linha) or re.match(r"^\s*-\s", linha):
            dentro = False

# Sem amostra o gate nao mediu nada — verde por ausencia e o modo de falha
# que o CLAUDE.md manda preferir INCONCLUSIVO a aceitar.
if len(campos) < 10:
    print(f"  \033[33mINCONCLUSIVO\033[0m so {len(campos)} campo(s) lidos do catalogo — leitor quebrado?")
    sys.exit(2)

def folhas(caminho):
    d = json.load(open(caminho, encoding="utf-8")).get("fieldNames", {})
    return {f"{m}.{f}" for m, fs in d.items() for f in fs}

en, pt = folhas(en_p), folhas(pt_p)

print(f"\n\033[1mA/B. todo campo do catalogo tem rotulo nos DOIS locales\033[0m")
print(f"               catalogo: {len(campos)} campos · en: {len(en)} · pt-BR: {len(pt)}")
for nome, tem in (("en", en), ("pt-BR", pt)):
    falta = sorted(campos - tem)
    if falta:
        bad(f"{nome}: {len(falta)} campo(s) sem rotulo — a tela cai no catalogo (portugues):")
        for c in falta[:12]:
            print(f"                 {c}")
    else:
        ok(f"{nome}: os {len(campos)} campos do catalogo tem rotulo")

print(f"\n\033[1mC. nenhuma chave orfa (campo que o catalogo nao declara mais)\033[0m")
orfas = sorted((en | pt) - campos)
if orfas:
    bad(f"{len(orfas)} chave(s) orfa(s) — campo removido do catalogo e nao do locale:")
    for c in orfas[:12]:
        print(f"                 {c}")
else:
    ok("nenhuma chave sobrando nos locales")

print(f"\n\033[1mD. o formulario RESOLVE o rotulo (senao a cobertura e decorativa)\033[0m")
src = open(form_p, encoding="utf-8").read()
if "fieldNames.${moduleId}.${fieldKey}" not in src:
    bad("o formulario nao monta a chave `fieldNames.<modulo>.<campo>`")
else:
    ok("o formulario monta a chave por (modulo, campo)")
# ⚠️ COMENTARIO NAO E CODIGO. A primeira versao deste ramo leu o fonte linha a
# linha e reprovou no COMENTARIO que documenta esta propria correcao — o mesmo
# defeito que o CLAUDE.md registra na D14 (*"grep contaria o comentario que
# documenta a migracao"*). Tiramos comentarios antes de julgar.
sem_com = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
sem_com = "\n".join(l for l in sem_com.split("\n")
                    if not l.lstrip().startswith("//"))
# O rotulo cru so pode aparecer como defaultValue — nunca como o que e renderizado.
cru = [l for l in sem_com.split("\n")
       if "schema.label" in l and "defaultValue" not in l]
if cru:
    bad("o rotulo CRU do catalogo voltou a ser renderizado:")
    for l in cru[:5]:
        print(f"                 {l.strip()}")
else:
    ok("`schema.label` so aparece como defaultValue (fallback de campo novo)")

print(f"\n\033[1mE. o NOME DO MODULO e a outra metade do mesmo cartao\033[0m")
# O cartao mostra nome do modulo em cima e os rotulos de campo embaixo. A AUT-42
# so quebrou a metade de baixo, mas as duas resolvem pelo mesmo arquivo e a de
# cima estava completa por ACIDENTE — a MOD-11 tinha acabado de tirar a chave
# orfa de `workflows` na vespera. Sem este ramo, o mesmo defeito volta pela
# metade que ninguem esta olhando.
mods = {c.split(".")[0] for c in campos}
for nome, caminho in (("en", en_p), ("pt-BR", pt_p)):
    mn = set(json.load(open(caminho, encoding="utf-8")).get("moduleNames", {}))
    falta, sobra = sorted(mods - mn), sorted(mn - mods)
    if falta:
        bad(f"{nome}: modulo(s) sem nome traduzido: {', '.join(falta)}")
    elif sobra:
        bad(f"{nome}: nome(s) de modulo orfao(s): {', '.join(sobra)}")
    else:
        ok(f"{nome}: os {len(mods)} modulos do catalogo tem nome, sem sobra")

print(f"\n\033[1mF. o rotulo de PAPEL, na mesma tela e pelo mesmo motivo\033[0m")
# A `AccessPage` tinha um `ROLE_LABELS` HARDCODED que os quatro call sites liam
# em vez do locale — e ele carregava `devops: "Developer"`, o rotulo que a
# MOD-10 aposentou na vespera. Texto hardcoded nao envelhece: ele mente parado.
# Este ramo cobra as duas metades — a chave existe, e ninguem a contorna.
page = open(page_p, encoding="utf-8").read()
m = re.search(r"const ALL_ROLES = \[([^\]]*)\]", page)
if not m:
    bad("nao achei `ALL_ROLES` na AccessPage — leitor quebrado")
else:
    papeis = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    for nome, caminho in (("en", en_p), ("pt-BR", pt_p)):
        rs = set(json.load(open(caminho, encoding="utf-8")).get("roles", {}))
        falta = sorted(papeis - rs)
        if falta:
            bad(f"{nome}: papel(is) sem rotulo traduzido: {', '.join(falta)}")
        else:
            ok(f"{nome}: os {len(papeis)} papeis tem rotulo")
    if "ROLE_LABELS[" in page or "const ROLE_LABELS" in page:
        bad("a AccessPage voltou a ter mapa HARDCODED de rotulo de papel")
    else:
        ok("nenhum mapa hardcoded de rotulo de papel contorna o locale")

print(f"\n\033[1mG. rotulo do catalogo nao quebra o YAML\033[0m")
# ⚠️ Escrito depois de eu quebrar o boot do auth-api, em 2026-09-09, ao dar aos
# rotulos a forma "Monitor: observar ...". Em YAML, escalar NAO-CITADO contendo
# ": " e erro de sintaxe -- `ScannerError`, e o servico morre no startup.
#
# ⚠️ E este probe ficou VERDE com o arquivo quebrado, porque le o catalogo por
# REGEX de propósito (para nao depender de PyYAML). Leitor tolerante e a razao
# de ele nao ter pego: ele nao le YAML, le linhas que se parecem com YAML.
# Este ramo cobra a forma que o leitor tolerante ignora, e sem dependencia nova.
quebrados = []
for n, linha in enumerate(texto.split("\n"), 1):
    m = re.match(r"^\s*label:\s*(.+?)\s*$", linha)
    if not m:
        continue
    v = m.group(1)
    citado = (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'"))
    if ": " in v and not citado:
        quebrados.append((n, v[:60]))
if quebrados:
    bad(f"{len(quebrados)} rotulo(s) com ': ' NAO-CITADO — isto e ScannerError no boot:")
    for n, v in quebrados[:6]:
        print(f"                 modules.yaml:{n}  label: {v}")
else:
    ok("nenhum rotulo com ': ' fora de aspas")

print()
if fail:
    print("\033[31mVERMELHO\033[0m - ver secoes acima.")
else:
    print("\033[32mVERDE\033[0m - os rotulos de campo do ABAC seguem o idioma da tela.")
sys.exit(fail)
PY
exit $?
