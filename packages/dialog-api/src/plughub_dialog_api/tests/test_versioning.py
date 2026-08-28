"""
test_versioning.py
Primeira suíte do dialog-api (2026-08-03).

POR QUE O VERSIONAMENTO. O `DialogForm` é servido ao dialog-runner e às três superfícies
(chat, inline, página web). A regra que o store guarda e que nenhum smoke isola é a
separação **rascunho × publicado**:

  · editar um form publicado NÃO altera o publicado — cria um rascunho novo;
  · publicar NÃO reescreve o conteúdo — promove a versão existente.

O precedente que torna isso caro é do próprio projeto: em `skill.flow_draft` o upsert
incondicional levava o rascunho a produção **e apagava o rascunho do editor** a cada boot
(`CLAUDE.md` § Instance Bootstrap). O modo de falha ali não foi erro — foi conteúdo errado
rodando em produção sem nada ficar vermelho. A mesma forma de defeito cabe aqui, e a
diferença entre as duas é UMA cláusula SQL.

Por isso as asserções olham o **SQL emitido** (INSERT × UPDATE, e o que o UPDATE toca),
não só o dicionário devolvido: o valor de retorno é igual nos dois caminhos, e é o comando
que distingue "criou versão" de "sobrescreveu produção".
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import json
import pytest

from ..db import (
    FormArchivedError,
    _row_to_form,
    _row_to_meta,
    db_create_form,
    db_delete_form,
    db_get_form,
    db_list_forms,
    db_publish_form,
    db_put_form,
    db_undelete_form,
)

_T   = "tenant_test"
_FID = "dialog_nps_buttons"
_NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
_ARQ = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)


def _row(version: int, status: str, doc: dict | None = None, deleted_at=None) -> dict:
    stored = doc or {"nodes": [], "status": "draft", "version": version}
    return {
        "tenant_id": _T, "form_id": _FID, "version": version, "status": status,
        "name": "NPS", "tags": json.dumps([]), "json": json.dumps(stored),
        "created_at": _NOW, "updated_at": _NOW, "deleted_at": deleted_at,
    }


def _state(versions=1, published=0, max_version=0, deleted_at=None) -> dict:
    """Linha do agregado `_SQL_FORM_STATE` (existe? já publicou? está arquivado?)."""
    return {"versions": versions, "published_versions": published,
            "max_version": max_version, "deleted_at": deleted_at}


class _Conn:
    """Conn que REGISTRA o SQL emitido e responde por ordem de chamada declarada.

    A consulta de ESTADO (o agregado que o guard usa) é respondida à parte, por `state`, e
    não consome a fila posicional: ela é pergunta de controle, não passo do caminho medido.
    Continua entrando em `self.sql` — o teste que quiser afirmar sobre ela pode.
    """

    def __init__(self, fetchrow_returns: list, fetchval_return=None, state: dict | None = None,
                 fetch_returns: list | None = None):
        self.sql: list[str] = []
        self.args: list[tuple] = []
        self._returns = list(fetchrow_returns)
        self._fetchval = fetchval_return
        self._state = state if state is not None else _state()
        self._fetch = list(fetch_returns or [])

    def _is_state(self, sql: str) -> bool:
        return "published_versions" in sql

    async def fetchrow(self, sql, *args):
        flat = " ".join(sql.split())
        self.sql.append(flat)
        self.args.append(args)
        if self._is_state(flat):
            return self._state
        return self._returns.pop(0) if self._returns else None

    async def fetchval(self, sql, *args):
        self.sql.append(" ".join(sql.split()))
        self.args.append(args)
        return self._fetchval

    async def fetch(self, sql, *args):
        self.sql.append(" ".join(sql.split()))
        self.args.append(args)
        return self._fetch.pop(0) if self._fetch else []

    def transaction(self):
        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=None)
        tx.__aexit__ = AsyncMock(return_value=False)
        return tx


def _pool(conn: _Conn):
    acq = MagicMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acq)
    return pool


# ── _row_to_form ──────────────────────────────────────────────────────────────

class TestRowToForm:
    def test_row_columns_WIN_over_the_json_snapshot(self):
        """A coluna manda; o snapshot é histórico.

        O `json` gravado carrega `status: "draft"` mesmo depois do publish — o publish só
        atualiza a COLUNA `status` (`db.py:238`). Sem o merge de `_row_to_form`, um form
        publicado se declararia rascunho para quem o lê, e o runner não teria como saber
        o que está em produção. É uma linha fácil de "simplificar" por engano.
        """
        row = _row(3, "published", {"nodes": [], "status": "draft", "version": 1,
                                    "tenant_id": "outro_tenant"})
        doc = _row_to_form(row)
        assert doc["status"] == "published"
        assert doc["version"] == 3
        assert doc["tenant_id"] == _T

    def test_content_survives_the_merge(self):
        row = _row(1, "draft", {"nodes": [{"id": "q1"}], "default_locale": "pt-BR"})
        doc = _row_to_form(row)
        assert doc["nodes"] == [{"id": "q1"}]
        assert doc["default_locale"] == "pt-BR"


# ── db_put_form ───────────────────────────────────────────────────────────────

class TestPutForm:
    async def test_edit_over_DRAFT_replaces_in_place(self):
        conn = _Conn([_row(2, "draft"), _row(2, "draft")])
        await db_put_form(_pool(conn), _T, _FID, {"name": "NPS v2", "nodes": []})
        assert "UPDATE dialog.forms" in conn.sql[-1]
        assert conn.args[-1][2] == 2         # mesma versão

    async def test_edit_over_PUBLISHED_creates_a_new_draft_version(self):
        """A garantia central: publicado é imutável para o editor.

        Se este caminho virasse UPDATE, editar o formulário mudaria o que já está em
        produção — sem erro, sem nova versão, sem rastro. Exatamente o defeito do
        `skill.flow_draft`.
        """
        conn = _Conn([_row(2, "published"), _row(3, "draft")])
        out = await db_put_form(_pool(conn), _T, _FID, {"name": "NPS v3", "nodes": []})
        assert "INSERT INTO dialog.forms" in conn.sql[-1]
        assert "UPDATE" not in conn.sql[-1]
        assert conn.args[-1][2] == 3         # versão nova = latest + 1
        assert out["status"] == "draft"

    async def test_first_edit_starts_at_version_1(self):
        conn = _Conn([None, _row(1, "draft")])
        await db_put_form(_pool(conn), _T, _FID, {"name": "novo", "nodes": []})
        assert "INSERT INTO dialog.forms" in conn.sql[-1]
        assert conn.args[-1][2] == 1

    async def test_new_draft_is_born_as_draft_in_the_snapshot_too(self):
        """O JSON gravado carimba `status: draft` — nada nasce publicado por omissão."""
        conn = _Conn([_row(2, "published"), _row(3, "draft")])
        await db_put_form(_pool(conn), _T, _FID, {"name": "x", "nodes": []})
        stored = json.loads(conn.args[-1][5])
        assert stored["status"] == "draft"
        assert stored["version"] == 3


# ── db_publish_form ───────────────────────────────────────────────────────────

class TestPublishForm:
    async def test_publish_does_NOT_rewrite_the_content(self):
        """O UPDATE do publish toca `status` e `updated_at` — e nada mais.

        Se ele passasse a escrever `json`, o publish deixaria de ser promoção e viraria
        gravação: o conteúdo publicado poderia divergir do que foi revisado, e a diferença
        não apareceria em lugar nenhum.
        """
        conn = _Conn([_row(2, "published")], fetchval_return=2)
        await db_publish_form(_pool(conn), _T, _FID)
        upd = next(s for s in conn.sql if s.startswith("UPDATE dialog.forms"))
        assert "SET status='published'" in upd
        assert "json=" not in upd
        assert "name=" not in upd

    async def test_default_target_is_the_latest_DRAFT(self):
        conn = _Conn([_row(5, "published")], fetchval_return=5)
        await db_publish_form(_pool(conn), _T, _FID)
        sel = next(s for s in conn.sql if "status='draft'" in s)
        assert "status='draft'" in sel
        assert "ORDER BY version DESC LIMIT 1" in sel

    async def test_no_draft_returns_None_without_publishing_anything(self):
        """Sem rascunho, não se promove nada — em vez de republicar a versão corrente.

        Republicar seria idempotente na aparência e mentiroso no registro: um
        `updated_at` novo sugerindo mudança que não houve.
        """
        conn = _Conn([], fetchval_return=None)
        out = await db_publish_form(_pool(conn), _T, _FID)
        assert out is None
        assert not any(s.startswith("UPDATE") for s in conn.sql)

    async def test_explicit_version_skips_the_draft_lookup(self):
        conn = _Conn([_row(2, "published")])
        await db_publish_form(_pool(conn), _T, _FID, version=2)
        assert not any("status='draft'" in s for s in conn.sql)
        assert conn.args[-1][2] == 2


# ── Arquivamento (ADR adr-dialog-form-deletion) ───────────────────────────────
#
# As asserções aqui olham o COMANDO emitido pelo mesmo motivo da suíte acima: o valor de
# retorno de "arquivou" e de "purgou" tem a mesma forma, e é o SQL que distingue o
# reversível do irreversível. E há uma segunda razão, própria deste arco: as duas metades
# da decisão (o catálogo fecha × a resolução NÃO fecha) são *ausência* e *presença* de uma
# cláusula WHERE — nenhum teste de valor de retorno as separa.

class TestWriteGuard:
    """Escrever sobre arquivado é recusado nos TRÊS escritores — e sem tocar em nada.

    Um guard que levantasse DEPOIS do INSERT/UPDATE deixaria o form publicado e fora do
    catálogo ao mesmo tempo; o teste afirma a recusa E o não-efeito.
    """

    async def test_put_over_archived_raises_and_writes_nothing(self):
        conn = _Conn([_row(2, "published", deleted_at=_ARQ)], state=_state(2, 1, 2, _ARQ))
        with pytest.raises(FormArchivedError):
            await db_put_form(_pool(conn), _T, _FID, {"name": "x", "nodes": []})
        assert not any(s.startswith(("INSERT", "UPDATE")) for s in conn.sql)

    async def test_publish_over_archived_raises_and_writes_nothing(self):
        conn = _Conn([], fetchval_return=2, state=_state(2, 1, 2, _ARQ))
        with pytest.raises(FormArchivedError):
            await db_publish_form(_pool(conn), _T, _FID)
        assert not any(s.startswith("UPDATE") for s in conn.sql)

    async def test_create_over_archived_raises_and_writes_nothing(self):
        """Criar versão nova num id arquivado é o caso caro: o conteúdo novo herdaria um id
        ao qual um slot antigo ainda aponta, e o slot passaria a executar outra coisa sem
        ninguém ter tocado no deploy."""
        conn = _Conn([], state=_state(3, 1, 3, _ARQ))
        with pytest.raises(FormArchivedError):
            await db_create_form(_pool(conn), _T, {"form_id": _FID, "nodes": []})
        assert not any(s.startswith("INSERT") for s in conn.sql)

    async def test_live_form_is_not_blocked(self):
        """Testemunha de presença: sem ela, um guard que recusasse SEMPRE passaria acima."""
        conn = _Conn([_row(2, "draft"), _row(2, "draft")], state=_state(2, 0, 2, None))
        await db_put_form(_pool(conn), _T, _FID, {"name": "ok", "nodes": []})
        assert "UPDATE dialog.forms" in conn.sql[-1]


class TestDeleteForm:
    async def test_never_published_is_PURGED(self):
        """Nenhuma versão publicada ⇒ nenhum leitor de runtime alcança o form (todos
        resolvem status='published') ⇒ apagar de verdade não derruba ninguém."""
        conn = _Conn([], state=_state(versions=2, published=0, max_version=2),
                     fetch_returns=[[{"version": 1}, {"version": 2}]])
        out = await db_delete_form(_pool(conn), _T, _FID)
        assert out["purged"] is True
        assert out["versions"] == 2
        assert any(s.startswith("DELETE FROM dialog.forms") for s in conn.sql)
        assert not any("deleted_at = now()" in s for s in conn.sql)

    async def test_published_is_ARCHIVED_never_purged(self):
        conn = _Conn([], state=_state(versions=3, published=1, max_version=3),
                     fetch_returns=[[{"deleted_at": _ARQ}, {"deleted_at": _ARQ},
                                     {"deleted_at": _ARQ}]])
        out = await db_delete_form(_pool(conn), _T, _FID)
        assert out["purged"] is False
        assert out["deleted_at"] == _ARQ.isoformat()
        assert not any(s.startswith("DELETE FROM") for s in conn.sql)
        upd = next(s for s in conn.sql if s.startswith("UPDATE dialog.forms"))
        assert "deleted_at = now()" in upd

    async def test_archive_does_NOT_touch_content_or_updated_at(self):
        """`updated_at` fala do CONTEÚDO. Arquivar não muda conteúdo — e a data do
        arquivamento tem coluna própria. Mesma regra do publish, que não reescreve `json`."""
        conn = _Conn([], state=_state(versions=1, published=1, max_version=1),
                     fetch_returns=[[{"deleted_at": _ARQ}]])
        await db_delete_form(_pool(conn), _T, _FID)
        upd = next(s for s in conn.sql if s.startswith("UPDATE dialog.forms"))
        assert "updated_at" not in upd
        assert "json=" not in upd

    async def test_re_archiving_preserves_the_ORIGINAL_timestamp(self):
        """Idempotente por preservação, não por repetição: re-carimbar apagaria QUANDO o
        form saiu de circulação, que é o dado pelo qual alguém vai perguntar depois."""
        conn = _Conn([], state=_state(versions=2, published=1, max_version=2, deleted_at=_ARQ))
        out = await db_delete_form(_pool(conn), _T, _FID)
        assert out["already_deleted"] is True
        assert out["deleted_at"] == _ARQ.isoformat()
        assert not any(s.startswith(("UPDATE", "DELETE")) for s in conn.sql)

    async def test_missing_form_returns_None_without_touching_anything(self):
        conn = _Conn([], state=_state(versions=0))
        out = await db_delete_form(_pool(conn), _T, _FID)
        assert out is None
        assert not any(s.startswith(("UPDATE", "DELETE")) for s in conn.sql)


class TestUndeleteForm:
    async def test_clears_the_stamp_on_every_version(self):
        conn = _Conn([], state=_state(versions=2, published=1, max_version=2, deleted_at=_ARQ),
                     fetch_returns=[[{"version": 1}, {"version": 2}]])
        out = await db_undelete_form(_pool(conn), _T, _FID)
        assert out["was_deleted"] is True
        assert out["restored_versions"] == 2
        upd = next(s for s in conn.sql if s.startswith("UPDATE dialog.forms"))
        assert "deleted_at = NULL" in upd

    async def test_live_form_is_a_no_op_and_says_so(self):
        conn = _Conn([], state=_state(versions=1, published=1, max_version=1))
        out = await db_undelete_form(_pool(conn), _T, _FID)
        assert out["was_deleted"] is False
        assert out["restored_versions"] == 0

    async def test_missing_form_returns_None(self):
        conn = _Conn([], state=_state(versions=0))
        assert await db_undelete_form(_pool(conn), _T, _FID) is None


class TestCatalogVsResolution:
    """As duas metades da D1. Elas moram na MESMA cláusula, em consultas diferentes — é por
    isso que precisam ser afirmadas juntas: consertar uma sem a outra parece certo."""

    def _fetch_pool(self):
        pool = MagicMock()
        sql: list[str] = []

        async def _fetch(q, *a):
            sql.append(" ".join(q.split()))
            return []

        pool.fetch = _fetch
        return pool, sql

    def _fetchrow_pool(self):
        pool = MagicMock()
        sql: list[str] = []

        async def _fetchrow(q, *a):
            sql.append(" ".join(q.split()))
            return None

        pool.fetchrow = _fetchrow
        return pool, sql

    async def test_catalog_carries_EVER_PUBLISHED_not_just_the_current_status(self):
        """A tela precisa saber se o DELETE vai arquivar ou PURGAR *antes* de perguntar.
        `status` desta linha não responde: a última versão pode ser rascunho e existir uma
        publicada mais antiga — nesse caso o form NÃO pode ser purgado."""
        pool, sql = self._fetch_pool()
        await db_list_forms(pool, _T)
        assert "ever_published" in sql[0]
        row = dict(_row(3, "draft"), ever_published=True)
        assert _row_to_meta(row)["ever_published"] is True
        row = dict(_row(1, "draft"), ever_published=False)
        assert _row_to_meta(row)["ever_published"] is False

    async def test_resolution_does_NOT_filter_archived(self):
        """A metade da D1 que só se afirma pela AUSÊNCIA de uma cláusula — e a que alguém
        vai 'consertar' um dia, por simetria com o catálogo. Os três caminhos de resolução
        (por versão, por published, o corrente) têm de ficar sem filtro."""
        for kwargs in ({"status": "published"}, {"version": 2}, {}):
            pool, sql = self._fetchrow_pool()
            await db_get_form(pool, _T, _FID, **kwargs)
            assert "deleted_at" not in sql[0], f"filtro vazou em {kwargs}"

    async def test_catalog_hides_archived_by_default(self):
        pool, sql = self._fetch_pool()
        await db_list_forms(pool, _T)
        assert "deleted_at IS NULL" in sql[0]

    async def test_trash_view_includes_them(self):
        pool, sql = self._fetch_pool()
        await db_list_forms(pool, _T, include_deleted=True)
        assert "deleted_at IS NULL" not in sql[0]

    def test_resolution_carries_the_stamp_instead_of_hiding_the_form(self):
        """`db_get_form` não filtra — serve arquivado, e o corpo DIZ que é arquivado. Se um
        dia alguém puser o filtro lá, o contato em andamento cai e o seed ressuscita o form
        no boot seguinte."""
        doc = _row_to_form(_row(2, "published", deleted_at=_ARQ))
        assert doc["deleted_at"] == _ARQ.isoformat()
        assert doc["status"] == "published"
