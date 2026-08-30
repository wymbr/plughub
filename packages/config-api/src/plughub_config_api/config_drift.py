"""
config_drift.py — comparação declarado × gravado para o seed (D7 do arco ALLOWLIST).

── Por que existe ───────────────────────────────────────────────────────────────

O seed é *seed-if-absent*: a key existe ⇒ pula. Isso é a política certa (o DB é a
fonte de verdade depois do primeiro boot), mas até 2026-08-29 o pulo era **MUDO** —
`skipped += 1` e nada mais. O modo de falha não é hipotético; foi medido duas vezes
na mesma sessão:

  · `masking.types` ganhou o tipo `texto` na declaração e a base seguiu com 10
    tipos. Nada disse nada. Só apareceu porque um gate vizinho reprovou.
  · `masking.context_rules` está com **23 regras declaradas e 14 gravadas** —
    uma base semeada hoje herdaria a política anterior ao conserto de 2026-08-26,
    sem os globs de sufixo.

── Por que COMPARA e LOGA, e nunca conserta ────────────────────────────────────

A tentação é o seed reaplicar sozinho o que difere. Medido no `__global__` vivo, a
divergência do `masking.context_rules` é **BIDIRECIONAL**: 10 regras só no
declarado *e* uma só no gravado (`session.cpf_titular`, que nenhum glob declarado
cobre — `*.cpf` casa o sufixo `.cpf`, não `cpf_titular`). Uma sobrescrita cega
apagaria essa regra e o campo cairia em `default_unmatched_operator: "plain"`.

Ou seja: a divergência **carrega informação nas duas direções**, e escolher uma
delas é decisão de política, não de mecanismo. O seed nomeia; quem decide é gente.

Por isso o relatório separa as duas direções e marca `overwrite_would_drop` — é o
único número que responde *"posso reaplicar esta key sem perder nada?"*.

Sem dependência de DB de propósito: assim o comparador é testável como função pura,
sem subir Postgres nem Redis.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Chaves que servem de IDENTIDADE de item numa lista, em ordem de preferência.
# Sem isto, uma lista de regras diffa por ÍNDICE e inserir um item no meio faz
# todos os seguintes parecerem alterados — ruído que esconde a mudança real.
_IDENTITY_KEYS = ("id", "key", "pattern", "tag", "category", "name", "slug")

# Teto de itens NOMEADOS por direção no log. O que passar disso é CONTADO, nunca
# truncado em silêncio (ver `truncated`).
MAX_NAMED = 12


def canonical(value: Any) -> str:
    """Forma canônica para comparação — ordem de chave não é diferença."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _identity_key(items: list[Any]) -> str | None:
    """
    Devolve a chave que identifica os itens de `items`, ou None se não houver uma
    boa. Exige que TODOS os itens a tenham e que os valores sejam ÚNICOS — chave
    repetida identificaria dois itens como um só, o que é pior que diffar por
    índice, porque some com um deles sem dizer.
    """
    if not items or not all(isinstance(i, dict) for i in items):
        return None
    for cand in _IDENTITY_KEYS:
        if all(cand in i for i in items):
            values = [canonical(i[cand]) for i in items]
            if len(set(values)) == len(values):
                return cand
    return None


@dataclass
class Divergence:
    """
    Relatório de UMA key. `only_declared` é o que a reaplicação ACRESCENTARIA;
    `only_stored` + `changed` é o que ela DESCARTARIA — e é essa metade que decide
    se `--overwrite` é seguro.
    """
    only_declared: list[str] = field(default_factory=list)
    only_stored:   list[str] = field(default_factory=list)
    changed:       list[str] = field(default_factory=list)

    @property
    def overwrite_would_drop(self) -> int:
        return len(self.only_stored) + len(self.changed)

    @property
    def total(self) -> int:
        return len(self.only_declared) + self.overwrite_would_drop

    def _fmt(self, label: str, items: list[str]) -> str:
        if not items:
            return ""
        named = ", ".join(items[:MAX_NAMED])
        rest  = len(items) - MAX_NAMED
        more  = f" (+{rest} não nomeados)" if rest > 0 else ""
        return f"{label}={len(items)} [{named}{more}]"

    def summary(self) -> str:
        parts = [
            self._fmt("só no DECLARADO", self.only_declared),
            self._fmt("só no GRAVADO",   self.only_stored),
            self._fmt("DIFEREM",         self.changed),
        ]
        return "; ".join(p for p in parts if p)


def _item_label(path: str, ident_json: str) -> str:
    """`rules` + `"*.cpf"` -> `rules[*.cpf]`. Separado da f-string porque uma
    contrabarra dentro de expressao de f-string so e legal a partir do 3.12, e o
    servico roda 3.11 — o host parseava e o container nao."""
    try:
        ident = json.loads(ident_json)
    except (ValueError, TypeError):
        ident = ident_json
    # Sem prefixo (o valor da key JA e a lista, como `agent_activity.pause_reasons`)
    # o rotulo sai sem colchete: `[[almoco]]` nao diz de que lista, e a linha do log
    # ja nomeia a key. Medido pelo gate em 2026-08-29, que reprovou por isto.
    if not path:
        return str(ident)
    return f"{path}[{ident}]"


def _walk(declared: Any, stored: Any, path: str, out: Divergence) -> None:
    if isinstance(declared, dict) and isinstance(stored, dict):
        for k in declared:
            sub = f"{path}.{k}" if path else k
            if k not in stored:
                out.only_declared.append(sub)
            else:
                _walk(declared[k], stored[k], sub, out)
        for k in stored:
            if k not in declared:
                out.only_stored.append(f"{path}.{k}" if path else k)
        return

    if isinstance(declared, list) and isinstance(stored, list):
        # A identidade precisa valer nas DUAS listas. Aceitar a da segunda quando a
        # primeira nao tem uma boa foi um bug real: com `id` repetido no declarado,
        # o dict indexado colapsava os dois itens num so e um deles sumia do
        # relatorio — divergencia escondida por dentro do detector de divergencia.
        # Lista VAZIA nao contradiz identidade nenhuma, logo nao veta.
        di, si = _identity_key(declared), _identity_key(stored)
        if declared and stored:
            ident = di if (di is not None and di == si) else None
        else:
            ident = di if declared else si
        if ident is not None:
            D = {canonical(i[ident]): i for i in declared if isinstance(i, dict) and ident in i}
            S = {canonical(i[ident]): i for i in stored   if isinstance(i, dict) and ident in i}
            for k in D:
                label = _item_label(path, k)
                if k not in S:
                    out.only_declared.append(label)
                else:
                    _walk(D[k], S[k], label, out)
            for k in S:
                if k not in D:
                    out.only_stored.append(_item_label(path, k))
            return
        # Sem identidade: a lista é um valor só. Nomear índices seria inventar
        # precisão que o dado não tem.
        if canonical(declared) != canonical(stored):
            out.changed.append(f"{path} (lista: {len(declared)} declarados × {len(stored)} gravados)")
        return

    if canonical(declared) != canonical(stored):
        out.changed.append(path or "(raiz)")


def describe_divergence(declared: Any, stored: Any) -> Divergence | None:
    """
    None ⇔ os dois valores são o MESMO valor (ordem de chave não conta).

    Devolver None em vez de um relatório vazio é deliberado: torna impossível o
    caller tratar "igual" e "diferente porém sem detalhe" pelo mesmo caminho.
    """
    if canonical(declared) == canonical(stored):
        return None
    out = Divergence()
    _walk(declared, stored, "", out)
    if out.total == 0:
        # Os canônicos diferem e a caminhada não achou ONDE. Só há um caso assim:
        # a ORDEM dos itens de uma lista com identidade. Reordenar regras não muda
        # política nenhuma, e reportá-lo encheria o log de ruído — que é como um
        # log deixa de ser lido. A caminhada é a autoridade; vazia ⇒ igual.
        #
        # (Forma incompatível na raiz — dict × list — NÃO cai aqui: a caminhada
        # não casa nenhum dos dois ramos e registra `(raiz)` pelo comparador final.
        # Guardado por `test_diverge_implica_relatorio_nao_vazio`.)
        return None
    return out
