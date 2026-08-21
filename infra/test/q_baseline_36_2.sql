-- Recorte canônico do Problema 36.2 (conference-mechanics.md § 36.2).
-- Roda ANTES e DEPOIS de qualquer conserto do arco da janela de espera (D12).
--
-- Baseline medida 2026-08-21 (código ANTIGO), população 522:
--   closed        / no_abandoned_queue  469
--   suspended     / no_abandoned_queue   27
--   closed        / queue_abandoned      10   <- TESTEMUNHA: têm de continuar fechando
--   active        / no_abandoned_queue    8
--   never_closed  / queue_abandoned       5   <- o defeito 36.2 (causa NÃO identificada)
--   (null)        / no_abandoned_queue    3
--
-- ⚠️ `closed_at IS NULL` é o instrumento, NÃO `status='active'` — o par
--    status='active' AND outcome='abandoned' devolve zero FABRICADO pelo recorte.
-- ⚠️ `FROM t AS alias FINAL`, nunca `FROM t FINAL AS alias` (erro de sintaxe no 23.8).
WITH q AS (
  SELECT session_id, countIf(outcome = 'abandoned') AS ab
  FROM plughub_demo.segments FINAL
  WHERE tenant_id = 'tenant_demo' AND role = 'queue'
  GROUP BY session_id
)
SELECT if(s.closed_at IS NULL, 'never_closed', 'closed')                  AS sess,
       coalesce(s.status, '(null)')                                       AS st,
       if(coalesce(q.ab, 0) > 0, 'queue_abandoned', 'no_abandoned_queue') AS segq,
       count() AS n
FROM plughub_demo.sessions AS s FINAL
LEFT JOIN q ON q.session_id = s.session_id
WHERE s.tenant_id = 'tenant_demo'
GROUP BY sess, st, segq ORDER BY n DESC;
