#!/usr/bin/env bash
# probe_attachment_expiry.sh — 2026-09-13  (VOZ-07)
#
# PERGUNTA: anexo vencido deixa de ser servido, e o blob dele sai do store?
#
# O DEFEITO QUE O ORIGINOU
#   O expurgo em dois estagios estava descrito em TRES casas — docstring de
#   `attachment_store.py`, `adr-webchat-channel.md`, `CLAUDE.md` § WebChat — e em
#   nenhum codigo. `soft_expire` nao tinha chamador, nenhum SQL aplicava
#   `expires_at`, e o serving so recusa por `deleted_at`, que ninguem escrevia. O
#   `attachment_expiry_days` editavel na tela carimbava uma data que nada lia:
#   anexo nenhum expirava, e nada ficava vermelho — a ausencia de um job nao gera
#   erro em lugar nenhum.
#
# QUATRO RAMOS
#   A  CENSO — a task `attachment-expiry` nasce supervisionada no boot, e os dois
#      estagios existem no Protocol e nas DUAS implementacoes (o demo roda S3).
#   B  INVARIANTE AO VIVO — nenhuma linha vencida ha mais de 2 h sem `deleted_at`,
#      e nenhuma marcada ha mais de 50 h com blob (24 h de carencia + ate 24 h
#      ate a passada do estagio 2 + folga). ⚠️ Com populacao natural zero o ramo
#      e VACUO, e diz isso: quem prova o mecanismo e o C.
#   C  EXERCICIO NA IMAGEM — `_attachment_expiry_exercise.py` roda o codigo do
#      container contra Postgres e blob store reais, com quatro fixtures: dois
#      casos que TEM de mudar e dois CONTROLES que nao podem (um expurgo que
#      apagasse tudo passaria nos dois primeiros). O "deixa de ser servido" e
#      medido pela ROTA de serving, nao deduzido do `deleted_at`: 410 na vencida,
#      200 na vigente.
#      ⚠️ As fixtures moram no tenant REAL (o serving resolve pelo tenant da
#      instalacao) e sao apagadas por ID, nunca por tenant.
#   D  MUTACAO — (1) o mesmo exercicio sem os estagios TEM de reprovar os dois
#      casos de mudanca; (2) uma linha vencida injetada TEM de acender o contador
#      do ramo B. Sem isso, B e C poderiam estar verdes por nao medirem nada.
#
# ⚠️ O C e o D mexem no store VIVO: as fixtures ficam numa sessao propria e sao
#    apagadas no fim por ID, e a passada de expurgo que o C dispara e a mesma que a task
#    roda de hora em hora — sobre linha real, ela faz o que o produto faria.
#
# EXIT: 0 OK · 1 FALHA · 3 SEM AMOSTRA

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
PG="${PG_CONTAINER:-plughub-demo-postgres-1}"
PGDB="${PGDB:-plughub_demo}"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
psqlq() { docker exec "$PG" psql -U plughub -d "$PGDB" -At -c "$1" 2>&1; }

echo "════════════════════════════════════════════════════════════════════"
echo " anexo vencido deixa de ser servido, e o blob sai do store?"
echo "════════════════════════════════════════════════════════════════════"

# ── A ────────────────────────────────────────────────────────────────────────
echo ""
echo "── A · CENSO ──────────────────────────────────────────────────────────"
SAIDA=$(python3 - <<'PYEOF'
import ast, io
B = "packages/channel-gateway/src/plughub_channel_gateway/"
erros = []
st = ast.parse(io.open(B + "attachment_store.py", encoding="utf-8").read())
cls = {c.name: {f.name for f in c.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
       for c in ast.walk(st) if isinstance(c, ast.ClassDef)}
for c in ("AttachmentStore", "FilesystemAttachmentStore", "S3AttachmentStore"):
    for m in ("expire_due", "purge_deleted"):
        if m not in cls.get(c, set()):
            erros.append("%s sem %s" % (c, m))
mn = ast.parse(io.open(B + "main.py", encoding="utf-8").read())
supervisionada = False
for n in ast.walk(mn):
    if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "supervisionar"
            and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "attachment-expiry"):
        supervisionada = "run_attachment_expiry" in ast.unparse(n)
if not supervisionada:
    erros.append("main.py nao cria `attachment-expiry` sob supervisionar(run_attachment_expiry)")
print("ERRO " + " ; ".join(erros) if erros else "OK")
PYEOF
)
if [ "$SAIDA" = "OK" ]; then
  ok "os dois estagios existem no Protocol e nos dois backends; a task nasce supervisionada"
else
  falha "$SAIDA"
fi

# ── B ────────────────────────────────────────────────────────────────────────
Q_VENCIDA="SELECT count(*) FROM session_attachments WHERE deleted_at IS NULL AND expires_at < NOW() - interval '2 hours'"
Q_BLOB="SELECT count(*) FROM session_attachments WHERE deleted_at < NOW() - interval '50 hours' AND file_path IS NOT NULL"
echo ""
echo "── B · INVARIANTE AO VIVO ─────────────────────────────────────────────"
TOTAL=$(psqlq "SELECT count(*) FROM session_attachments")
VENC=$(psqlq "$Q_VENCIDA")
BLOB=$(psqlq "$Q_BLOB")
JA=$(psqlq "SELECT count(*) FROM session_attachments WHERE deleted_at IS NOT NULL OR expires_at < NOW()")
echo "   populacao: $TOTAL linha(s) · ja venceram alguma vez: $JA"
case "$VENC$BLOB" in
  *[!0-9]*|"") incon "nao consegui ler o Postgres ($PG/$PGDB): vencidas='$VENC' blob='$BLOB'" ;;
  *)
    if [ "$VENC" != "0" ]; then falha "$VENC linha(s) vencida(s) ha mais de 2 h sem deleted_at — o estagio 1 nao esta rodando"
    elif [ "$BLOB" != "0" ]; then falha "$BLOB linha(s) marcada(s) ha mais de 50 h ainda com blob — o estagio 2 nao esta rodando"
    elif [ "$JA" = "0" ]; then ok "invariante vale — VACUAMENTE: nenhuma linha venceu ainda; o C e quem prova"
    else ok "nenhuma vencida sem marca, nenhuma marcada antiga com blob"
    fi ;;
esac

# ── C ────────────────────────────────────────────────────────────────────────
exercicio() { docker exec -i "$GW" python - "$@" < infra/test/_attachment_expiry_exercise.py 2>/dev/null | tail -1; }
echo ""
echo "── C · EXERCICIO NA IMAGEM ────────────────────────────────────────────"
JSON=$(exercicio)
echo "   $JSON"
VERED=$(printf '%s' "$JSON" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    print("SEM"); sys.exit()
if "erro" in d or "casos" not in d:
    print("SEM " + d.get("erro", "sem casos")); sys.exit()
ruins = [k for k, v in d["casos"].items() if not v]
if d.get("limpeza_falhou"):
    ruins.append("limpeza: %s" % d["limpeza_falhou"])
print("OK" if not ruins else "FALHA " + ", ".join(ruins))')
case "$VERED" in
  OK)     ok "vencida marcada e servida como 410 · blob da carencia apagado · vigente intacta e 200 · recente mantida" ;;
  SEM*)   incon "o exercicio nao produziu veredicto: ${VERED#SEM}" ;;
  *)      falha "${VERED#FALHA }" ;;
esac

# ── D ────────────────────────────────────────────────────────────────────────
echo ""
echo "── D · MUTACAO ────────────────────────────────────────────────────────"
JSON=$(exercicio --sem-expurgo)
VERED=$(printf '%s' "$JSON" | python3 -c '
import json, sys
try:
    c = json.loads(sys.stdin.read())["casos"]
except Exception:
    print("SEM"); sys.exit()
mudancas_reprovam = (not c["vencida_marcada"]) and (not c["carencia_blob_apagado"]) and (not c["vencida_serving_410"])
controles_seguem = c["vigente_intacta"] and c["recente_blob_mantido"] and c["vigente_serving_200"]
print("OK" if mudancas_reprovam and controles_seguem else "FALHA %s" % c)')
case "$VERED" in
  OK)   ok "sem os estagios, os dois casos de mudanca reprovam e os controles seguem" ;;
  SEM)  incon "a mutacao do exercicio nao produziu veredicto" ;;
  *)    falha "o exercicio passa sem expurgo — mede a fixture, nao o mecanismo: ${VERED#FALHA }" ;;
esac

MUT_ID=$(psqlq "INSERT INTO session_attachments (file_id, tenant_id, session_id, original_name, mime_type, expires_at) VALUES (gen_random_uuid(), '__probe_voz07_b__', 'probe', 'x.jpg', 'image/jpeg', NOW() - interval '3 hours') RETURNING file_id" | head -1)
VENC_MUT=$(psqlq "$Q_VENCIDA")
psqlq "DELETE FROM session_attachments WHERE tenant_id = '__probe_voz07_b__'" > /dev/null
case "$VENC_MUT" in
  *[!0-9]*|"") incon "mutacao do B sem leitura (id='$MUT_ID' contagem='$VENC_MUT')" ;;
  0)           falha "linha vencida injetada e o contador do B seguiu em 0 — o B nao pode reprovar" ;;
  *)           ok "linha vencida injetada acende o contador do B ($VENC_MUT)" ;;
esac

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " RESULTADO: FALHA em $FALHA verificacao(oes)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " RESULTADO: $INCONCL INCONCLUSIVO(s), nenhum vermelho"; exit 3; fi
echo " RESULTADO: OK"
exit 0
