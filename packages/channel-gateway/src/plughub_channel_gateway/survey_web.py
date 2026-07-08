"""
survey_web.py — Veículo WEB do survey (dialog primitive, §9.2/§19).

Segundo veículo do primitivo de diálogo: um link tokenizado (entregue por
SMS/e-mail — entrega real é trilha à parte) leva a uma página pública
`/survey/{token}` que renderiza o **mesmo** DialogForm (buscado do dialog-api) e
grava pela **mesma** trilha confiável (evento `session.signals`, idêntico ao que
o `survey_record` publica). O conteúdo (DialogForm) é agnóstico de veículo: o
runner o renderiza como chat; aqui vira uma página `<form>`.

Snapshot: o form publicado é congelado no create (pina a versão). Store = Redis
(`survey_web:token:{token}`), TTL configurável.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer

logger = logging.getLogger("plughub.survey-web")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SurveyLinkDelivery:
    """Pluggable delivery of the survey link (SMS/e-mail). The mock LOGS the link
    (gated by PLUGHUB_SURVEY_LINK_DEV_LOG, default on in demo); a real provider is
    a drop-in that overrides `send`. Delivery is DECOUPLED from token creation so
    the outbound trigger (§19) can re-send the same link. Real SMS/e-mail
    integration é trilha à parte (mesmo item 1 do OTP)."""

    def __init__(self, dev_log: bool | None = None) -> None:
        self._dev_log = (
            dev_log if dev_log is not None
            else os.getenv("PLUGHUB_SURVEY_LINK_DEV_LOG", "true").lower() in ("1", "true", "yes")
        )

    async def send(self, kind: str, address: str, url: str) -> dict[str, Any]:
        # ── Real provider (SMS/e-mail) plugs in HERE ──────────────────────────
        # Until a channel provider is wired, fall back to the dev log / no-op.
        if self._dev_log:
            logger.warning(
                "[SURVEY-LINK-DEV] kind=%s address=%s url=%s (entrega mockada — "
                "wire a provider real p/ produção)", kind, address, url,
            )
            return {"delivered": True, "provider": "mock", "url": url}
        logger.info("[SURVEY-LINK] kind=%s address=%s (entrega real pendente — sem provider)", kind, address)
        return {"delivered": False, "provider": None, "reason": "no_provider"}


# ── Public survey page (self-contained; renders any DialogForm) ───────────────
# Reads the token from the URL, fetches the frozen form, renders statements +
# questions, and submits answers. Same DialogForm content as the chat runner —
# here it becomes a <form> page. No build step: plain HTML/JS.
SURVEY_PAGE_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Pesquisa</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background:#f1f5f9;
         margin:0; padding:24px; color:#0f172a; }
  .card { max-width:560px; margin:24px auto; background:#fff; border-radius:16px;
          box-shadow:0 4px 20px rgba(0,0,0,.08); padding:24px; }
  h1 { font-size:18px; margin:0 0 16px; color:#1B4F8A; }
  .stmt { margin:12px 0; color:#334155; line-height:1.5; }
  .q { margin:20px 0; }
  .q-prompt { font-weight:600; margin-bottom:8px; }
  .opts { display:flex; flex-wrap:wrap; gap:8px; }
  .opt { border:1px solid #cbd5e1; border-radius:10px; padding:8px 14px; cursor:pointer; background:#fff; }
  .opt.sel { background:#1B4F8A; color:#fff; border-color:#1B4F8A; }
  input[type=text], input[type=number], textarea {
    width:100%; border:1px solid #cbd5e1; border-radius:10px; padding:10px; font-size:15px; }
  button.submit { margin-top:20px; background:#1B4F8A; color:#fff; border:none; border-radius:10px;
                  padding:12px 20px; font-size:15px; cursor:pointer; width:100%; }
  button.submit:disabled { opacity:.5; cursor:not-allowed; }
  .done, .err { text-align:center; padding:20px; }
  .done { color:#059669; } .err { color:#DC2626; }
</style>
</head>
<body>
<div class="card" id="root"><div class="stmt">Carregando…</div></div>
<script>
(function () {
  var token = location.pathname.split('/').filter(Boolean).pop();
  var root  = document.getElementById('root');
  var answers = {};
  var guards  = {};   // nodeIndex -> ask_when guard
  var nodeOk  = {};   // nodeIndex -> output_key (for clearing skipped answers)

  // Mirror of @plughub/schemas evaluateAskWhen (adr-dialog-conditional-skip-logic).
  function awNum(x){ return typeof x === 'number' ? x : Number(x); }
  function awEq(a, b){ var na = awNum(a), nb = awNum(b); if (isFinite(na) && isFinite(nb)) return na === nb; return String(a) === String(b); }
  function awEval(g){
    if (!g) return true;
    var a = answers[g.field];
    if (a === undefined || a === null || a === '') return false;
    switch (g.op) {
      case 'lt':  return awNum(a) <  awNum(g.value);
      case 'lte': return awNum(a) <= awNum(g.value);
      case 'gt':  return awNum(a) >  awNum(g.value);
      case 'gte': return awNum(a) >= awNum(g.value);
      case 'eq':  return awEq(a, g.value);
      case 'ne':  return !awEq(a, g.value);
      case 'in':  return Array.isArray(g.value) && g.value.some(function (v) { return awEq(a, v); });
      default:    return false;
    }
  }
  function refresh(){
    Object.keys(guards).forEach(function (i) {
      var el = root.querySelector('[data-node="' + i + '"]');
      if (!el) return;
      var show = awEval(guards[i]);
      el.style.display = show ? '' : 'none';
      if (!show && nodeOk[i]) delete answers[nodeOk[i]];   // skipped ⇒ NA on submit
    });
  }

  function lt(t, dl) {
    if (t == null) return '';
    if (typeof t === 'string') return t;
    return t[dl] || Object.values(t)[0] || '';
  }
  function esc(s){ var d=document.createElement('div'); d.textContent=s==null?'':String(s); return d.innerHTML; }

  function render(form) {
    var dl = form.default_locale || 'pt-BR';
    var h = '<h1>' + esc(form.name || 'Pesquisa') + '</h1>';
    (form.nodes || []).forEach(function (node, i) {
      if (node.ask_when) guards[i] = node.ask_when;
      if (node.kind === 'statement') {
        h += '<div class="stmt" data-node="' + i + '">' + esc(lt(node.text, dl)) + '</div>';
        return;
      }
      var ok = node.output_key;
      nodeOk[i] = ok;
      h += '<div class="q" data-node="' + i + '" data-ok="' + esc(ok) + '">';
      h += '<div class="q-prompt">' + esc(lt(node.prompt, dl)) + '</div>';
      var it = node.interaction;
      if (it === 'button' || it === 'list' || it === 'checklist') {
        h += '<div class="opts">';
        (node.options || []).forEach(function (o) {
          var val = o.value != null ? o.value : o.id;
          h += '<div class="opt" data-ok="' + esc(ok) + '" data-val="' + esc(val) + '">' + esc(lt(o.label, dl)) + '</div>';
        });
        h += '</div>';
      } else {
        h += '<input type="text" data-input="' + esc(ok) + '" />';
      }
      h += '</div>';
    });
    h += '<button class="submit" id="submit-btn">Enviar</button>';
    root.innerHTML = h;

    root.querySelectorAll('.opt').forEach(function (el) {
      el.addEventListener('click', function () {
        var ok = el.getAttribute('data-ok');
        answers[ok] = el.getAttribute('data-val');
        root.querySelectorAll('.opt[data-ok="' + ok + '"]').forEach(function (e2) { e2.classList.remove('sel'); });
        el.classList.add('sel');
        refresh();
      });
    });
    root.querySelectorAll('input[data-input]').forEach(function (el) {
      el.addEventListener('input', function () { answers[el.getAttribute('data-input')] = el.value; refresh(); });
    });
    document.getElementById('submit-btn').addEventListener('click', submit);
    refresh();
  }

  function submit() {
    var btn = document.getElementById('submit-btn');
    btn.disabled = true;
    fetch('/v1/survey/web/' + encodeURIComponent(token) + '/submit', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answers: answers })
    }).then(function (r) { return r.json(); }).then(function (res) {
      if (res && res.ok) root.innerHTML = '<div class="done">✅ Obrigado! Sua resposta foi registrada.</div>';
      else root.innerHTML = '<div class="err">Não foi possível registrar. ' + esc((res && res.reason) || '') + '</div>';
    }).catch(function () { root.innerHTML = '<div class="err">Erro de rede.</div>'; });
  }

  fetch('/v1/survey/web/' + encodeURIComponent(token))
    .then(function (r) { if (!r.ok) throw new Error('not found'); return r.json(); })
    .then(function (data) {
      if (data.status && data.status !== 'open') { root.innerHTML = '<div class="done">Esta pesquisa já foi respondida. Obrigado!</div>'; return; }
      render(data.form || {});
    })
    .catch(function () { root.innerHTML = '<div class="err">Pesquisa não encontrada ou expirada.</div>'; });
})();
</script>
</body>
</html>
"""


class SurveyWebService:
    def __init__(
        self,
        redis:         aioredis.Redis,
        producer:      AIOKafkaProducer,
        dialog_api_url: str,
        signals_topic: str,
        ttl_s:         int = 604800,
        base_url:      str = "",
        delivery:      SurveyLinkDelivery | None = None,
    ) -> None:
        self._redis    = redis
        self._producer = producer
        self._dialog   = dialog_api_url.rstrip("/")
        self._topic    = signals_topic
        self._ttl      = ttl_s
        self._base_url = base_url.rstrip("/")
        self._delivery = delivery or SurveyLinkDelivery()

    def _key(self, token: str) -> str:
        return f"survey_web:token:{token}"

    def _link(self, token: str) -> str:
        path = f"/survey/{token}"
        return f"{self._base_url}{path}" if self._base_url else path

    async def deliver(self, token: str, kind: str, address: str) -> dict[str, Any]:
        """Envia o link de um token existente via a camada plugável de entrega
        (SMS/e-mail). Desacoplado do create → o gatilho outbound (§19) reenvia."""
        return await self._delivery.send(kind, address, self._link(token))

    async def create(
        self,
        tenant_id:         str,
        form_id:           str,
        origin_session_id: str = "",
        customer_key:      str = "",
        deliver_kind:      str = "",
        deliver_address:   str = "",
    ) -> dict[str, Any]:
        """Congela o form publicado num token. Retorna {token, path[, delivery]}.
        Se deliver_kind+deliver_address vierem, entrega o link (camada plugável)."""
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"{self._dialog}/v1/dialog/forms/{form_id}",
                params={"status": "published"},
                headers={"X-Tenant-ID": tenant_id},
            )
            r.raise_for_status()
            form = r.json()

        token  = secrets.token_urlsafe(24)
        record = {
            "tenant_id":         tenant_id,
            "form_id":           form_id,
            "form":              form,
            "origin_session_id": origin_session_id,
            "customer_key":      customer_key,
            "status":            "open",
            "created_at":        _now_iso(),
        }
        await self._redis.set(self._key(token), json.dumps(record), ex=self._ttl)
        result: dict[str, Any] = {"token": token, "path": f"/survey/{token}"}
        if deliver_kind and deliver_address:
            result["delivery"] = await self.deliver(token, deliver_kind, deliver_address)
        return result

    async def get(self, token: str) -> dict[str, Any] | None:
        raw = await self._redis.get(self._key(token))
        return json.loads(raw) if raw else None

    async def submit(self, token: str, answers: dict[str, Any]) -> dict[str, Any]:
        """
        Grava as respostas: monta signals das perguntas com capture.metric + valor
        numérico e publica um `session.signals` (mesma trilha do survey_record).
        Idempotente por status (open → submitted).
        """
        raw = await self._redis.get(self._key(token))
        if not raw:
            return {"ok": False, "reason": "not_found"}
        rec = json.loads(raw)
        if rec.get("status") != "open":
            return {"ok": False, "reason": "already_submitted"}

        form = rec.get("form") or {}
        signals: list[dict[str, Any]] = []
        for node in form.get("nodes", []):
            if not isinstance(node, dict) or node.get("kind") != "question":
                continue
            metric = (node.get("capture") or {}).get("metric")
            if not metric:
                continue
            val = answers.get(node.get("output_key"))
            if val is None or val == "":
                continue
            try:
                num = float(val)
            except (TypeError, ValueError):
                continue  # não-numérica (open_text) → verbatim, não signal
            signals.append({"metric": metric, "value": num})

        if signals:
            event = {
                "event_id":          str(uuid.uuid4()),
                "tenant_id":         rec["tenant_id"],
                "origin_session_id": rec.get("origin_session_id") or token,
                "grain":             "session",
                "segment_id":        None,
                "agent_key":         "",
                "survey_session_id": None,
                "pool_id":           "",
                "signals":           signals,
                "captured_at":       _now_iso(),
            }
            await self._producer.send(self._topic, value=json.dumps(event).encode("utf-8"))

        rec["status"]       = "submitted"
        rec["submitted_at"] = _now_iso()
        await self._redis.set(self._key(token), json.dumps(rec), ex=self._ttl)
        return {"ok": True, "signals_recorded": len(signals)}
