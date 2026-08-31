import { getAccessToken, reauthorize } from '@/auth/token-store'

/**
 * apiFetch — `fetch` que anexa `Authorization: Bearer` do token em memória (quando há e
 * ainda não foi setado), e que **reage a 401 renovando a sessão uma vez**. USAR em TODA
 * chamada de relatório/leitura gateada por ABAC: `/reports/*`, `/v1/evaluation/*`,
 * `/analytics/*`, `/v1/operational/*`.
 *
 * Motivo (arco de segurança, 2026-07-23): sem o token o backend não sabe quem chama.
 * ⚠️ A consequência INVERTEU em 2026-08-31 (AUT-17/AUT-23): antes a chamada sem Bearer
 * degradava ABERTA (via o tenant inteiro); hoje ela recebe **domínio vazio** e não vê
 * nada. Anexar o Bearer deixou de ser o que amplia e passou a ser o que HABILITA —
 * tela não migrada para cá agora aparece vazia, não completa. Não é monkey-patch do
 * fetch global (que vazaria token em auth/refresh/CDN) — é explícito, consistente com o
 * `bearer()` do `api/registry.ts`.
 *
 * ── Re-auth reativo (AUT-19, 2026-08-31) ────────────────────────────────────────
 * Até aqui a renovação era só por TIMER (60 s antes de expirar). Isso basta enquanto a
 * aba está viva e acordada; não basta para aba suspensa, laptop que dormiu, relógio
 * atrasado, ou segredo rotacionado no servidor — casos em que o token parece válido
 * LOCALMENTE e o servidor o recusa. Por isso a renovação aqui é FORÇADA
 * (`reauthorize()`), não `getAccessToken()`: este último devolveria o mesmo token
 * stale, porque localmente ele ainda não expirou, e a retentativa levaria outro 401.
 *
 * **Uma retentativa, nunca duas.** Se o 401 persistir depois de uma sessão nova, o
 * problema não é a credencial — insistir viraria laço contra o servidor e esconderia a
 * causa real (por exemplo, uma rota que responde 401 por outro motivo).
 */
export function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  // Quem passou o próprio `Authorization` é dono da credencial daquela chamada: não
  // sobrescrevemos, e também não renovamos por ela.
  const credencialDoChamador = headers.has('Authorization')

  const token = getAccessToken()
  if (token && !credencialDoChamador) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  return fetch(input, { ...init, headers }).then(async (res) => {
    if (res.status !== 401 || credencialDoChamador) return res

    const novo = await reauthorize()
    // Sem sessão renovável (refresh recusado, ou usuário nunca logou): devolve o 401
    // ORIGINAL. A tela precisa vê-lo para mandar ao login — engoli-lo aqui seria a
    // degradação silenciosa que este arco inteiro existe para remover.
    if (!novo) return res

    const retryHeaders = new Headers(init.headers)
    retryHeaders.set('Authorization', `Bearer ${novo}`)
    return fetch(input, { ...init, headers: retryHeaders })
  })
}
