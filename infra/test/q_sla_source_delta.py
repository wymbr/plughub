#!/usr/bin/env python3
"""
D14-iii — CONTAR ANTES DE TROCAR A FONTE. Probe de medição, sem efeito colateral.

Pergunta que ele responde:

  Os três leitores de SLA (`query.py:240` · `reports_query.py:3803` ·
  `_sla_eligible` em `reports_query.py:5901`) leem `sessions.sla_target_ms`.
  A (ii) entregou `segments.sla_target_ms`, carimbado no fechamento da espera.
  **Quanto muda cada número, por pool, ao trocar a fonte?**

  Trocar em silêncio é o que não pode: a (ii) é forward-only (linha antiga tem
  `NULL` e não há migração possível — o `first_queued_ms` é consumido na saída),
  então o denominador `countIf(sla_target_ms > 0)` encolhe e a série histórica
  esvazia. Se a mudança de número chegar ao operador sem contagem prévia, ela
  vira "o relatório quebrou".

  Decisão do dono (2026-08-25): saída **(b) cortar a série numa data declarada**.
  Este probe é quem DECLARA a data — `first_stamped` abaixo é o instante da
  primeira espera carimbada pelo produtor novo.

O que cada coluna mede (grão = PASSAGEM pela fila, nunca sessão):

  waits          todas as esperas (`segments role='queue'`) do pool
  concl          esperas concluídas (`duration_ms` não-nulo) — só elas são julgáveis
  aband          esperas abandonadas (excluídas da aderência desde a D14-i)
  alvo_sess      esperas cuja SESSÃO tem alvo > 0        ← fonte de HOJE
  alvo_seg       esperas cujo SEGMENTO tem alvo > 0      ← fonte DEPOIS
  discord        esperas com os DOIS alvos presentes e DIFERENTES
  elig_*         elegíveis pela regra do relatório, por fonte
  within_*       dentro do alvo, por fonte
  ader_*         within/elig — o número que o operador lê

⚠️ O PREDICADO É COPIADO DO RELATÓRIO, NÃO REESCRITO DE MEMÓRIA — e a primeira
   versão deste probe errou exatamente aqui. `_sla_eligible` (`reports_query.py:5901`)
   é `q_count > 0 ∧ wait_ms IS NOT NULL ∧ sla_target_ms > 0`: a espera ABANDONADA
   está no DENOMINADOR (só o `within_sla` a exclui, `:5913`). Excluí-la dos dois
   lados devolvia `retencao_humano` a 95,7% enquanto a tela mostra 0,6364 — um
   delta publicado contra um número que o operador nunca viu. Um instrumento pode
   ser falseável, ramificado e honesto e ainda medir a proposição adjacente.

⚠️ TESTEMUNHA DE PRESENÇA, obrigatória ao lado dos contadores de ausência:
  `waits` e `alvo_sess` ficam impressos junto de `alvo_seg`. Se `alvo_seg` vier
  0 em TODO pool, há dois mundos incompatíveis — "o produtor da (ii) não está
  emitindo" e "não houve tráfego desde o deploy" — e o probe **não** os separa
  sozinho: por isso imprime `first_stamped` e o total de esperas depois dela.
  Sem essa linha, um produtor que nunca emite passa por "ambiente parado".

⚠️ `discord` é a população que dá VALOR ao gate da fatia. Um teste de "as duas
  fontes concordam" só julga se existir o caso em que elas DISCORDAM — é a mesma
  lição do `0` no `test_sla_target_predicate`. Se `discord` vier 0 no ambiente, o
  gate precisa CONSTRUIR o caso (sessão com duas esperas, alvos diferentes), e
  isso passa a ser requisito da tarefa, não detalhe.

Uso (no host, fora do container):
    python3 infra/test/q_sla_source_delta.py
Env:
    TENANT (tenant_demo) · CH (http://localhost:8123) · CH_USER/CH_PASS (plughub)
    DB (plughub_demo — NUNCA `analytics`, que só existe nos testes)
"""
import os
import sys
import urllib.error
import urllib.request

TENANT = os.environ.get("TENANT", "tenant_demo")
CH = os.environ.get("CH", "http://localhost:8123")
CH_USER = os.environ.get("CH_USER", "plughub")
CH_PASS = os.environ.get("CH_PASS", "plughub")
DB = os.environ.get("DB", "plughub_demo")


def ch(sql: str) -> list[list[str]]:
    """Executa e devolve linhas já partidas por TAB.

    Delimitador: `TabSeparated` do CH. O split é por `\\t` EXPLÍCITO, nunca por
    `.split()` — tab é IFS whitespace e campo vazio seria dobrado, fazendo o
    campo seguinte escorregar de coluna (memória `ifs-whitespace-dobra-campo-vazio`).
    """
    req = urllib.request.Request(
        CH,
        data=sql.encode(),
        headers={"X-ClickHouse-User": CH_USER, "X-ClickHouse-Key": CH_PASS},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"INCONCLUSIVO: ClickHouse recusou a query\n{exc.read().decode()}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"INCONCLUSIVO: ClickHouse inalcançável em {CH} ({exc})")
    return [ln.split("\t") for ln in body.splitlines() if ln]


# `coalesce(...,0)` em TODA comparação com Nullable: sobre `Nullable`, um
# predicado com NULL devolve NULL e o `countIf` PULA a linha — o denominador se
# move sem que nada fique vermelho (memória `clickhouse-agregado-vazio-devolve-default`).
WAITS = f"""
    SELECT session_id,
           pool_id,
           coalesce(duration_ms, -1)    AS dur,
           coalesce(outcome, '')        AS outcome,
           coalesce(sla_target_ms, 0)   AS seg_target,
           started_at
    FROM {DB}.segments AS g FINAL
    WHERE tenant_id = '{TENANT}' AND role = 'queue'
"""

SESSIONS = f"""
    SELECT session_id, coalesce(sla_target_ms, 0) AS sess_target
    FROM {DB}.sessions AS x FINAL
    WHERE tenant_id = '{TENANT}'
"""

# Join binário simples: CH 23.8 não faz subquery correlacionada.
BY_POOL = f"""
SELECT w.pool_id                                                       AS pool_id,
       count()                                                         AS waits,
       countIf(w.dur >= 0)                                             AS concl,
       countIf(w.outcome = 'abandoned')                                AS aband,
       countIf(s.sess_target > 0)                                      AS alvo_sess,
       countIf(w.seg_target > 0)                                       AS alvo_seg,
       countIf(s.sess_target > 0 AND w.seg_target > 0
               AND s.sess_target != w.seg_target)                      AS discord,
       countIf(w.dur >= 0 AND s.sess_target > 0)                       AS elig_sess,
       countIf(w.dur >= 0 AND w.seg_target > 0)                        AS elig_seg,
       countIf(w.dur >= 0 AND w.outcome != 'abandoned'
               AND s.sess_target > 0 AND w.dur <= s.sess_target)       AS within_sess,
       countIf(w.dur >= 0 AND w.outcome != 'abandoned'
               AND w.seg_target > 0 AND w.dur <= w.seg_target)         AS within_seg,
       toString(min(w.started_at))                                     AS first_wait,
       toString(minIf(w.started_at, w.seg_target > 0))                 AS first_stamped
FROM ({WAITS}) AS w
LEFT JOIN ({SESSIONS}) AS s ON w.session_id = s.session_id
GROUP BY pool_id
ORDER BY waits DESC
FORMAT TabSeparated
"""


def pct(num: int, den: int) -> str:
    return f"{num / den * 100:5.1f}%" if den else "    —"


def main() -> None:
    rows = ch(BY_POOL)
    if not rows:
        raise SystemExit(
            "INCONCLUSIVO: zero segmentos `role='queue'` no tenant. Sem população,\n"
            "nenhum ramo abaixo vale — não é evidência de que o defeito não existe."
        )

    hdr = ("pool", "waits", "concl", "aband", "alvo_sess", "alvo_seg", "discord",
           "elig_sess", "elig_seg", "ader_sess", "ader_seg")
    print(f"== D14-iii · delta de fonte do alvo · tenant {TENANT} · db {DB}")
    print()
    print(f"{hdr[0]:<26}" + "".join(f"{h:>10}" for h in hdr[1:]))
    print("-" * (26 + 10 * (len(hdr) - 1)))

    tot = dict.fromkeys(
        ("waits", "concl", "aband", "alvo_sess", "alvo_seg", "discord",
         "elig_sess", "elig_seg", "within_sess", "within_seg"), 0)
    stamped_rows: list[tuple[str, str]] = []

    for r in rows:
        (pool, waits, concl, aband, a_sess, a_seg, disc,
         e_sess, e_seg, w_sess, w_seg, first_wait, first_stamped) = r
        vals = dict(zip(
            ("waits", "concl", "aband", "alvo_sess", "alvo_seg", "discord",
             "elig_sess", "elig_seg", "within_sess", "within_seg"),
            map(int, (waits, concl, aband, a_sess, a_seg, disc,
                      e_sess, e_seg, w_sess, w_seg))))
        for k, v in vals.items():
            tot[k] += v
        print(f"{pool:<26}"
              f"{vals['waits']:>10}{vals['concl']:>10}{vals['aband']:>10}"
              f"{vals['alvo_sess']:>10}{vals['alvo_seg']:>10}{vals['discord']:>10}"
              f"{vals['elig_sess']:>10}{vals['elig_seg']:>10}"
              f"{pct(vals['within_sess'], vals['elig_sess']):>10}"
              f"{pct(vals['within_seg'], vals['elig_seg']):>10}")
        # `minIf` sem nenhuma linha casando devolve o EPOCH, não NULL — por isso
        # o instante só é lido quando o contador ao lado prova que há amostra.
        if vals["alvo_seg"] > 0:
            stamped_rows.append((pool, first_stamped))

    print("-" * (26 + 10 * (len(hdr) - 1)))
    print(f"{'TOTAL':<26}"
          f"{tot['waits']:>10}{tot['concl']:>10}{tot['aband']:>10}"
          f"{tot['alvo_sess']:>10}{tot['alvo_seg']:>10}{tot['discord']:>10}"
          f"{tot['elig_sess']:>10}{tot['elig_seg']:>10}"
          f"{pct(tot['within_sess'], tot['elig_sess']):>10}"
          f"{pct(tot['within_seg'], tot['elig_seg']):>10}")

    # ── Testemunha: o produtor da (ii) está emitindo? ────────────────────────
    print()
    print("== TESTEMUNHA — o produtor da (ii) emite? (três ramos, ausente é INCONCLUSIVO)")
    print(f"   esperas totais={tot['waits']}  ·  com alvo na SESSÃO={tot['alvo_sess']}"
          f"  ·  com alvo no SEGMENTO={tot['alvo_seg']}")
    if tot["alvo_seg"] == 0:
        print("   ⇒ NENHUMA espera carimbada. Dois mundos que este número NÃO separa:")
        print("     produtor mudo × zero tráfego desde o deploy. Gerar um contato")
        print("     que ENFILEIRE antes de concluir qualquer coisa.")
    else:
        print("   ⇒ VIVO. Primeira espera carimbada por pool (= candidata a data do corte):")
        for pool, ts in sorted(stamped_rows, key=lambda x: x[1]):
            print(f"       {pool:<26} {ts}")
        print(f"   ⇒ DATA DO CORTE (saída b) = {min(ts for _, ts in stamped_rows)}")

    # ── Testemunha: existe o caso em que as fontes DISCORDAM? ────────────────
    print()
    print("== TESTEMUNHA — população que dá valor ao gate")
    print(f"   esperas com os DOIS alvos e valores DIFERENTES: {tot['discord']}")
    if tot["discord"] == 0:
        print("   ⇒ o ambiente NÃO contém o caso discriminante. Um teste de 'as duas")
        print("     fontes concordam' passaria idêntico sobre o código VELHO — logo o")
        print("     gate tem de CONSTRUIR a sessão com duas esperas de alvos distintos.")
    else:
        print("   ⇒ o caso existe no dado; o gate pode ancorar nele além do sintético.")

    # ── O que o operador vê mudar ────────────────────────────────────────────
    print()
    print("== DELTA QUE O OPERADOR VÊ")
    print(f"   denominador (elegíveis): {tot['elig_sess']} → {tot['elig_seg']}"
          f"  ({tot['elig_seg'] - tot['elig_sess']:+d})")
    print(f"   aderência global:        {pct(tot['within_sess'], tot['elig_sess']).strip()}"
          f" → {pct(tot['within_seg'], tot['elig_seg']).strip()}")
    print("   ⚠️ Encolher o denominador é o esperado, não sintoma: a (ii) é")
    print("      forward-only e linha antiga fica sem alvo. O corte (b) declara a")
    print("      data em vez de misturar duas fontes num mesmo percentual.")
    print()
    print("   ⚠️ Aderência AUSENTE ≠ aderência zero. Pool que perde todas as")
    print("      elegíveis tem de devolver `null`, e a UI já renderiza ausente.")


if __name__ == "__main__":
    sys.exit(main())
