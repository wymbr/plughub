#!/usr/bin/env python3
"""
_rank_grant_probe.py — o guard de RANK medido na BORDA, nao no predicado.

Auxiliar de `probe_rank_grant_guard.sh` (MOD-02 / E2 da emenda de 2026-09-08).

POR QUE ELE EXISTE, TENDO 21 TESTES DE UNIDADE DO PREDICADO
------------------------------------------------------------
Porque predicado correto e ROTA GUARDADA sao dois fatos, e so o segundo protege
alguem. Medido em 2026-09-08, durante esta propria entrega: com `grants.violacoes`
ja verde em 21 testes, a primeira medicao ao vivo mostrou um supervisor criando um
usuario `admin`. O predicado estava certo; o que faltava conferir era a fiacao.

(Naquele caso especifico o veredicto ao vivo tambem estava contaminado — a conta
usada detinha `config.permissions`, logo era MASTER e o 201 estava correto. As duas
licoes valem: medir na borda, e conferir de que regime e a conta que se usa.)

O QUE ELE MEDE
--------------
Um DELEGADO de verdade, criado pelo probe: `config.users: read_write`, o pacote do
preset de `operator`, e UM pool. Com ele:

  P1  POSITIVO — cria `operator` no proprio pool  -> 201
      Sem este, um guard que negasse tudo passaria no arquivo inteiro. E ele e a
      Costura 1 do ADR: antes da MOD-02 este caso era 403, e o supervisor nao
      conseguia contratar.
  N1  NEGATIVO — `roles: ["admin"]`               -> 403 nomeando o campo
  N2  NEGATIVO — pool fora do proprio escopo      -> 403 nomeando o pool
  N3  NEGATIVO — escreve `module_config` alheio com campo acima do proprio -> 403
      (a SEGUNDA porta: ate a MOD-02 ela nao comparava nada com o chamador)

Tudo o que ele cria, ele remove — inclusive em falha.

SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

VERDE, VERMELHO, INCONCLUSIVO = 0, 1, 2

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3202").rstrip("/")
TENANT = sys.argv[2] if len(sys.argv) > 2 else "tenant_demo"
PREFIXO = "rankprobe_"

# O delegado precisa COBRIR o preset de `operator` — e essa lista nao e livre: ela
# e o preset, campo a campo. Toda vez que o preset do contratado cresce, esta
# fixture fica para tras e o cenario P1 (dentro do alcance) vira 403. Aconteceu na
# MOD-05, quando `agent_assist.atender` entrou no preset do operator: o vermelho
# aqui e o MESMO fato que o backfill conserta na populacao viva.
PACOTE_DELEGADO = {
    "config": {"users": {"access": "read_write", "scope": []}},
    "contacts": {"monitorar": {"access": "read_write", "scope": []},
                 "visualizar": {"access": "read_only", "scope": []}},
    "agent_assist": {"atender": {"access": "read_write", "scope": []}},
    "approvals": {"decide": {"access": "read_write", "scope": []},
                  "operacao": {"access": "read_write", "scope": []}},
    "evaluation": {"contestar": {"access": "read_write", "scope": []}},
}


def call(path, data=None, tok=None, method=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(data).encode() if data is not None else None,
        method=method or ("POST" if data is not None else "GET"),
    )
    req.add_header("Content-Type", "application/json")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            # 204 vem sem corpo (o DELETE de usuario): `json.load` num corpo vazio
            # levanta, e a limpeza morreria deixando conta de teste no banco.
            bruto = r.read()
            return r.status, (json.loads(bruto) if bruto.strip() else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return e.code, {}
    except urllib.error.URLError as e:
        return 0, {"detail": str(e)}


def main() -> int:
    tok_master = os.environ.get("PLUGHUB_TOKEN", "")
    if not tok_master:
        print("INCONCLUSIVO — sem PLUGHUB_TOKEN")
        return INCONCLUSIVO

    criados: list[str] = []
    templates: list[str] = []
    falhas = 0

    def limpar():
        for uid in criados:
            call(f"/auth/users/{uid}", tok=tok_master, method="DELETE")
        for tid in templates:
            call(f"/auth/templates/{tid}", tok=tok_master, method="DELETE")

    try:
        # ── o delegado ────────────────────────────────────────────────────────
        st, novo = call("/auth/users", {
            "tenant_id": TENANT, "email": f"{PREFIXO}delegado@plughub.local",
            "password": "password123", "roles": ["operator"],
            "accessible_pools": ["limite_ia"],
        }, tok=tok_master)
        if st != 201:
            print(f"INCONCLUSIVO — nao consegui criar o delegado ({st}): "
                  f"{str(novo)[:160]}")
            return INCONCLUSIVO
        uid = novo["id"]
        criados.append(uid)
        st, _ = call(f"/auth/users/{uid}/module-config", PACOTE_DELEGADO,
                     tok=tok_master, method="PUT")
        if st != 200:
            print(f"INCONCLUSIVO — nao consegui montar o pacote do delegado ({st})")
            return INCONCLUSIVO

        st, d = call("/auth/login", {"email": f"{PREFIXO}delegado@plughub.local",
                                     "password": "password123"})
        if st != 200:
            print(f"INCONCLUSIVO — delegado nao loga ({st})")
            return INCONCLUSIVO
        tok = d["access_token"]
        print(f"delegado montado · pools={d.get('user', {}).get('accessible_pools')}")

        def cenario(rot, corpo, esperado, deve_citar=None):
            nonlocal falhas
            body = {"tenant_id": TENANT, "email": f"{PREFIXO}alvo@plughub.local",
                    "password": "password123"}
            body.update(corpo)
            st, d = call("/auth/users", body, tok=tok)
            if st == 201:
                criados.append(d["id"])
            det = str(d.get("detail", "")) if isinstance(d, dict) else ""
            ok = st == esperado and (deve_citar is None or deve_citar in det)
            if not ok:
                falhas += 1
            print(f"  {'verde' if ok else 'VERMELHO'} — {rot}: {st} "
                  f"(esperado {esperado}){' · ' + det[:110] if det else ''}")
            if st == 201:  # limpa ja, para o proximo cenario reusar o e-mail
                call(f"/auth/users/{d['id']}", tok=tok_master, method="DELETE")
                criados.remove(d["id"])

        print("\nP1 — o POSITIVO (a Costura 1: antes da MOD-02 isto era 403)")
        cenario("cria operator no proprio pool",
                {"roles": ["operator"], "accessible_pools": ["limite_ia"]}, 201)

        print("\nN1..N2 — os negativos no CORPO")
        cenario("roles=['admin'] (o preset expande no que ele nao tem)",
                {"roles": ["admin"]}, 403, "nao pode conceder")
        cenario("pool fora do proprio escopo",
                {"accessible_pools": ["sac_ia"]}, 403, "accessible_pools")

        # ── N2b/N2c — o corpo CURTO, medido em 2026-09-08 ────────────────────
        # `CreateUserRequest.roles` tem default `["operator"]`, e o preset e aplicado
        # em toda criacao. Enquanto o guard rodava sob `model_fields_set`, OMITIR o
        # campo pulava a verificacao e o usuario nascia com o preset inteiro — um
        # delegado que so detinha `config.users` criava operator completo. O par
        # abaixo precisa dos DOIS: sem o positivo, um guard que negasse toda criacao
        # deste delegado passaria igual.
        print("\nN2b..N2c — o corpo CURTO (o default de `roles` tambem concede)")
        st, mini = call("/auth/users", {
            "tenant_id": TENANT, "email": f"{PREFIXO}minimo@plughub.local",
            "password": "password123", "roles": [], "accessible_pools": [],
        }, tok=tok_master)
        if st != 201:
            print(f"  INCONCLUSIVO — nao consegui criar o delegado minimo ({st})")
            falhas += 1
        else:
            criados.append(mini["id"])
            call(f"/auth/users/{mini['id']}/module-config",
                 {"config": {"users": {"access": "read_write", "scope": []}}},
                 tok=tok_master, method="PUT")
            st, d = call("/auth/login", {"email": f"{PREFIXO}minimo@plughub.local",
                                         "password": "password123"})
            tok_min = d.get("access_token", "") if st == 200 else ""
            if not tok_min:
                print(f"  INCONCLUSIVO — delegado minimo nao loga ({st})")
                falhas += 1
            else:
                def cenario_min(rot, corpo, esperado, deve_citar=None):
                    nonlocal falhas
                    body = {"tenant_id": TENANT,
                            "email": f"{PREFIXO}curto@plughub.local",
                            "password": "password123"}
                    body.update(corpo)
                    st, d = call("/auth/users", body, tok=tok_min)
                    det = str(d.get("detail", "")) if isinstance(d, dict) else ""
                    ok = st == esperado and (deve_citar is None or deve_citar in det)
                    if not ok:
                        falhas += 1
                    print(f"  {'verde' if ok else 'VERMELHO'} — {rot}: {st} "
                          f"(esperado {esperado}){' · ' + det[:100] if det else ''}")
                    if st == 201:
                        call(f"/auth/users/{d['id']}", tok=tok_master, method="DELETE")

                cenario_min("corpo SEM `roles` (o default e ['operator'])",
                            {}, 403, "nao pode conceder")
                cenario_min("POSITIVO: `roles: []` — nada a conceder",
                            {"roles": [], "accessible_pools": []}, 201)

        print("\nN3 — a SEGUNDA porta (`PUT module-config`)")
        st, alvo = call("/auth/users", {
            "tenant_id": TENANT, "email": f"{PREFIXO}alvo2@plughub.local",
            "password": "password123"}, tok=tok_master)
        if st == 201:
            criados.append(alvo["id"])
            st, d = call(f"/auth/users/{alvo['id']}/module-config",
                         {"config": {"permissions": {"access": "read_write", "scope": []}}},
                         tok=tok, method="PUT")
            det = str(d.get("detail", "")) if isinstance(d, dict) else ""
            ok = st == 403 and "config.permissions" in det
            if not ok:
                falhas += 1
            print(f"  {'verde' if ok else 'VERMELHO'} — delegado tenta conceder "
                  f"config.permissions: {st} (esperado 403) · {det[:110]}")
        else:
            print(f"  INCONCLUSIVO — nao consegui criar o alvo da segunda porta ({st})")
            return INCONCLUSIVO

        print("\nT1..T3 - a TERCEIRA porta (`from-template`, MOD-09)")
        st, t_ok = call("/auth/templates", {
            "tenant_id": TENANT, "name": PREFIXO + "Operador", "description": "probe",
            "config": {"role": "operator",
                       "module_config": {"contacts": {"monitorar": {"access": "read_write", "scope": []}}},
                       "accessible_pools": ["sac_ia"]}}, tok=tok_master)
        st2, t_mau = call("/auth/templates", {
            "tenant_id": TENANT, "name": PREFIXO + "AdminTotal", "description": "probe",
            "config": {"role": "admin", "module_config": {}}}, tok=tok_master)
        if st != 201 or st2 != 201:
            print("  INCONCLUSIVO - nao consegui criar os templates (%s/%s)" % (st, st2))
            return INCONCLUSIVO
        templates.extend([t_ok["id"], t_mau["id"]])

        def cenario_tpl(rot, tid, corpo, esperado, deve_citar=None, checar=None):
            nonlocal falhas
            body = {"tenant_id": TENANT, "email": PREFIXO + "tpl@plughub.local",
                    "password": "password123"}
            body.update(corpo)
            st, d = call("/auth/users/from-template/" + tid, body, tok=tok)
            det = str(d.get("detail", "")) if isinstance(d, dict) else ""
            ok = st == esperado and (deve_citar is None or deve_citar in det)
            extra = ""
            if st == 201:
                if checar:
                    ok2, extra = checar(d)
                    ok = ok and ok2
                call("/auth/users/" + d["id"], tok=tok_master, method="DELETE")
            if not ok:
                falhas += 1
            print("  %s - %s: %s (esperado %s)%s%s" % (
                "verde" if ok else "VERMELHO", rot, st, esperado, extra,
                (" - " + det[:90]) if det else ""))

        def confere_proveniencia(d):
            veio = d.get("created_from_template_id") == t_ok["id"]
            tem_hash = bool(d.get("created_from_template_hash"))
            pools_ok = d.get("accessible_pools") == ["limite_ia"]
            return (veio and tem_hash and pools_ok,
                    " - proveniencia=%s hash=%s pools_do_aplicador=%s" % (veio, tem_hash, pools_ok))

        cenario_tpl("aplica template dentro do alcance", t_ok["id"],
                    {"accessible_pools": ["limite_ia"]}, 201, checar=confere_proveniencia)
        cenario_tpl("aplica template 'Admin Total'", t_mau["id"], {}, 403, "nao pode conceder")
        cenario_tpl("pool fora do escopo do aplicador", t_ok["id"],
                    {"accessible_pools": ["sac_ia"]}, 403, "accessible_pools")

        print(f"\n== {falhas} cenario(s) reprovado(s) ==")
        return VERMELHO if falhas else VERDE
    finally:
        limpar()


if __name__ == "__main__":
    sys.exit(main())
