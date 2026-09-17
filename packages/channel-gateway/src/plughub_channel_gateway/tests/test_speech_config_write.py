"""
test_speech_config_write.py — VOZ-17: a porta que grava modelo, língua e voz.

A proposição: *config de fala que o serviço não serve não chega ao store* — e o que prova isso não
é o 422, é o config-api NÃO ter sido chamado. Um 422 devolvido depois da gravação passaria por
portão exatamente como o 401 da VOZ-27 passaria se o executor já tivesse começado.

O que cada bloco fixa:
  * PORTÃO — `config.channels` em leitura vê o catálogo e NÃO grava; em escrita grava (controle
    positivo, senão "nega tudo" pareceria conferência);
  * RECUSA ANTES — forma inválida nem consulta o serviço (recusa que não precisa de rede), e
    modelo/voz/língua que o serviço não serve não viram escrita;
  * EFETIVO — o que se confere é env ⊕ tenant ⊕ perfil, não o corpo: o perfil que só troca a voz é
    conferido contra o modelo que VAI valer;
  * CREDENCIAL — o gateway repassa o Bearer de quem pediu; ele confere, não empresta poder;
  * SERVIÇO FORA — 503 e nada gravado (decisão do dono: não existe "grava sem conferir");
  * DEFAULT — campo ausente não é tocado, campo vazio REMOVE o override e volta ao env.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from plughub_channel_gateway import main as cg_main
from plughub_channel_gateway import speech_catalog as sc
from plughub_channel_gateway.adapters.webrtc import WebRTCAdapter
from plughub_channel_gateway.main import SpeechDefaultsBody
from plughub_channel_gateway.speech_config import SpeechSegmentationConfig

SECRET = "segredo-de-teste-hs256"
ESCRITA = {"config": {"channels": {"access": "read_write"}}}
LEITURA = {"config": {"channels": {"access": "read_only"}}}

KOKORO = {"id": "speaches-ai/Kokoro-82M-v1.0-ONNX", "task": sc.TTS, "language": ["multilingual"],
          "voices": [{"name": "pf_dora", "language": "pt-br"}]}
PIPER = {"id": "speaches-ai/piper-pt_BR-faber-medium", "task": sc.TTS, "language": ["pt"],
         "voices": [{"name": "faber", "language": "pt"}]}
WHISPER = {"id": "deepdml/faster-whisper-large-v3-turbo-ct2", "task": sc.STT, "language": ["pt", "en"]}
MODELOS = [KOKORO, PIPER, WHISPER]

ENV = {"stt_model": WHISPER["id"], "stt_language": "pt-BR",
       "tts_model": KOKORO["id"], "tts_voice": "pf_dora"}


def _token(module_config=None, tenant="tenant_a", sub="ana") -> str:
    return pyjwt.encode({"sub": sub, "tenant_id": tenant, "module_config": module_config or {},
                         "exp": int(time.time()) + 600}, SECRET, algorithm="HS256")


def _req(mc=None, **extra):
    h = {"authorization": "Bearer " + _token(mc)} if mc is not None else {}
    h.update(extra)
    return SimpleNamespace(headers=h)


class _Resposta:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code, self._payload, self.text = status_code, payload, text

    def json(self):
        if self._payload is None:
            raise ValueError("sem JSON")
        return self._payload


class _Http:
    """O httpx do gateway: serve o catálogo do serviço de fala e as escritas no config-api."""

    def __init__(self, modelos=None, erro_catalogo=None, config_api=None):
        self.modelos = MODELOS if modelos is None else modelos
        self.erro_catalogo = erro_catalogo
        self.config_api = config_api or _Resposta(200, {"ok": True})
        self.catalogos: list[str] = []
        self.escritas: list[dict] = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, timeout=None):
        self.catalogos.append(url)
        if self.erro_catalogo is not None:
            raise self.erro_catalogo
        return _Resposta(200, {"data": self.modelos})

    async def request(self, metodo, url, headers=None, json=None, params=None):
        self.escritas.append({"metodo": metodo, "url": url, "headers": headers,
                              "json": json, "params": params})
        return self.config_api


class _AdapterFalso:
    """A camada de baixo (env) e a do meio (tenant), como o adapter vivo as expõe."""

    def __init__(self, tenant_voice=None):
        self._tenant = dict(tenant_voice or {})
        self.speech_config = SimpleNamespace(voice=self._voice)

    def voice_defaults(self):
        return dict(ENV)

    async def _voice(self, tenant_id):
        return dict(self._tenant), {k: "tenant" for k in self._tenant}


def test_o_falso_nao_inventa_o_alvo():
    """Um mock não verifica que o método existe — ele o CRIA. Estes dois nasceram na VOZ-17 e é o
    produto que tem de tê-los, senão os casos abaixo medem o meu próprio dublê."""
    assert hasattr(WebRTCAdapter, "voice_defaults")
    assert hasattr(SpeechSegmentationConfig, "voice")


@pytest.fixture
def ambiente(monkeypatch):
    s = cg_main.get_settings()
    monkeypatch.setattr(s, "auth_jwt_secret", SECRET, raising=False)
    monkeypatch.setattr(s, "webrtc_speaches_url", "http://speaches:8000", raising=False)
    monkeypatch.setattr(s, "config_api_url", "http://config-api:3600", raising=False)

    def montar(http=None, tenant_voice=None):
        http = http or _Http()
        monkeypatch.setattr(cg_main.httpx, "AsyncClient", http)
        monkeypatch.setattr(cg_main, "_webrtc_adapter", _AdapterFalso(tenant_voice))
        return http

    return SimpleNamespace(montar=montar)


PERFIL_BOM = {"description": "tronco sip", "stt_model": WHISPER["id"], "stt_end_silence_ms": 1500}


class TestPortao:
    async def test_catalogo_sem_credencial_401_e_nao_consulta_o_servico(self, ambiente):
        http = ambiente.montar()
        with pytest.raises(HTTPException) as e:
            await cg_main.speech_models(_req())
        assert e.value.status_code == 401 and http.catalogos == []

    async def test_catalogo_com_leitura_responde(self, ambiente):
        """CONTROLE POSITIVO: quem só olha canais vê o que existe."""
        ambiente.montar()
        r = await cg_main.speech_models(_req(LEITURA))
        assert [m["id"] for m in r["stt"]] == [WHISPER["id"]]
        assert [m["id"] for m in r["tts"]] == [KOKORO["id"], PIPER["id"]]
        assert r["tts"][0]["voices"][0]["name"] == "pf_dora"

    async def test_gravar_perfil_com_leitura_403_e_nada_escrito(self, ambiente):
        http = ambiente.montar()
        with pytest.raises(HTTPException) as e:
            await cg_main.speech_profile_put("sip", _req(LEITURA), dict(PERFIL_BOM))
        assert e.value.status_code == 403 and http.escritas == []

    async def test_gravar_perfil_com_escrita_passa(self, ambiente):
        """CONTROLE POSITIVO do portão E da conferência: config que o serviço serve é gravada."""
        http = ambiente.montar()
        r = await cg_main.speech_profile_put("sip", _req(ESCRITA), dict(PERFIL_BOM))
        assert r.status_code == 200 and len(http.escritas) == 1
        e = http.escritas[0]
        assert e["metodo"] == "PUT" and e["url"].endswith("/config/speech_profiles/sip")
        assert e["json"] == {"value": PERFIL_BOM, "tenant_id": "tenant_a"}

    async def test_apagar_perfil_exige_escrita(self, ambiente):
        http = ambiente.montar()
        with pytest.raises(HTTPException) as e:
            await cg_main.speech_profile_delete("sip", _req(LEITURA))
        assert e.value.status_code == 403 and http.escritas == []
        r = await cg_main.speech_profile_delete("sip", _req(ESCRITA))
        assert r.status_code == 204 and http.escritas[0]["metodo"] == "DELETE"
        assert http.catalogos == []      # apagar não cria config que a chamada não sustente


class TestRecusaAntesDeGravar:
    async def test_modelo_que_o_servico_nao_tem(self, ambiente):
        http = ambiente.montar()
        with pytest.raises(HTTPException) as e:
            await cg_main.speech_profile_put("sip", _req(ESCRITA),
                                             {"stt_model": "Systran/faster-whisper-medium"})
        assert e.value.status_code == 422
        assert "faster-whisper-medium" in str(e.value.detail["reasons"])
        assert http.escritas == []       # o ponto do caso: recusou ANTES de gravar

    async def test_campo_desconhecido_nem_consulta_o_servico(self, ambiente):
        http = ambiente.montar()
        with pytest.raises(HTTPException) as e:
            await cg_main.speech_profile_put("sip", _req(ESCRITA), {"stt_modelo": "x"})
        assert e.value.status_code == 422 and http.catalogos == [] and http.escritas == []

    async def test_faixa_fora(self, ambiente):
        http = ambiente.montar()
        with pytest.raises(HTTPException) as e:
            await cg_main.speech_profile_put("sip", _req(ESCRITA), {"stt_end_silence_ms": 99_000})
        assert e.value.status_code == 422 and "stt_end_silence_ms" in str(e.value.detail["reasons"])
        assert http.escritas == []

    async def test_id_de_perfil_invalido(self, ambiente):
        http = ambiente.montar()
        with pytest.raises(HTTPException) as e:
            await cg_main.speech_profile_put("Perfil Com Espaco", _req(ESCRITA), dict(PERFIL_BOM))
        assert e.value.status_code == 422 and http.escritas == []

    async def test_servico_fora_recusa_e_nao_grava(self, ambiente):
        http = ambiente.montar(_Http(erro_catalogo=ConnectionError("recusou")))
        with pytest.raises(HTTPException) as e:
            await cg_main.speech_profile_put("sip", _req(ESCRITA), dict(PERFIL_BOM))
        assert e.value.status_code == 503 and "NAO gravada" in e.value.detail
        assert http.escritas == []


class TestEfetivo:
    async def test_voz_do_perfil_contra_modelo_do_TENANT(self, ambiente):
        """O perfil só troca a voz; o modelo em vigor vem da camada do tenant. Conferir o corpo
        isolado deixaria passar o par que quebra na chamada."""
        http = ambiente.montar(tenant_voice={"tts_model": PIPER["id"]})
        with pytest.raises(HTTPException) as e:
            await cg_main.speech_profile_put("sip", _req(ESCRITA), {"tts_voice": "pf_dora"})
        assert e.value.status_code == 422
        assert PIPER["id"] in str(e.value.detail["reasons"]) and http.escritas == []

    async def test_o_mesmo_par_passa_quando_o_perfil_traz_o_modelo(self, ambiente):
        http = ambiente.montar(tenant_voice={"tts_model": PIPER["id"]})
        r = await cg_main.speech_profile_put("sip", _req(ESCRITA),
                                            {"tts_model": KOKORO["id"], "tts_voice": "pf_dora"})
        assert r.status_code == 200 and len(http.escritas) == 1


class TestCredencial:
    async def test_o_config_api_recebe_o_bearer_de_quem_pediu(self, ambiente):
        http = ambiente.montar()
        pedido = _req(ESCRITA)
        await cg_main.speech_profile_put("sip", pedido, dict(PERFIL_BOM))
        assert http.escritas[0]["headers"]["authorization"] == pedido.headers["authorization"]
        assert http.escritas[0]["headers"]["x-tenant-id"] == "tenant_a"

    async def test_recusa_do_config_api_chega_ao_chamador(self, ambiente):
        http = ambiente.montar(_Http(config_api=_Resposta(403, {"detail": "sem config.channels"})))
        with pytest.raises(HTTPException) as e:
            await cg_main.speech_profile_put("sip", _req(ESCRITA), dict(PERFIL_BOM))
        assert e.value.status_code == 403 and e.value.detail["code"] == "config_api_refused"


class TestDefaultDoTenant:
    async def test_campo_ausente_nao_e_tocado(self, ambiente):
        http = ambiente.montar(tenant_voice={"tts_voice": "pf_dora"})
        r = await cg_main.speech_defaults_put(_req(ESCRITA), SpeechDefaultsBody(stt_language="pt"))
        assert r.status_code == 200
        assert [e["url"].rsplit("/", 1)[-1] for e in http.escritas] == ["stt_language"]

    async def test_vazio_REMOVE_o_override_e_volta_ao_env(self, ambiente):
        http = ambiente.montar(tenant_voice={"tts_voice": "pf_dora", "tts_model": KOKORO["id"]})
        r = await cg_main.speech_defaults_put(_req(ESCRITA), SpeechDefaultsBody(tts_voice=""))
        assert http.escritas[0]["metodo"] == "DELETE"
        assert http.escritas[0]["url"].endswith("/config/webrtc/tts_voice")
        corpo = r.body.decode()
        assert '"removed":["tts_voice"]' in corpo.replace(" ", "")
        assert ENV["tts_voice"] in corpo          # o efetivo volta ao env, e a resposta diz qual

    async def test_default_que_o_servico_nao_serve_e_recusado(self, ambiente):
        http = ambiente.montar()
        with pytest.raises(HTTPException) as e:
            await cg_main.speech_defaults_put(_req(ESCRITA), SpeechDefaultsBody(tts_voice="faber"))
        assert e.value.status_code == 422 and http.escritas == []

    async def test_corpo_sem_campo_nenhum_e_recusado(self, ambiente):
        http = ambiente.montar()
        with pytest.raises(HTTPException) as e:
            await cg_main.speech_defaults_put(_req(ESCRITA), SpeechDefaultsBody())
        assert e.value.status_code == 422 and http.escritas == []

    async def test_sem_credencial_401_e_nada_escrito(self, ambiente):
        http = ambiente.montar()
        with pytest.raises(HTTPException) as e:
            await cg_main.speech_defaults_put(_req(), SpeechDefaultsBody(tts_voice="pf_dora"))
        assert e.value.status_code == 401 and http.escritas == [] and http.catalogos == []
