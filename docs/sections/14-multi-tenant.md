# 14. Arquitetura Multi-Tenant

> Fonte: PlugHub spec v24.0

14. Arquitetura Multi-Tenant

14.1 Modelo Híbrido de Isolamento

  -----------------------------------------------------------------------
  Tier               Características
  ------------------ ----------------------------------------------------
  Standard —         Infra compartilhada. Kafka, Redis, PostgreSQL e
  namespace          ClickHouse particionados por tenant_id. Agent
                     Registry, MCP Servers e pipeline STT são instâncias
                     compartilhadas. Onboarding em ~5 minutos.

  Enterprise —       Infra dedicada por tenant. Kafka, Redis, PostgreSQL,
  cluster dedicado   ClickHouse e GPUs são clusters independentes. Zero
                     blast radius entre tenants. Onboarding em ~2 horas.
  -----------------------------------------------------------------------

14.2 Isolamento por Componente

  -----------------------------------------------------------------------
  Componente         Isolamento por Tenant
  ------------------ ----------------------------------------------------
  Agent Registry     Cada tenant tem seu próprio conjunto de agentes,
                     versões e permissões.

  MCP Servers        Cada chamada carrega tenant_id. Resolução de
                     endpoint correto do sistema do tenant.

  Kafka              Tópicos prefixados por tenant_id (standard).
                     Clusters separados (enterprise).

  Redis              Keyspace prefixado por tenant_id. TTLs e limites de
                     memória configuráveis por tenant.

  PostgreSQL         Schema por tenant (standard). Database dedicado
                     (enterprise).

  ClickHouse         Partição por tenant_id em todas as tabelas com
                     row-level security.

  STT / fine-tuning  Modelo base compartilhado + LoRA adapter por tenant.
                     Dados de um tenant nunca contaminam outro.
  -----------------------------------------------------------------------

14.3 Onboarding Dinâmico

Provisionar um novo tenant não requer deploy de infraestrutura.
Sequência do Tenant Provisioning Service:

1. Cria namespace/schema/keyspace por tier

2. Registra agentes base no Agent Registry do tenant

3. Configura MCP Servers com endpoints do tenant

4. Provisiona canais (WhatsApp, SIP, email domain)

5. Inicializa configuração de modelo do tenant (model_profile padrão)

6. Gera credenciais mcp-server-omnichannel

7. Configura observabilidade (dashboard, alertas)

8. Publica evento: tenant.provisioned

Tier standard: ~5 minutos. Tier enterprise: ~2 horas.

  ---------------- -------------------- --------------------- ----------------
  Etapa            Tier Standard        Tier Enterprise       Owner

  Provisao de      Automatico —         Manual — cluster      Platform
  namespace        namespace K8s criado dedicado provisionado engineering
                   por webhook                                

  Configuracao     API: tenant_id,      Idem + ResourceQuota  Operador via API
  base             workspace_id, tier,  por namespace +       administrativa
                   rate_limits          network policies      

  Configuracao de  Webhook URLs         Idem + SIP trunk para Operador
  canais           (WhatsApp, SMS,      voz + certificados    
                   Email)               mTLS                  

  Primeiro Agent   API: agent_type_id,  Idem +                Time de produto
  Registry         pools, permissoes,   routing_expression    do operador
                   model_profile        customizado por       
                                        tenant                

  Validacao de     agent_login com      Idem + validacao de   Operador +
  conectividade    agente de smoke test latência cross-site < platform
  MCP                                   50ms                  engineering

  Configuracao de  Opt-in padrao por    Idem + politica de    Compliance +
  consent (A6)     canal conforme       silêncio e auditoria  operador
                   regulacao            de consent            

  Ativacao billing Automatico com       Negociado             Comercial +
  e rate limits    tier_id no evento    contratualmente —     plataforma
                   tenant.provisioned   limites customizados  
  ---------------- -------------------- --------------------- ----------------

15. Gestão de Conhecimento — mcp-server-knowledge
