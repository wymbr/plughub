#!/usr/bin/env bash
# probe_agent_event_tree_rollup.sh
#
# D10 do `adr-dialog-tree-options` — o endpoint `/reports/agent-events/tree`.
#
# PROPOSICOES, uma por ramo:
#   B  a aritmetica do rollup fecha: marcacoes de uma pasta = proprio + soma dos filhos
#   C  `branch_contacts` NAO e a soma dos filhos — e por isso que a coluna existe
#   D  `own` de uma pasta aparece em coluna PROPRIA, nao dissolvido no ramo
#   E  raiz irma nao vaza, e `root` ausente e 422 (nunca um default)
#
# Por que B nao e obvio: `count()` por prefixo so fecha porque cada evento e expandido
# em TODOS os seus ancestrais (`arrayJoin`). Trocar o `arrayJoin` por um `startsWith`
# por nivel, ou esquecer o proprio no na expansao, produz numeros que continuam
# plausiveis e nao fecham — e ninguem confere soma de arvore a olho.
#
# Por que C precisa de TESTEMUNHA: `branch_contacts <= branch_marks` sozinho e verdade
# barata (vale ate se os dois forem sempre iguais). O ramo so passa se existir ao menos
# um no onde eles DIVERGEM; sem esse caso, ele se declara INCONCLUSIVO — porque a
# proposicao "nao e soma" nao foi exercida, e verde ali seria verde por ausencia.
set -u

API="${PLUGHUB_ANALYTICS_URL:-http://localhost:3500}"
TOK="${ANALYTICS_SERVICE_TOKEN:-changeme_analytics_service_token_demo}"
TENANT="${PLUGHUB_TENANT_ID:-tenant_demo}"
ROOT="${PLUGHUB_TREE_ROOT:-retencao_humano.wrapup.servico}"

FAIL=0
INCONC=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=1; }
info() { printf '  \033[33m•\033[0m %s\n' "$1"; }

printf '\033[1mprobe_agent_event_tree_rollup — a arvore fecha, e contatos nao somam\033[0m\n\n'

BODY=$(curl -s "${API}/reports/agent-events/tree?tenant_id=${TENANT}&root=${ROOT}" \
       -H "X-Service-Token: ${TOK}")

printf '\033[1mA — controle positivo: a arvore respondeu com nos\033[0m\n'
N=$(printf '%s' "$BODY" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(len(d.get("data") or []))' 2>/dev/null)
if [ -z "$N" ]; then
  bad "resposta ilegivel — INCONCLUSIVO, e inconclusivo NAO e verde"
  printf '\n\033[31m\033[1mINCONCLUSIVO\033[0m\n'; exit 1
fi
if [ "$N" = "0" ]; then
  info "arvore vazia sob ${ROOT} — SEM AMOSTRA"
  info "rode um wrap-up e repita; verde por ausencia seria mentira"
  printf '\n\033[33m\033[1mINCONCLUSIVO\033[0m — sem populacao para julgar.\n'; exit 1
fi
ok "${N} no(s) sob ${ROOT}"

# ── B, C, D em um passe sobre a resposta ─────────────────────────────────────
printf '\n\033[1mB/C/D — aritmetica, nao-aditividade e a coluna propria\033[0m\n'
printf '%s' "$BODY" | python3 -c '
import sys, json
d = json.load(sys.stdin)
nos = {r["prefix"]: r for r in d["data"]}
prof = lambda p: p.count(".") + 1

def pai(p):
    i = p.rfind(".")
    return p[:i] if i > 0 else None

# ── B: pasta = proprio + soma dos filhos DIRETOS ────────────────────────────
filhos = {}
for p in nos:
    q = pai(p)
    if q in nos:
        filhos.setdefault(q, []).append(p)

quebras = []
for p, r in nos.items():
    ch = filhos.get(p, [])
    if not ch:
        continue
    soma = r["own"] + sum(nos[c]["branch_marks"] for c in ch)
    if soma != r["branch_marks"]:
        quebras.append((p, r["branch_marks"], soma))
print("B|" + ("ok" if not quebras else "erro") + "|" + json.dumps(quebras))

# ── C: existe no onde contatos < marcacoes? (testemunha da nao-aditividade) ──
maior = [p for p, r in nos.items() if r["branch_contacts"] > r["branch_marks"]]
divergem = [(p, nos[p]["branch_marks"], nos[p]["branch_contacts"])
            for p in nos if nos[p]["branch_contacts"] < nos[p]["branch_marks"]]
print("C|" + ("erro" if maior else ("ok" if divergem else "sem_testemunha"))
      + "|" + json.dumps(divergem[:3] + [["MAIOR_QUE_MARCACOES"] + maior[:2]] if maior else divergem[:3]))

# ── D: pasta com own > 0 e visivel na coluna propria ────────────────────────
pastas_com_own = [(p, nos[p]["own"], nos[p]["branch_marks"])
                  for p in nos if not nos[p]["derived_leaf"] and nos[p]["own"] > 0]
print("D|ok|" + json.dumps(pastas_com_own[:3]))
print("META|" + json.dumps(d.get("meta", {}).get("single_vocabulary")))
' > /tmp/tree_probe.txt 2>/dev/null || { bad "falha ao analisar a resposta"; }

while IFS='|' read -r RAMO ST PAYLOAD; do
  case "$RAMO" in
    B) if [ "$ST" = "ok" ]; then ok "aritmetica fecha em toda pasta (proprio + filhos = ramo)"
       else bad "rollup NAO fecha: ${PAYLOAD}"; fi ;;
    C) case "$ST" in
         ok)  ok "ha no onde contatos < marcacoes — nao-aditividade EXERCIDA: ${PAYLOAD}" ;;
         erro) bad "contatos MAIOR que marcacoes — impossivel: ${PAYLOAD}" ;;
         *)   info "nenhum no com contatos < marcacoes nesta janela"
              info "a proposicao 'contatos nao somam' NAO foi exercida — marque 2 folhas na mesma pasta"
              # Sem testemunha o ramo C nao mediu nada: uniqExact trocado por
              # count() produziria exatamente este estado, e verde aqui compraria
              # confianca sem dar nada. INCONCLUSIVO e desfecho proprio, nao verde.
              INCONC=1 ;;
       esac ;;
    D) if [ "$PAYLOAD" = "[]" ]; then info "nenhuma pasta com \`own\` > 0 na janela (nada a denunciar)"
       else ok "pasta com resposta parada nela aparece em coluna propria: ${PAYLOAD}"; fi ;;
    META) [ "$ST" = "false" ] && info "janela com MAIS DE UM vocabulario — o endpoint declara (single_vocabulary=false)" ;;
  esac
done < /tmp/tree_probe.txt

# ── E: isolamento da raiz e obrigatoriedade ──────────────────────────────────
printf '\n\033[1mE — raiz irma nao vaza, e `root` nao tem default\033[0m\n'
CODE=$(curl -s -o /dev/null -w '%{http_code}' \
       "${API}/reports/agent-events/tree?tenant_id=${TENANT}" -H "X-Service-Token: ${TOK}")
if [ "$CODE" = "422" ]; then
  ok "sem \`root\` responde 422 — floresta somada nao e arvore"
else
  bad "sem \`root\` respondeu ${CODE}; um default aqui somaria taxonomias sem raiz comum"
fi

VAZA=$(curl -s "${API}/reports/agent-events/tree?tenant_id=${TENANT}&root=${ROOT%?}" \
       -H "X-Service-Token: ${TOK}" \
       | python3 -c 'import sys,json;print(len(json.load(sys.stdin).get("data") or []))' 2>/dev/null)
if [ "${VAZA:-0}" = "0" ]; then
  ok "raiz truncada (${ROOT%?}) nao casa nada — o ponto separa, e prefixo cru nao"
else
  bad "raiz truncada casou ${VAZA} no(s): o filtro esta usando prefixo SEM o ponto"
fi

printf '\n'
if [ "$FAIL" != "0" ]; then
  printf '\033[31m\033[1mVERMELHO\033[0m\n'; exit 1
elif [ "$INCONC" != "0" ]; then
  printf '\033[33m\033[1mINCONCLUSIVO\033[0m — o ramo C nao teve caso para exercer.\n'; exit 1
else
  printf '\033[32m\033[1mVERDE\033[0m — o rollup fecha e a nao-aditividade e visivel.\n'; exit 0
fi
