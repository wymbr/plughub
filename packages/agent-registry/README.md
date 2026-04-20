# @plughub/agent-registry

API Administrativa da **PlugHub Platform** — registro de pools, tipos de agente e skills.

## Iniciar

```bash
npm install
npx prisma generate
npx prisma migrate dev
npm run dev
```

Variáveis de ambiente:
```
DATABASE_URL=postgresql://user:pass@localhost:5432/plughub
PORT=3300
```

## Rotas

### Pools
| Método | Rota | Descrição |
|---|---|---|
| POST | `/v1/pools` | Registrar pool |
| GET | `/v1/pools` | Listar pools do tenant |
| GET | `/v1/pools/:pool_id` | Consultar pool |
| PUT | `/v1/pools/:pool_id` | Atualizar pool |

### Agent Types
| Método | Rota | Descrição |
|---|---|---|
| POST | `/v1/agent-types` | Registrar tipo de agente |
| GET | `/v1/agent-types` | Listar (filtro: pool_id, role) |
| GET | `/v1/agent-types/:id` | Consultar tipo |

### Skills
| Método | Rota | Descrição |
|---|---|---|
| POST | `/v1/skills` | Registrar skill |
| GET | `/v1/skills` | Listar (filtro: type, vertical, domain) |
| GET | `/v1/skills/:skill_id` | Consultar skill |

## Validações cruzadas

Implementadas na camada de serviço — não nos schemas Zod:

- `pools` em AgentType devem existir no registry
- `skills` em AgentType devem existir no registry
- `skill_id` e `agent_type_id` são imutáveis — criar nova versão (`_v2`)
- Pools não podem ser deletados — apenas `status: inactive`

## Testes

```bash
npm test
```
