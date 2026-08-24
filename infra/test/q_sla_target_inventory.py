#!/usr/bin/env python3
"""
Inventário de `sla_target_ms` — probe de MEDIÇÃO, sem efeito colateral (D14.1).

Pergunta que ele responde (TODO § "SLA está no grão errado", subseção D14.1):

  O campo é DECLARADO como "Total service SLA" (rótulo + contrato Zod
  `agent-registry.ts:390`) e CONSUMIDO em dois sentidos incompatíveis:

    ESPERA  — analytics (`query.py:240`, `reports_query.py:3803`, `:5827`) E,
              o que o TODO não registrava, o ROTEAMENTO: `scorer.py:177`
              (aging/breach do ZSET), `decide.py:287` (`sla_urgency > 1.0 → inf`),
              `saturated.py:92/109/126` (ETA e redirect), `main.py:1055`
              (`avg_handle_ms = sla*0.7` → ETA publicada ao CLIENTE).
    TOTAL   — `supervisor.ts:202` (`now − session.started_at`), Console
              `ContactList.tsx:149`, `agent-assist-ui` ×3.

  A consequência que o probe mede não é de exibição. Com alvo de 24 h, o
  `sla_ratio` do scorer após 10 min de espera é 0,0069 ⇒ o termo de aging é
  ~0,7% do `aging_factor` e o `breach_bonus` é ZERO para sempre. Um contato
  nesses pools **não ganha prioridade por esperar**, e o ramo de prioridade
  máxima absoluta (`sla_urgency > 1.0`) é inalcançável em qualquer horizonte
  prático.

  ⚠️ Mas isso só MORDE em pool onde contato de fato ESPERA. E "espera" NÃO se
  lê da config: `queue_address() == ""` significa fila **MUDA**, não "não
  enfileira" — o contato espera igual, ganha `first_queued_ms` e emite
  `role='queue'`; o endereço só decide se uma IA atende a espera. Logo a
  testemunha do "morde ou é latente" é DADO (segmentos `role='queue'` por pool),
  não configuração — e sai numa query à parte, impressa no fim.

  Baldes por faixa (mutuamente exclusivos):
    wait_plausible  — 1 s ≤ alvo ≤ 10 min   (alvo de ESPERA, plausível)
    grey            — 10 min < alvo < 1 h   (ambíguo — declarar, não interpretar)
    process         — alvo ≥ 1 h            (prazo de PROCESSO; inviolável como espera)
    absent          — campo ausente/0/inválido

  Testemunhas obrigatórias (contadores de PRESENÇA ao lado dos de ausência):
    · `wait_plausible` ao lado de `process` — se um vier 0, "o parque é todo de
      um tipo" é medição; sozinho, `process > 0` não diz que há duas populações.
    · segmentos `role='queue'` observados nos pools da faixa `process` — se vier
      0, o aging inerte é latente, não vivo. Contar quem sofre ANTES de declarar
      risco (memória `exposicao-latente-e-hipotese`: o mesmo arco já deu os dois
      desfechos, e só a contagem os separou).
    · os DOIS defaults (30 000 do formulário × 480 000 do routing/mcp-server)
      contados separadamente — um campo com dois defaults não tem default.

Uso (no host, fora do container):
    python3 infra/test/q_sla_target_inventory.py
Env:
    TENANT (default tenant_demo) · REGISTRY (3300) · CONFIG_API (3600)
"""
import json
import os
import urllib.error
import urllib.request

TENANT = os.environ.get("TENANT", "tenant_demo")
REGISTRY = os.environ.get("REGISTRY", "http://localhost:3300")
CONFIG_API = os.environ.get("CONFIG_API", "http://localhost:3600")

MINUTE = 60_000
HOUR = 60 * MINUTE

# Os dois defaults que convivem hoje. NÃO são "o default": são duas fontes que
# discordam, e é por isso que aparecem contados à parte.
FORM_DEFAULT = 30_000        # PoolsPage.tsx:603,755
RUNTIME_DEFAULT = 480_000    # kafka_listener.py:218 · registry.py:3133 · supervisor.ts:74


def fetch(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def unwrap(payload):
    """`GET /v1/pools` devolve ENVELOPE, não lista."""
    if isinstance(payload, list):
        return payload
    for key in ("pools", "data", "items"):
        if isinstance(payload.get(key), list):
            return payload[key]
    raise SystemExit(f"INCONCLUSIVO: envelope desconhecido, chaves={list(payload)}")


def fmt_ms(ms: int) -> str:
    if ms >= HOUR:
        h = ms / HOUR
        return f"{h:g}h" if h < 24 else f"{h / 24:g}d"
    if ms >= MINUTE:
        return f"{ms / MINUTE:g}min"
    return f"{ms / 1000:g}s"


def band(ms: int | None) -> str:
    if not ms or ms <= 0:
        return "absent"
    if ms <= 10 * MINUTE:
        return "wait_plausible"
    if ms < HOUR:
        return "grey"
    return "process"


def main() -> None:
    try:
        pools = unwrap(fetch(f"{REGISTRY}/v1/pools", {"x-tenant-id": TENANT}))
    except urllib.error.URLError as exc:
        raise SystemExit(f"INCONCLUSIVO: agent-registry inalcançável ({exc})")

    # O tenant default de fila decide se um pool SEM `queue_config` enfileira.
    # Ausente ⇒ pool sem endereço próprio NÃO enfileira (defeito 2, já corrigido:
    # o tier passou a ser decidido pelo ENDEREÇO, via `mute_queue.queue_address`).
    tenant_default = {}
    for key in ("queue_default_agent_type_id", "queue_default_skill_id"):
        try:
            tenant_default[key] = fetch(
                f"{CONFIG_API}/config/session/{key}?tenant_id={TENANT}"
            )["value"]
        except urllib.error.HTTPError:
            tenant_default[key] = None
        except urllib.error.URLError as exc:
            tenant_default[key] = f"INCONCLUSIVO ({exc})"

    buckets: dict[str, list] = {
        "wait_plausible": [], "grey": [], "process": [], "absent": [],
    }
    for pool in pools:
        pid = pool.get("pool_id") or pool.get("id") or "?"
        raw = pool.get("sla_target_ms")
        ms = int(raw) if isinstance(raw, (int, float)) and raw else None
        cfg = pool.get("queue_config")
        # TIER da espera, não "enfileira ou não": todo pool pode ter contato
        # esperando. Só `pool_id` endereça quem ATENDE a espera (o `skill_id`
        # legado não resolve flow nenhum desde 2026-07-13). Ver
        # `q_queue_config_inventory.py` e CLAUDE.md § corolário de 2026-08-24.
        addressed = bool(isinstance(cfg, dict) and (cfg.get("pool_id") or "").strip())
        buckets[band(ms)].append({
            "pool_id":     pid,
            "ms":          ms,
            "purpose":     pool.get("purpose") or "?",
            "agent_kind":  pool.get("agent_kind") or "?",
            "dispatch":    pool.get("dispatch_mode") or "?",
            "channels":    ",".join(pool.get("channel_types") or []) or "-",
            "tier":        "atendida" if addressed else "muda",
        })

    print(f"== pools no tenant {TENANT}: {len(pools)}")
    print()
    for name in ("wait_plausible", "grey", "process", "absent"):
        rows = sorted(buckets[name], key=lambda r: (r["ms"] or 0, r["pool_id"]))
        print(f"-- {name}: {len(rows)}")
        for r in rows:
            alvo = fmt_ms(r["ms"]) if r["ms"] else "AUSENTE"
            print(f"     {r['pool_id']:<28} {alvo:>7}  purpose={r['purpose']:<8} "
                  f"kind={r['agent_kind']:<7} dispatch={r['dispatch']:<5} "
                  f"fila={r['tier']:<8} ch={r['channels']}")
        print()

    # ── Testemunha 1: as duas populações existem? ────────────────────────────
    n_wait = len(buckets["wait_plausible"])
    n_proc = len(buckets["process"])
    print("== TESTEMUNHA 1 — duas populações no mesmo campo")
    print(f"   wait_plausible={n_wait}  ×  process={n_proc}  (grey={len(buckets['grey'])})")
    if n_wait == 0 or n_proc == 0:
        print("   ⇒ UMA população só. O campo tem sentido único NESTE parque;")
        print("     a ambiguidade seria de contrato, não de dado.")
    else:
        print("   ⇒ DUAS populações incomparáveis. Todo agregado de SLA hoje as mistura.")

    # ── Testemunha 2: o aging inerte MORDE, ou é latente? (pergunta de DADO) ──
    proc_ids = sorted(r["pool_id"] for r in buckets["process"])
    wait_ids = sorted(r["pool_id"] for r in buckets["wait_plausible"])
    print()
    print("== TESTEMUNHA 2 — o aging inerte é VIVO ou LATENTE? (roda a query abaixo)")
    print(f"   tier das filas: atendida="
          f"{sum(1 for b in buckets.values() for r in b if r['tier'] == 'atendida')}"
          f"  muda={sum(1 for b in buckets.values() for r in b if r['tier'] == 'muda')}")
    print(f"   tenant default de fila: {tenant_default}")
    print("   ⚠️ tier NÃO responde 'espera ou não' — fila muda também espera. A")
    print("      pergunta é quantos contatos ESPERARAM em pool da faixa `process`:")
    print()
    proc_list = ", ".join(f"'{p}'" for p in proc_ids) or "''"
    wait_list = ", ".join(f"'{p}'" for p in wait_ids) or "''"
    print("   docker compose -f docker-compose.demo.yml exec -T clickhouse \\")
    print("     clickhouse-client -q \"")
    print("       SELECT multiIf(pool_id IN (%s), 'process'," % proc_list)
    print("                      pool_id IN (%s), 'wait_plausible'," % wait_list)
    print("                      'outro')                       AS faixa,")
    print("              count()                                AS esperas,")
    print("              uniqExact(session_id)                  AS sessoes,")
    print("              round(avg(duration_ms))                AS media_ms,")
    print("              max(duration_ms)                       AS max_ms")
    print("       FROM plughub_demo.segments AS s FINAL")
    print("       WHERE tenant_id = '%s' AND role = 'queue'" % TENANT)
    print("       GROUP BY faixa ORDER BY esperas DESC\" < /dev/null")
    print()
    print("   Leitura do veredicto — TRÊS ramos, e o ausente é INCONCLUSIVO:")
    print("     faixa `process` com esperas > 0  ⇒ VIVO (contato esperou onde o")
    print("        aging é inerte e o breach é inalcançável)")
    print("     faixa `process` com esperas = 0  ⇒ LATENTE (declarar como dedução)")
    print("     nenhuma linha sequer            ⇒ INCONCLUSIVO (sem produtor ou")
    print("        sem amostra — não é evidência de ausência de defeito)")
    print("   `wait_plausible` é a TESTEMUNHA DE PRESENÇA ao lado: se ela vier 0")
    print("   também, o instrumento não está medindo nada e nenhum ramo vale.")

    # ── Testemunha 3: o campo tem default? ───────────────────────────────────
    n_form = sum(1 for b in buckets.values() for r in b if r["ms"] == FORM_DEFAULT)
    n_rt = sum(1 for b in buckets.values() for r in b if r["ms"] == RUNTIME_DEFAULT)
    print()
    print("== TESTEMUNHA 3 — dois defaults convivem")
    print(f"   pools em {FORM_DEFAULT} (formulário, PoolsPage.tsx):        {n_form}")
    print(f"   pools em {RUNTIME_DEFAULT} (routing/mcp-server, hardcoded): {n_rt}")
    print(f"   pools em outro valor (configurado à mão):                   "
          f"{len(pools) - n_form - n_rt - len(buckets['absent'])}")
    print("   ⇒ um campo com dois defaults não tem default; quem lê a tela e quem")
    print("     lê o runtime discordam quando a config está ausente.")

    # ── Correlação com o discriminador da D13 ────────────────────────────────
    print()
    print("== CORRELAÇÃO com o discriminador D13 (contato × interno)")
    print(f"   {'faixa':<16} {'purpose=contact':>16} {'purpose=internal':>17} {'outro':>7}")
    for name in ("wait_plausible", "grey", "process", "absent"):
        rows = buckets[name]
        c = sum(1 for r in rows if r["purpose"] == "contact")
        i = sum(1 for r in rows if r["purpose"] == "internal")
        print(f"   {name:<16} {c:>16} {i:>17} {len(rows) - c - i:>7}")
    print("   ⇒ se a divisão COINCIDE com `purpose`, a correção é tratável sem")
    print("     adivinhar intenção pool a pool. Se NÃO coincide, cada pool precisa")
    print("     de decisão própria — e o custo da D14.1 muda de ordem.")


if __name__ == "__main__":
    main()
