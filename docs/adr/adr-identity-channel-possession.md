# ADR: Plataforma como autoridade de posse de canal (OTP) + `verification_class`

**Status:** Aceito — implementado (2026-07-04, Identity Resolver nível b, Fase B). Ver `CHANGELOG.md`.
**Data:** 2026-07-04
**Componentes:** `packages/channel-gateway` (`identity/`), `packages/mcp-server-plughub`, `packages/skill-flow-engine` (skills de intake)
**Relacionado:** `docs/product/identity-resolver-nivel-b-spec.md` (§4.4, princípio 7, §5, §11), Thread A (reconexão cross-canal)

---

## Contexto

A identidade progressiva anexa âncoras (telefone/e-mail/…) a um cliente à medida que
aparecem, para viabilizar resolução e retomada cross-canal. Mas anexar uma âncora só
porque o cliente **a digitou** afirma "essa âncora pertence a esse cliente" **sem prova**.
Combinado com a retomada cross-canal de `customer_resumable`, isso é vetor de
**account-takeover / enumeração**: um terceiro informa o número de outra pessoa e passa a
resolver as pendências e o histórico dela. O risco é ampliado porque quem "valida" no meio
de um fluxo pode ser um agente **IA** lendo input do cliente.

A verificação forte de **identidade-de-registro** (quem a pessoa legalmente é) é do
contratante (CRM/auth federada da loja) — a plataforma **não** é e não quer ser essa
autoridade. Mas há um grau intermediário, barato e universal, que a plataforma **pode**
atestar: **posse do canal** (o cliente controla aquele telefone/e-mail agora).

## Decisão

1. **A plataforma é autoridade de _posse de canal_, não de identidade-de-registro.** Prova
   posse via OTP (código enviado ao canal da âncora). Nunca armazena credenciais nem
   templates biométricos (tratados como masked input, in-memory, descartados). Isso **emenda
   o princípio 7 / §4.4** do spec: além de "só chaves + atributos mascarados", a plataforma
   agora carimba **como** cada chave foi conhecida.

2. **`verification_class` é fato do dado, sempre presente.** Cada âncora carrega
   `claimed` (digitada/afirmada) ou `possessed` (provada por OTP), no índice Redis
   (`{cid, vc}`, leitor tolerante) e no PG (`customer_secondary_keys.verification_class`).
   A confiança de desambiguação é `f(kind, classe)`: qualquer `possessed` supera qualquer
   `claimed`.

3. **Verificar é opcional; confiar é consequência.** O OTP é um **serviço componível**
   (`otp_challenge`/`otp_verify`), acionado a critério do fluxo — nunca implícito. Uma âncora
   só vira `possessed` **via OTP**; as tools de enriquecimento (`customer_attach_key`) só
   escrevem `claimed`. Invariante: **`possessed` ⟺ verificado**.

4. **Um único default seguro de plataforma:** a **retomada cross-canal de `customer_resumable`
   exige âncora `possessed`.** O tool `pending_workflow_get` não devolve `resume_token`/contexto
   quando a resolução é apenas `claimed`; devolve `verification_required` **sem revelar se há
   pendência** (anti-enumeração). O fluxo pode então oferecer OTP e re-consultar. Todo o resto
   (quando/onde pedir OTP) é régua do fluxo.

## Consequências

- **Progressiva fica segura:** anexar uma âncora `claimed` não destrava ação sensível; o poder
  vem só da posse provada. "Não fazer nada" é seguro; step-up é opt-in explícito.
- **Muda UX:** a reconexão cross-canal (que no Thread A passava com telefone `claimed`) passa a
  exigir um passo de OTP. É o ponto da feature.
- **OTP generaliza:** por ser serviço agnóstico de identidade, serve step-up para qualquer ação
  sensível futura (pagamento, revelar dado mascarado), não só identidade.
- **Entrega mockada no demo:** o código só vai a log/`dev_code` sob `PLUGHUB_OTP_DEV_RETURN_CODE`;
  em produção entrega real pelo canal e **nunca** loga o código. O transporte real (adapter
  outbound, idealmente por canal diferente do da sessão) é follow-up.

## Alternativas descartadas

- **Confiança 100% a critério do fluxo** (sem default de plataforma): empurra a segurança de
  cada ação para cada autor de fluxo — um esquecimento reabre o takeover. Rejeitado: o caminho
  sensível tem default seguro estrutural.
- **Plataforma como autoridade de identidade-de-registro:** amplia superfície de responsabilidade
  (credenciais, biometria) sem necessidade — a identidade legal é do CRM do tenant.

## Fora de escopo

Merge de clientes e `external_refs` (Fase C / quando houver CRM); origem `resume_origin=same_channel`
(continuidade intra-canal, platform-level); transporte real do OTP.
