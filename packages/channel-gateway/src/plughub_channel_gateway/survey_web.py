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
from typing import Any, Awaitable, Callable, Protocol

import httpx
import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer

logger = logging.getLogger("plughub.survey-web")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Link delivery: pluggable provider layer ───────────────────────────────────
# Delivers the survey link (SMS/e-mail) via a provider selected per `kind` from
# per-tenant config (config-api namespace `survey`, key `link_delivery`). Two
# providers ship: `mock` (dev log / no-op, default) and `webhook` (vendor-neutral
# — POSTs to the tenant's own SMS/e-mail gateway). No vendor SDK is hardcoded
# (no-lock-in). Secrets stay in env; the non-secret routing/URL lives in config.
# Delivery is DECOUPLED from token creation so the outbound trigger (§19) can
# (re)send.


class LinkDeliveryProvider(Protocol):
    """A concrete channel that actually sends the link. Returns a result dict
    with at least `delivered: bool` and `provider: str`."""
    name: str

    async def send(self, kind: str, address: str, url: str, tenant_id: str) -> dict[str, Any]: ...


class MockProvider:
    """Default/fallback provider — logs the link (gated by PLUGHUB_SURVEY_LINK_DEV_LOG)
    and reports success without actually sending. Keeps the demo flowing with no
    external dependency."""
    name = "mock"

    def __init__(self, dev_log: bool | None = None) -> None:
        self._dev_log = (
            dev_log if dev_log is not None
            else os.getenv("PLUGHUB_SURVEY_LINK_DEV_LOG", "true").lower() in ("1", "true", "yes")
        )

    async def send(self, kind: str, address: str, url: str, tenant_id: str) -> dict[str, Any]:
        if self._dev_log:
            logger.warning(
                "[SURVEY-LINK-DEV] tenant=%s kind=%s address=%s url=%s (entrega mockada)",
                tenant_id, kind, address, url,
            )
            return {"delivered": True, "provider": "mock", "url": url}
        return {"delivered": False, "provider": "mock", "reason": "dev_log_off"}


# httpx client factory type — injectable so tests can supply a mock transport.
ClientFactory = Callable[[], httpx.AsyncClient]


class WebhookProvider:
    """Vendor-neutral real provider: POSTs {kind, address, url, tenant_id} to a
    tenant-configured HTTP endpoint (their own SMS/e-mail gateway sits behind it).
    Auth via a bearer token kept in env (secret), never in config."""
    name = "webhook"

    def __init__(self, url: str, token: str = "", client_factory: ClientFactory | None = None) -> None:
        self._url = url
        self._token = token
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=5))

    async def send(self, kind: str, address: str, url: str, tenant_id: str) -> dict[str, Any]:
        payload = {"kind": kind, "address": address, "url": url, "tenant_id": tenant_id}
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            async with self._client_factory() as client:
                resp = await client.post(self._url, json=payload, headers=headers)
            if 200 <= resp.status_code < 300:
                return {"delivered": True, "provider": "webhook", "status": resp.status_code}
            logger.warning(
                "[SURVEY-LINK] webhook HTTP %d tenant=%s kind=%s", resp.status_code, tenant_id, kind,
            )
            return {"delivered": False, "provider": "webhook", "status": resp.status_code, "reason": "http_error"}
        except Exception as exc:  # noqa: BLE001 — network/timeout must not break the flow
            logger.warning("[SURVEY-LINK] webhook transport error tenant=%s: %s", tenant_id, exc)
            return {"delivered": False, "provider": "webhook", "reason": "transport_error"}


# Injectable async fetcher (tenant_id) -> link_delivery config dict — for tests.
ConfigFetch = Callable[[str], Awaitable[dict[str, Any]]]


class SurveyLinkDelivery:
    """Router over the providers. Resolves the per-tenant `survey.link_delivery`
    config (config-api HTTP, cached), then picks a provider per `kind`
    (`routes[kind]` → `default_provider` → `mock`). Unknown provider or missing
    webhook URL degrades to the mock — delivery never breaks the survey flow.

    link_delivery config shape (config-api namespace `survey`, key `link_delivery`):
        { "default_provider": "mock"|"webhook",
          "routes":  { "sms": "webhook", "email": "webhook" },
          "webhook": { "url": "https://tenant-gateway/notify" } }
    The webhook auth token is NOT here — it comes from env
    (PLUGHUB_SURVEY_LINK_WEBHOOK_TOKEN), per the config/secret split.
    """

    def __init__(
        self,
        config_api_url: str = "",
        webhook_token:  str = "",
        config_fetch:   ConfigFetch | None = None,
        mock_dev_log:   bool | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._config_api_url = config_api_url.rstrip("/")
        self._webhook_token = webhook_token or os.getenv("PLUGHUB_SURVEY_LINK_WEBHOOK_TOKEN", "")
        self._config_fetch = config_fetch
        self._client_factory = client_factory
        self._mock = MockProvider(dev_log=mock_dev_log)
        self._cache: dict[str, dict[str, Any]] = {}   # tenant_id → link_delivery config

    def invalidate(self, tenant_id: str | None = None) -> None:
        """Drop cached config on config.changed(survey). All tenants if None."""
        if tenant_id:
            self._cache.pop(tenant_id, None)
        else:
            self._cache.clear()

    async def _resolve_config(self, tenant_id: str) -> dict[str, Any]:
        if tenant_id in self._cache:
            return self._cache[tenant_id]
        cfg: dict[str, Any] = {}
        try:
            if self._config_fetch is not None:
                cfg = await self._config_fetch(tenant_id) or {}
            elif self._config_api_url:
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(
                        f"{self._config_api_url}/config/survey", params={"tenant_id": tenant_id},
                    )
                if resp.status_code == 200:
                    body = resp.json()
                    entries = body.get("entries") or body
                    raw = entries.get("link_delivery") if isinstance(entries, dict) else None
                    value = raw["value"] if isinstance(raw, dict) and "value" in raw else raw
                    cfg = value if isinstance(value, dict) else {}
        except Exception as exc:  # noqa: BLE001 — config-api down → mock fallback
            logger.warning("survey link_delivery config fetch failed (%s) — mock fallback", exc)
        self._cache[tenant_id] = cfg
        return cfg

    async def send(self, kind: str, address: str, url: str, tenant_id: str = "") -> dict[str, Any]:
        cfg = await self._resolve_config(tenant_id)
        routes = cfg.get("routes") or {}
        provider_name = routes.get(kind) or cfg.get("default_provider") or "mock"
        if provider_name == "webhook":
            webhook_url = (cfg.get("webhook") or {}).get("url") or ""
            if webhook_url:
                return await WebhookProvider(
                    webhook_url, self._webhook_token, self._client_factory,
                ).send(kind, address, url, tenant_id)
            logger.warning(
                "survey link_delivery: 'webhook' selected but no webhook.url configured — mock fallback",
            )
        return await self._mock.send(kind, address, url, tenant_id)


# ── Journey J4c — collect-based survey page (minimal webchat client) ──────────
# The customer opened the invitation link → the session was just created (routed
# inbound to the survey pool). This page connects as a NORMAL webchat client
# (pre-bound by a JWT carrying session_id) and lets the survey pool's dialog_runner
# render the DialogForm live via `interaction.request` / `menu.submit`.
#
# Why a webchat client and not a standalone <form>: the survey is now a first-class
# routed contact (journey member N1, quota + max_concurrent_sessions + metering all
# enforced on admission). Reusing the webchat transport means ZERO new adapter — the
# single generic runner interprets the config-driven DialogForm, as everywhere else.
SURVEY_COLLECT_PAGE_HTML = """<!DOCTYPE html>
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
  .muted { color:#64748b; font-size:13px; }
  .prompt { font-weight:600; margin:16px 0 12px; line-height:1.5; }
  .opts { display:flex; flex-wrap:wrap; gap:8px; }
  .opt { padding:10px 14px; border:1px solid #cbd5e1; border-radius:10px; background:#fff;
         cursor:pointer; font-size:14px; }
  .opt:hover { border-color:#1B4F8A; color:#1B4F8A; }
  .fld { margin:12px 0; }
  .fld label { display:block; font-size:13px; color:#334155; margin-bottom:4px; }
  .fld input { width:100%; padding:10px; border:1px solid #cbd5e1; border-radius:8px; font-size:14px; }
  button.send { margin-top:16px; width:100%; padding:12px; border:0; border-radius:10px;
                background:#1B4F8A; color:#fff; font-size:15px; font-weight:600; cursor:pointer; }
  .done { text-align:center; padding:24px 0; font-size:16px; color:#059669; font-weight:600; }
  .err  { color:#DC2626; font-size:13px; }
</style>
</head>
<body>
<div class="card">
  <h1>Pesquisa</h1>
  <div id="status" class="muted">Conectando…</div>
  <div id="body"></div>
  <div id="done" class="done" style="display:none">Obrigado pela sua resposta! 🙏</div>
</div>
<script>
  var BOOT = __SURVEY_BOOTSTRAP__;
  var statusEl = document.getElementById('status');
  var bodyEl   = document.getElementById('body');
  var doneEl   = document.getElementById('done');
  var answered = false;

  var proto = (location.protocol === 'https:') ? 'wss://' : 'ws://';
  var ws = new WebSocket(proto + location.host + '/ws/chat/' + encodeURIComponent(BOOT.pool_id));

  function finish() {
    answered = true;
    bodyEl.innerHTML = '';
    statusEl.style.display = 'none';
    doneEl.style.display = 'block';
  }

  function submit(menu, result) {
    ws.send(JSON.stringify({
      type: 'menu.submit', menu_id: menu.menu_id,
      interaction: menu.interaction, result: result
    }));
    finish();
  }

  function render(menu) {
    statusEl.style.display = 'none';
    bodyEl.innerHTML = '';
    var p = document.createElement('div');
    p.className = 'prompt';
    p.textContent = menu.prompt || '';
    bodyEl.appendChild(p);

    // button / list / checklist → option chips
    if (menu.options && menu.options.length) {
      var wrap = document.createElement('div');
      wrap.className = 'opts';
      menu.options.forEach(function (o) {
        var b = document.createElement('button');
        b.className = 'opt';
        b.textContent = o.label || o.id;
        b.onclick = function () { submit(menu, o.id); };
        wrap.appendChild(b);
      });
      bodyEl.appendChild(wrap);
      return;
    }

    // form → fields
    if (menu.fields && menu.fields.length) {
      var inputs = {};
      menu.fields.forEach(function (f) {
        var d = document.createElement('div'); d.className = 'fld';
        var l = document.createElement('label'); l.textContent = f.label || f.id;
        var i = document.createElement('input');
        i.type = (menu.masked_fields || []).indexOf(f.id) >= 0 ? 'password' : 'text';
        d.appendChild(l); d.appendChild(i); bodyEl.appendChild(d);
        inputs[f.id] = i;
      });
      var btn = document.createElement('button');
      btn.className = 'send'; btn.textContent = 'Enviar';
      btn.onclick = function () {
        var res = {};
        Object.keys(inputs).forEach(function (k) { res[k] = inputs[k].value; });
        submit(menu, res);
      };
      bodyEl.appendChild(btn);
      return;
    }

    // free text
    var d2 = document.createElement('div'); d2.className = 'fld';
    var i2 = document.createElement('input'); i2.type = 'text';
    d2.appendChild(i2); bodyEl.appendChild(d2);
    var b2 = document.createElement('button');
    b2.className = 'send'; b2.textContent = 'Enviar';
    b2.onclick = function () { submit(menu, i2.value); };
    bodyEl.appendChild(b2);
  }

  ws.onmessage = function (ev) {
    var m;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    switch (m.type) {
      case 'conn.hello':
        ws.send(JSON.stringify({ type: 'conn.authenticate', token: BOOT.jwt }));
        break;
      case 'conn.authenticated':
        statusEl.textContent = 'Carregando a pesquisa…';
        break;
      case 'interaction.request':
        render(m);
        break;
      case 'conn.session_closed':
      case 'conn.session_ended':
        if (!answered) finish();
        break;
      case 'conn.error':
        statusEl.className = 'err';
        statusEl.textContent = 'Não foi possível abrir a pesquisa (' + (m.code || 'erro') + ').';
        break;
    }
  };
  ws.onerror = function () {
    if (!answered) { statusEl.className = 'err'; statusEl.textContent = 'Falha de conexão.'; }
  };
  ws.onclose = function () { if (answered) return; };
</script>
</body>
</html>
"""


# ── Public survey page (self-contained; renders any DialogForm) ───────────────
# LEGACY / anonymous vehicle (J4b): signal-only, no session. Kept for surveys with
# no known root/customer (unsolicited feedback). The collect-based flow above is
# the one that produces a journey-member contact.
# Reads the token from the URL, fetches the frozen form, renders statements +
# questions, and submits answers. Same DialogForm content as the chat runner —
# here it becomes a <form> page. No build step: plain HTML/JS.
# ⚠️ RAW (`r"""`): esta pagina embute regex JS (`\d`, `\D`), e num literal
# comum cada uma delas e um escape invalido — hoje SyntaxWarning, e o Python
# ja anunciou que vira SyntaxError. Um aviso que ninguem le e o degrau antes
# de um servico que nao sobe.
SURVEY_PAGE_HTML = r"""<!DOCTYPE html>
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
  .fmt-err { color:#b91c1c; font-size:12px; margin-top:4px; }
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
  function awSobPrefixo(caminho, prefixo){ return caminho === prefixo || caminho.indexOf(prefixo + '.') === 0; }
  function awEval(g){
    if (!g) return true;
    var a = answers[g.field];
    if (a === undefined || a === null || a === '') return false;
    if (Array.isArray(a) && a.length === 0) return false;
    var multi = Array.isArray(a);
    var vals = multi ? a : [a];
    switch (g.op) {
      case 'lt':  return multi ? false : awNum(a) <  awNum(g.value);
      case 'lte': return multi ? false : awNum(a) <= awNum(g.value);
      case 'gt':  return multi ? false : awNum(a) >  awNum(g.value);
      case 'gte': return multi ? false : awNum(a) >= awNum(g.value);
      case 'eq':  return vals.some(function (v) { return awEq(v, g.value); });
      case 'ne':  return !vals.some(function (v) { return awEq(v, g.value); });
      case 'in':  return Array.isArray(g.value) && vals.some(function (v) {
                    return g.value.some(function (x) { return awEq(v, x); }); });
      case 'prefix': return vals.some(function (v) { return awSobPrefixo(String(v), String(g.value)); });
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
      nodePorOk[ok] = node;
      h += '<div class="q" data-node="' + i + '" data-ok="' + esc(ok) + '">';
      h += '<div class="q-prompt">' + esc(lt(node.prompt, dl)) + '</div>';
      var it = node.interaction;
      if (it === 'button' || it === 'list' || it === 'checklist') {
        h += '<div class="opts">';
        var multi = it === 'checklist' ? '1' : '0';
        (node.options || []).forEach(function (o) {
          var val = o.value != null ? o.value : o.id;
          h += '<div class="opt" data-ok="' + esc(ok) + '" data-multi="' + multi + '" data-val="' + esc(val) + '">' + esc(lt(o.label, dl)) + '</div>';
        });
        h += '</div>';
      } else {
        // AFORDANCIA — guia a digitacao. Nao autoriza (§D7): o veredicto fala
        // no envio, com a mensagem que o catalogo carrega.
        var ent = entradaDe(node);
        var af  = (ent && ent.affordance) || {};
        var dcl = declDe(node) || {};
        var mx  = af.maxlength !== undefined
          ? (dcl.max_length !== undefined ? Math.min(af.maxlength, dcl.max_length) : af.maxlength)
          : dcl.max_length;
        h += '<input type="text" data-input="' + esc(ok) + '"'
           + (af.mask ? ' data-mask="' + esc(af.mask) + '" placeholder="' + esc(af.mask) + '"' : '')
           + (af.inputmode ? ' inputmode="' + esc(af.inputmode) + '"' : '')
           + (mx !== undefined ? ' maxlength="' + mx + '"' : '')
           + ' />';
        h += '<div class="fmt-err" data-err="' + esc(ok) + '" style="display:none"></div>';
      }
      h += '</div>';
    });
    h += '<button class="submit" id="submit-btn">Enviar</button>';
    root.innerHTML = h;

    root.querySelectorAll('.opt').forEach(function (el) {
      el.addEventListener('click', function () {
        var ok = el.getAttribute('data-ok');
        var val = el.getAttribute('data-val');
        if (el.getAttribute('data-multi') !== '1') {
          answers[ok] = val;
          root.querySelectorAll('.opt[data-ok="' + ok + '"]').forEach(function (e2) { e2.classList.remove('sel'); });
          el.classList.add('sel');
        } else {
          var cur = Array.isArray(answers[ok]) ? answers[ok] : [];
          var at = cur.indexOf(val);
          if (at >= 0) { cur.splice(at, 1); el.classList.remove('sel'); }
          else { cur.push(val); el.classList.add('sel'); }
          // Zero marcacoes REMOVE a chave em vez de gravar `[]`: `awEval` trata
          // ausencia como "nao respondeu" e `[]` nao casa com nenhum teste de
          // vazio, entao um array vazio faria pergunta nao respondida parecer
          // respondida. Mesma regra do renderer do Console.
          if (cur.length) answers[ok] = cur; else delete answers[ok];
        }
        refresh();
      });
    });
    root.querySelectorAll('input[data-input]').forEach(function (el) {
      el.addEventListener('input', function () {
        var ok = el.getAttribute('data-input');
        var mask = el.getAttribute('data-mask');
        if (mask) { el.value = aplicaMascara(el.value, mask); }
        answers[ok] = el.value;
        julgaCampo(ok, el.value);
        refresh();
      });
    });
    document.getElementById('submit-btn').addEventListener('click', submit);
    refresh();
  }


  // <<<FORMAT-INTERPRETER>>>  (marcador: extraido pelo gate das tres superficies)
  // ── catalogo de formatos: afordancia + veredicto ────────────────────────────
  // TERCEIRA implementacao das primitivas semanticas, e a duplicacao e DECLARADA
  // (§D2 do adr-dialog-input-format-catalog): as tres superficies nao compartilham
  // import — foi assim que `evaluateAskWhen` acabou com tres copias, por topologia
  // e nao por desleixo. O que impede a divergencia e a POLITICA ser dado (shape,
  // mascara, teclado, mensagem vem do catalogo) e cada entrada carregar seus
  // VETORES, rodados contra as tres implementacoes pelo gate das superficies.
  var FORMATS = [];
  var invalidos = {};
  var nodePorOk = {};

  function ehDataCalendario(s) {
    var m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(s); if (!m) return false;
    var d = +m[1], mo = +m[2], y = +m[3];
    if (mo < 1 || mo > 12 || d < 1) return false;
    return d <= new Date(y, mo, 0).getDate();
  }
  function ehHora(s) {
    var m = /^(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(s); if (!m) return false;
    return +m[1] <= 23 && +m[2] <= 59 && (m[3] === undefined || +m[3] <= 59);
  }
  function ehCpf(s) {
    var d = String(s).replace(/\D/g, '');
    if (d.length !== 11 || /^(\d)\1{10}$/.test(d)) return false;
    function dv(ate) {
      var soma = 0, i;
      for (i = 0; i < ate; i++) soma += (+d[i]) * (ate + 1 - i);
      var r = (soma * 10) % 11; return r === 10 ? 0 : r;
    }
    return dv(9) === +d[9] && dv(10) === +d[10];
  }
  function ehLuhn(s) {
    var d = String(s).replace(/\D/g, ''); if (d.length < 12) return false;
    var soma = 0, alt = false, i, n;
    for (i = d.length - 1; i >= 0; i--) {
      n = +d[i]; if (alt) { n *= 2; if (n > 9) n -= 9; } soma += n; alt = !alt;
    }
    return soma % 10 === 0;
  }
  function ehMesAno(s) {
    var m = /^(\d{2})\/(\d{2})$/.exec(s); if (!m) return false;
    return +m[1] >= 1 && +m[1] <= 12;
  }
  var SEMANTICAS = {
    none: function () { return true; }, calendar_date: ehDataCalendario,
    clock_time: ehHora, cpf_checkdigit: ehCpf, luhn: ehLuhn, month_year: ehMesAno
  };

  function fmtPorId(id) {
    for (var i = 0; i < FORMATS.length; i++) if (FORMATS[i].id === id) return FORMATS[i];
    return null;
  }
  // D8 — o campo nomeia o tipo UMA vez: `masked: "cpf"` deriva o formato.
  function fmtPorMasked(tipo) {
    if (!tipo) return null;
    for (var i = 0; i < FORMATS.length; i++)
      if (FORMATS[i].from_masked_type === tipo) return FORMATS[i];
    return null;
  }
  function declDe(node) {
    var d = node.validation || null;
    if (d && d.format) return d;
    var tipo = node.masked === true ? 'opaque' : (typeof node.masked === 'string' ? node.masked : null);
    var der = fmtPorMasked(tipo);
    if (!der) return d;
    var out = {}; if (d) for (var k in d) out[k] = d[k];
    out.format = der.id; return out;
  }
  function entradaDe(node) {
    var d = declDe(node);
    return d && d.format ? fmtPorId(d.format) : null;
  }
  // Semantica desconhecida RECUSA: aceitar o que nao sabemos checar e fail-open
  // com cara de tolerancia.
  function julgaFormato(valor, ent) {
    if (!ent) return false;
    var s = valor == null ? '' : String(valor);
    var v = ent.verdict || {};
    if (v.shape) { try { if (!new RegExp(v.shape).test(s)) return false; } catch (e) { return false; } }
    var fn = SEMANTICAS[v.semantic || 'none'];
    if (!fn) return false;
    return fn(s);
  }
  function julgaDeclaracao(valor, decl) {
    if (!decl) return true;
    var s = valor == null ? '' : String(valor);
    if (decl.format && !julgaFormato(s, fmtPorId(decl.format))) return false;
    if (decl.numeric && (s.trim() === '' || isNaN(Number(s)))) return false;
    if (decl.min_length !== undefined && s.length < decl.min_length) return false;
    if (decl.max_length !== undefined && s.length > decl.max_length) return false;
    if (decl.min !== undefined && Number(s) < decl.min) return false;
    if (decl.max !== undefined && Number(s) > decl.max) return false;
    return true;
  }
  // A mascara GUIA e nao briga: consome so os digitos, insere os separadores e
  // descarta o excedente. Mascara que rejeita tecla vira veredicto disfarcado.
  function aplicaMascara(valor, mask) {
    if (!mask) return valor;
    var dig = String(valor || '').replace(/\D/g, ''), out = '', i = 0, k;
    for (k = 0; k < mask.length; k++) {
      if (i >= dig.length) break;
      if (mask[k] === '#') { out += dig[i]; i++; } else { out += mask[k]; }
    }
    return out;
  }
  function txt(ent, loc) {
    var e = ent && ent.verdict && ent.verdict.error;
    if (!e) return 'Formato invalido.';
    return typeof e === 'string' ? e : (e[loc] || e['pt-BR'] || Object.keys(e).map(function (k) { return e[k]; })[0]);
  }
  // <<<END-FORMAT-INTERPRETER>>>

  // So julga o que tem VALOR: campo vazio e assunto de obrigatoriedade, nao de
  // formato — reprovar vazio faria a pagina nascer vermelha.
  function julgaCampo(ok, valor) {
    var node = nodePorOk[ok];
    var alvo = document.querySelector('[data-err="' + ok + '"]');
    var mau  = false;
    if (node && valor) { mau = !julgaDeclaracao(valor, declDe(node)); }
    if (mau) { invalidos[ok] = 1; } else { delete invalidos[ok]; }
    if (alvo) {
      alvo.textContent = mau ? txt(entradaDe(node), 'pt-BR') : '';
      alvo.style.display = mau ? 'block' : 'none';
    }
    var btn = document.getElementById('submit-btn');
    if (btn) { btn.disabled = Object.keys(invalidos).length > 0; }
  }

  function submit() {
    var btn = document.getElementById('submit-btn');
    // O bloqueio ja aconteceu no botao, e o motivo esta impresso ao lado do
    // campo. Esta segunda checagem existe porque o botao pode ser acionado por
    // teclado antes do primeiro `input` — e um envio que passasse aqui gravaria
    // um sinal com formato que a plataforma acabou de recusar noutra superficie.
    if (Object.keys(invalidos).length > 0) { return; }
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
      // Catalogo indisponivel ⇒ lista vazia, e `julgaFormato` RECUSA quem
      // nomear formato. Campo sem formato segue livre, entao uma queda do
      // config-api nao trava um survey que nao usa formato nenhum.
      FORMATS = data.formats || [];
      render(data.form || {});
    })
    .catch(function () { root.innerHTML = '<div class="err">Pesquisa não encontrada ou expirada.</div>'; });
})();
</script>
</body>
</html>
"""


class ArchivedFormError(Exception):
    """O DialogForm pedido está ARQUIVADO no dialog-api (`deleted_at`).

    Criar link de survey é criar VÍNCULO NOVO — e é o único ponto do produto que faz isso a
    partir de um `form_id` que não veio de um vínculo já existente. Por isso recusa aqui e
    em lugar nenhum mais: os demais leitores CONTINUAM um vínculo (skill em execução, slot,
    ctx, segmento histórico) e servem arquivado de propósito (ADR adr-dialog-form-deletion
    D1/D4). Sem esta guarda, o arquivamento seria silenciosamente contornado toda vez que
    alguém disparasse um survey outbound — e o form ficaria congelado num token por dias.
    """

    def __init__(self, form_id: str, deleted_at: Any) -> None:
        super().__init__(f"dialog form archived: {form_id}")
        self.form_id = form_id
        self.deleted_at = deleted_at


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
        evaluation_api_url:       str = "",
        evaluation_service_token: str = "",
    ) -> None:
        self._redis    = redis
        self._producer = producer
        self._dialog   = dialog_api_url.rstrip("/")
        self._topic    = signals_topic
        self._ttl      = ttl_s
        self._base_url = base_url.rstrip("/")
        self._delivery = delivery or SurveyLinkDelivery()
        # S8/S9 — store operacional (evaluation-api). Vazio → degrada p/ emit-only.
        self._eval_url   = evaluation_api_url.rstrip("/")
        self._eval_token = evaluation_service_token
        # Cache do catálogo de formatos por tenant. Vive no __init__ e não no
        # `create_link`: lá ele seria REATRIBUÍDO a cada link criado, e um cache
        # que se esvazia sozinho é indistinguível de cache nenhum — só mais lento
        # de descobrir.
        self._fmt_cache: dict[str, list[dict[str, Any]]] = {}

    def invalidate_delivery_config(self, tenant_id: str | None = None) -> None:
        """Called on config.changed(survey) — drops the delivery layer's cached
        per-tenant link_delivery config so the next send re-reads config-api."""
        self._delivery.invalidate(tenant_id)

    def _key(self, token: str) -> str:
        return f"survey_web:token:{token}"

    def _link(self, token: str) -> str:
        path = f"/survey/{token}"
        return f"{self._base_url}{path}" if self._base_url else path

    async def deliver(self, token: str, kind: str, address: str, tenant_id: str = "") -> dict[str, Any]:
        """Envia o link de um token existente via a camada plugável de entrega
        (SMS/e-mail). Desacoplado do create → o gatilho outbound (§19) reenvia."""
        return await self._delivery.send(kind, address, self._link(token), tenant_id)

    async def create(
        self,
        tenant_id:         str,
        form_id:           str,
        origin_session_id: str = "",
        customer_key:      str = "",
        deliver_kind:      str = "",
        deliver_address:   str = "",
        grain:             str = "session",
        pool_id:           str = "",
    ) -> dict[str, Any]:
        """Congela o form publicado num token. Retorna {token, path[, delivery]}.
        Se deliver_kind+deliver_address vierem, entrega o link (camada plugável).
        `grain` (Journey J4): grão do sinal gravado no submit — `session` (default) ou
        `journey` (survey de processo N3, chaveado na raiz canônica via origin_session_id).
        `pool_id` (Segurança Fase B): pool da SESSÃO PESQUISADA (origin), congelado no
        token → carimbado na resposta (survey_instance.pool_id) e no session.signals no
        submit. Vazio = admin-only (decisão C)."""
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"{self._dialog}/v1/dialog/forms/{form_id}",
                params={"status": "published"},
                headers={"X-Tenant-ID": tenant_id},
            )
            r.raise_for_status()
            form = r.json()

        # O dialog-api SERVE form arquivado (é o que mantém contato em andamento e história
        # de pé) e diz que está — a recusa é decisão de quem cria vínculo, não do store.
        if form.get("deleted_at"):
            raise ArchivedFormError(form_id, form["deleted_at"])

        token  = secrets.token_urlsafe(24)
        record = {
            "tenant_id":         tenant_id,
            "form_id":           form_id,
            "form":              form,
            "origin_session_id": origin_session_id,
            "customer_key":      customer_key,
            "grain":             grain or "session",   # Journey J4
            "pool_id":           pool_id or "",        # Segurança Fase B (pool da origem)
            "status":            "open",
            "created_at":        _now_iso(),
        }
        await self._redis.set(self._key(token), json.dumps(record), ex=self._ttl)
        result: dict[str, Any] = {"token": token, "path": f"/survey/{token}"}
        if deliver_kind and deliver_address:
            result["delivery"] = await self.deliver(token, deliver_kind, deliver_address, tenant_id)
        return result

    async def get(self, token: str) -> dict[str, Any] | None:
        raw = await self._redis.get(self._key(token))
        return json.loads(raw) if raw else None

    async def format_catalog(self, tenant_id: str) -> list[dict[str, Any]]:
        """Catálogo de formatos (`dialog.formats`) para a página julgar e guiar.

        Lido no GET e **não congelado no token**: o catálogo pode mudar entre
        criar o link e a pessoa responder, e a fonte de verdade em runtime é o
        store. Congelá-lo faria a página validar contra uma política aposentada.

        Degrada para lista VAZIA com log NOMEANDO o motivo. A consequência está
        no interpretador da página: sem catálogo, campo que NOMEIE um formato é
        recusado — não sabemos julgar, então não deixamos passar como se
        soubéssemos. Campo sem `format` segue livre, então uma queda do
        config-api não trava um survey que não usa formato nenhum.
        """
        if tenant_id in self._fmt_cache:
            return self._fmt_cache[tenant_id]
        formatos: list[dict[str, Any]] = []
        if self._config_api_url:
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(
                        f"{self._config_api_url}/config/dialog/formats",
                        params={"tenant_id": tenant_id},
                    )
                if resp.status_code == 200:
                    valor = (resp.json() or {}).get("value") or {}
                    bruto = valor.get("formats")
                    if isinstance(bruto, list):
                        formatos = bruto
                else:
                    logger.warning(
                        "catalogo de formatos indisponivel (HTTP %s) — a pagina de survey "
                        "vai RECUSAR campo que declare formato, e aceitar os demais",
                        resp.status_code,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "catalogo de formatos indisponivel (%s) — a pagina de survey vai "
                    "RECUSAR campo que declare formato, e aceitar os demais", exc,
                )
        self._fmt_cache[tenant_id] = formatos
        return formatos

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
        signals:   list[dict[str, Any]] = []
        verbatims: list[dict[str, Any]] = []
        for node in form.get("nodes", []):
            if not isinstance(node, dict) or node.get("kind") != "question":
                continue
            okey = node.get("output_key")
            val = answers.get(okey)
            if val is None or val == "":
                continue
            metric = (node.get("capture") or {}).get("metric")
            if metric:
                try:
                    signals.append({"metric": metric, "value": float(val)})
                    continue
                except (TypeError, ValueError):
                    pass  # métrica com resposta não-numérica → cai p/ verbatim
            # Sem métrica (ou métrica não-numérica) = TEXTO ABERTO. Antes era DESCARTADO
            # (`continue`); agora vira verbatim LGPD no store operacional — NUNCA no
            # session.signals (analítico numérico).
            verbatims.append({"question_id": okey, "text": str(val)})

        captured_at = _now_iso()

        # Persist-first (ADR adr-survey-response-store): grava a resposta operacional
        # (inclui verbatim) ANTES de emitir o sinal. Falha → não emite, token fica
        # 'open' p/ retry. Idempotência = o TOKEN (single-use). Sem eval_url → emit-only.
        if (signals or verbatims) and self._eval_url:
            try:
                headers = {"Content-Type": "application/json"}
                if self._eval_token:
                    headers["X-Service-Token"] = self._eval_token
                async with httpx.AsyncClient(timeout=5) as c:
                    pr = await c.post(
                        f"{self._eval_url}/v1/evaluation/survey/responses",
                        headers=headers,
                        json={
                            "tenant_id":         rec["tenant_id"],
                            "idempotency_key":   token,
                            "grain":             rec.get("grain") or "session",
                            "survey_id":         rec.get("form_id"),
                            "origin_session_id": rec.get("origin_session_id") or token,
                            "customer_key":      rec.get("customer_key") or None,
                            # Segurança Fase B: pool da sessão pesquisada (congelado no create).
                            "pool_id":           rec.get("pool_id") or "",
                            "channel":           "web",
                            "signals":           signals,
                            "verbatims":         verbatims,
                            "response_channel":  "web",
                            "captured_at":       captured_at,
                        },
                    )
                    pr.raise_for_status()
            except Exception as exc:
                logger.warning("survey_web persist failed token=%s: %s", token, exc)
                return {"ok": False, "reason": "persist_failed", "error": str(exc)}

        if signals:
            event = {
                "event_id":          str(uuid.uuid4()),
                "tenant_id":         rec["tenant_id"],
                "origin_session_id": rec.get("origin_session_id") or token,
                "grain":             rec.get("grain") or "session",   # Journey J4
                "segment_id":        None,
                "agent_key":         "",
                "survey_session_id": None,
                "pool_id":           rec.get("pool_id") or "",        # Segurança Fase B
                "signals":           signals,
                "captured_at":       captured_at,
            }
            await self._producer.send(self._topic, value=json.dumps(event).encode("utf-8"))

        rec["status"]       = "submitted"
        rec["submitted_at"] = _now_iso()
        await self._redis.set(self._key(token), json.dumps(rec), ex=self._ttl)
        return {"ok": True, "signals_recorded": len(signals), "verbatims_recorded": len(verbatims)}
