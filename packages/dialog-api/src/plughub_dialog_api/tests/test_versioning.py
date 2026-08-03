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

from ..db import _row_to_form, db_publish_form, db_put_form

_T   = "tenant_test"
_FID = "dialog_nps_buttons"
_NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def _row(version: int, status: str, doc: dict | None = None) -> dict:
    stored = doc or {"nodes": [], "status": "draft", "version": version}
    return {
        "tenant_id": _T, "form_id": _FID, "version": version, "status": status,
        "name": "NPS", "tags": json.dumps([]), "json": json.dumps(stored),
        "created_at": _NOW, "updated_at": _NOW,
    }


class _Conn:
    """Conn que REGISTRA o SQL emitido e responde por ordem de chamada declarada."""

    def __init__(self, fetchrow_returns: list, fetchval_return=None):
        self.sql: list[str] = []
        self.args: list[tuple] = []
        self._returns = list(fetchrow_returns)
        self._fetchval = fetchval_return

    async def fetchrow(self, sql, *args):
        self.sql.append(" ".join(sql.split()))
        self.args.append(args)
        return self._returns.pop(0) if self._returns else None

    async def fetchval(self, sql, *args):
        self.sql.append(" ".join(sql.split()))
        self.args.append(args)
        return self._fetchval

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
        assert "UPDATE dialog.forms" in conn.sql[1]
        assert conn.args[1][2] == 2          # mesma versão

    async def test_edit_over_PUBLISHED_creates_a_new_draft_version(self):
        """A garantia central: publicado é imutável para o editor.

        Se este caminho virasse UPDATE, editar o formulário mudaria o que já está em
        produção — sem erro, sem nova versão, sem rastro. Exatamente o defeito do
        `skill.flow_draft`.
        """
        conn = _Conn([_row(2, "published"), _row(3, "draft")])
        out = await db_put_form(_pool(conn), _T, _FID, {"name": "NPS v3", "nodes": []})
        assert "INSERT INTO dialog.forms" in conn.sql[1]
        assert "UPDATE" not in conn.sql[1]
        assert conn.args[1][2] == 3          # versão nova = latest + 1
        assert out["status"] == "draft"

    async def test_first_edit_starts_at_version_1(self):
        conn = _Conn([None, _row(1, "draft")])
        await db_put_form(_pool(conn), _T, _FID, {"name": "novo", "nodes": []})
        assert "INSERT INTO dialog.forms" in conn.sql[1]
        assert conn.args[1][2] == 1

    async def test_new_draft_is_born_as_draft_in_the_snapshot_too(self):
        """O JSON gravado carimba `status: draft` — nada nasce publicado por omissão."""
        conn = _Conn([_row(2, "published"), _row(3, "draft")])
        await db_put_form(_pool(conn), _T, _FID, {"name": "x", "nodes": []})
        stored = json.loads(conn.args[1][5])
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
        sel = conn.sql[0]
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
        assert conn.args[0][2] == 2
