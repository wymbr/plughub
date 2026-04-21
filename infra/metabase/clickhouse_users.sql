-- clickhouse_users.sql
-- Cria usuários read-only por tenant com Row Policies em todas as tabelas Analytics.
-- Executar como superusuário no ClickHouse após o schema existir.
--
-- Tabelas protegidas (database `plughub`):
--   sessions, queue_events, agent_events, messages, usage_events, sentiment_events
--
-- Uso:
--   clickhouse-client -h localhost -u plughub --password plughub \
--     --multiquery --multiline < clickhouse_users.sql
--
-- Para adicionar um novo tenant:
--   1. Copiar o bloco "-- TENANT: template" e substituir TENANT_ID + PASSWORD
--   2. Executar o bloco no ClickHouse
--
-- Sandboxing: cada usuário só vê registros onde tenant_id = '<seu_tenant>'.
-- A aplicação (Metabase) usa uma conexão separada por tenant, portanto a
-- filtragem é transparente — queries sem WHERE tenant_id ainda estão protegidas.

-- ─────────────────────────────────────────────────────────────────────────────
-- Garante que o access management está habilitado
-- ─────────────────────────────────────────────────────────────────────────────
GRANT SELECT ON plughub.* TO plughub;  -- admin já existe; garante SELECT no schema


-- ─────────────────────────────────────────────────────────────────────────────
-- TENANT: tenant_telco
-- ─────────────────────────────────────────────────────────────────────────────
CREATE USER IF NOT EXISTS tenant_telco
    IDENTIFIED WITH plaintext_password BY 'tenant_telco_ro_2024'
    HOST ANY
    SETTINGS readonly = 1;

GRANT SELECT ON plughub.sessions         TO tenant_telco;
GRANT SELECT ON plughub.queue_events     TO tenant_telco;
GRANT SELECT ON plughub.agent_events     TO tenant_telco;
GRANT SELECT ON plughub.messages         TO tenant_telco;
GRANT SELECT ON plughub.usage_events     TO tenant_telco;
GRANT SELECT ON plughub.sentiment_events TO tenant_telco;

CREATE ROW POLICY IF NOT EXISTS tenant_telco_sessions
    ON plughub.sessions FOR SELECT USING tenant_id = 'tenant_telco' TO tenant_telco;

CREATE ROW POLICY IF NOT EXISTS tenant_telco_queue_events
    ON plughub.queue_events FOR SELECT USING tenant_id = 'tenant_telco' TO tenant_telco;

CREATE ROW POLICY IF NOT EXISTS tenant_telco_agent_events
    ON plughub.agent_events FOR SELECT USING tenant_id = 'tenant_telco' TO tenant_telco;

CREATE ROW POLICY IF NOT EXISTS tenant_telco_messages
    ON plughub.messages FOR SELECT USING tenant_id = 'tenant_telco' TO tenant_telco;

CREATE ROW POLICY IF NOT EXISTS tenant_telco_usage_events
    ON plughub.usage_events FOR SELECT USING tenant_id = 'tenant_telco' TO tenant_telco;

CREATE ROW POLICY IF NOT EXISTS tenant_telco_sentiment_events
    ON plughub.sentiment_events FOR SELECT USING tenant_id = 'tenant_telco' TO tenant_telco;


-- ─────────────────────────────────────────────────────────────────────────────
-- TENANT: tenant_bank  (exemplo de segundo tenant)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE USER IF NOT EXISTS tenant_bank
    IDENTIFIED WITH plaintext_password BY 'tenant_bank_ro_2024'
    HOST ANY
    SETTINGS readonly = 1;

GRANT SELECT ON plughub.sessions         TO tenant_bank;
GRANT SELECT ON plughub.queue_events     TO tenant_bank;
GRANT SELECT ON plughub.agent_events     TO tenant_bank;
GRANT SELECT ON plughub.messages         TO tenant_bank;
GRANT SELECT ON plughub.usage_events     TO tenant_bank;
GRANT SELECT ON plughub.sentiment_events TO tenant_bank;

CREATE ROW POLICY IF NOT EXISTS tenant_bank_sessions
    ON plughub.sessions FOR SELECT USING tenant_id = 'tenant_bank' TO tenant_bank;

CREATE ROW POLICY IF NOT EXISTS tenant_bank_queue_events
    ON plughub.queue_events FOR SELECT USING tenant_id = 'tenant_bank' TO tenant_bank;

CREATE ROW POLICY IF NOT EXISTS tenant_bank_agent_events
    ON plughub.agent_events FOR SELECT USING tenant_id = 'tenant_bank' TO tenant_bank;

CREATE ROW POLICY IF NOT EXISTS tenant_bank_messages
    ON plughub.messages FOR SELECT USING tenant_id = 'tenant_bank' TO tenant_bank;

CREATE ROW POLICY IF NOT EXISTS tenant_bank_usage_events
    ON plughub.usage_events FOR SELECT USING tenant_id = 'tenant_bank' TO tenant_bank;

CREATE ROW POLICY IF NOT EXISTS tenant_bank_sentiment_events
    ON plughub.sentiment_events FOR SELECT USING tenant_id = 'tenant_bank' TO tenant_bank;


-- ─────────────────────────────────────────────────────────────────────────────
-- Verificação (executar separadamente para confirmar isolamento)
-- ─────────────────────────────────────────────────────────────────────────────
-- SELECT user, name FROM system.row_policies WHERE database = 'plughub';
-- SELECT name, host_names, default_roles FROM system.users WHERE name LIKE 'tenant_%';
