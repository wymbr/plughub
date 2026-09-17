"""
test_speech_catalog.py — VOZ-17: o que o serviço de fala TEM, e o que isso recusa.

A proposição: *uma config de fala que o serviço não serve é recusada ANTES de virar chamada* — e,
tão importante quanto, *uma que ele serve passa* (sem o controle positivo, "recusa tudo" pareceria
conferência).

Os casos que o defeito original produzia, todos plausíveis:
  * modelo que não está instalado (o nome casa o padrão, o serviço dá 404 por frase);
  * modelo instalado na tarefa ERRADA — trocar `stt_model` e `tts_model` de lugar é o erro que
    nenhum padrão de nome pega;
  * voz válida em OUTRO modelo: o par (modelo de uma camada, voz de outra) é o que o `efetivo`
    existe para conferir, e é invisível para quem só olha o corpo enviado;
  * língua que o modelo não declara — com `pt-BR` contra `pt` passando, porque a comparação é pela
    subtag, e `multilingual` passando sempre.

E o contrato de `catalogo()`: falha NUNCA vira lista vazia. Lista vazia significaria "nenhum modelo
existe" e recusaria tudo pela razão errada — o oposto de dizer que não deu para conferir.
"""
from __future__ import annotations

import pytest

from plughub_channel_gateway import speech_catalog as sc

KOKORO = {"id": "speaches-ai/Kokoro-82M-v1.0-ONNX", "task": sc.TTS, "language": ["multilingual"],
          "voices": [{"name": "pf_dora", "language": "pt-br"}, {"name": "af_heart", "language": "en-us"}]}
PIPER = {"id": "speaches-ai/piper-pt_BR-faber-medium", "task": sc.TTS, "language": ["pt"],
         "voices": [{"name": "faber", "language": "pt"}]}
WHISPER = {"id": "deepdml/faster-whisper-large-v3-turbo-ct2", "task": sc.STT,
           "language": ["en", "pt", "es"]}
SEM_VOZES = {"id": "svc/tts-sem-catalogo-de-voz", "task": sc.TTS, "language": ["pt"]}
MODELOS = [KOKORO, PIPER, WHISPER]

BOM = {"stt_model": WHISPER["id"], "stt_language": "pt-BR",
       "tts_model": KOKORO["id"], "tts_voice": "pf_dora"}


class TestPassaQuemDeve:
    def test_o_efetivo_do_demo_nao_tem_recusa(self):
        """CONTROLE POSITIVO: é a config que está no ar hoje."""
        assert sc.conferir(MODELOS, BOM) == []

    def test_campo_vazio_nao_e_recusa(self):
        """Vazio = a camada de baixo responde; recusar aqui recusaria quem só troca a voz."""
        assert sc.conferir(MODELOS, {**BOM, "stt_language": "", "tts_voice": ""}) == []

    def test_modelo_que_nao_declara_vozes_nao_reprova_a_voz(self):
        """Não dá para conferir o que o serviço não declara — e inventar recusa é tão ruim quanto
        inventar permissão."""
        assert sc.conferir([WHISPER, SEM_VOZES],
                           {**BOM, "tts_model": SEM_VOZES["id"], "tts_voice": "qualquer"}) == []

    def test_multilingual_aceita_qualquer_lingua(self):
        assert sc.conferir([{**WHISPER, "language": ["multilingual"]}, KOKORO],
                           {**BOM, "stt_language": "ja"}) == []


class TestRecusa:
    def test_modelo_nao_instalado_nomeia_os_instalados(self):
        r = sc.conferir(MODELOS, {**BOM, "stt_model": "Systran/faster-whisper-medium"})
        assert len(r) == 1 and "faster-whisper-medium" in r[0] and WHISPER["id"] in r[0]

    def test_tarefa_trocada(self):
        """O modelo EXISTE — é o campo que está errado. Padrão de nome nunca pegaria."""
        r = sc.conferir(MODELOS, {**BOM, "stt_model": KOKORO["id"]})
        assert len(r) == 1 and sc.TTS in r[0] and sc.STT in r[0]

    def test_voz_de_outro_modelo(self):
        """`faber` é voz de verdade — do piper. Com o Kokoro em vigor, a chamada falha."""
        r = sc.conferir(MODELOS, {**BOM, "tts_voice": "faber"})
        assert len(r) == 1 and "faber" in r[0] and KOKORO["id"] in r[0]

    def test_voz_da_camada_de_cima_contra_modelo_da_de_baixo(self):
        """O caso que justifica conferir o EFETIVO: o perfil só mudou a voz."""
        efetivo = {**BOM, "tts_model": PIPER["id"], "tts_voice": "pf_dora"}
        r = sc.conferir(MODELOS, efetivo)
        assert len(r) == 1 and "pf_dora" in r[0] and PIPER["id"] in r[0]

    def test_lingua_que_o_modelo_nao_declara(self):
        r = sc.conferir(MODELOS, {**BOM, "stt_language": "ja"})
        assert len(r) == 1 and "stt_language" in r[0] and "ja" in r[0]

    def test_recusas_somam_sem_esconder_uma_na_outra(self):
        r = sc.conferir(MODELOS, {"stt_model": "nao/existe", "stt_language": "pt",
                                  "tts_model": "tambem/nao", "tts_voice": "x"})
        assert len(r) == 2      # os dois modelos; voz e língua não têm modelo contra o que conferir


class _RespostaFalsa:
    def __init__(self, status_code=200, payload=None, text="", quebra_json=False):
        self.status_code, self._payload, self.text = status_code, payload, text
        self._quebra = quebra_json

    def json(self):
        if self._quebra:
            raise ValueError("nao e JSON")
        return self._payload


class _ClienteFalso:
    def __init__(self, resposta=None, erro=None):
        self.resposta, self.erro, self.urls = resposta, erro, []

    async def get(self, url, timeout=None):
        self.urls.append(url)
        if self.erro is not None:
            raise self.erro
        return self.resposta


class TestCatalogo:
    async def test_le_os_instalados_e_ignora_entrada_sem_id(self):
        cli = _ClienteFalso(_RespostaFalsa(200, {"data": MODELOS + [{"task": sc.STT}]}))
        m = await sc.catalogo("http://speaches:8000/", client=cli)
        assert [x["id"] for x in m] == [x["id"] for x in MODELOS]
        assert cli.urls == ["http://speaches:8000/v1/models"]      # nunca /v1/registry

    @pytest.mark.parametrize("resposta,erro,pedaco", [
        (None, ConnectionError("recusou"), "inalcancavel"),
        (_RespostaFalsa(503, text="indo"), None, "HTTP 503"),
        (_RespostaFalsa(200, quebra_json=True), None, "nao-JSON"),
        (_RespostaFalsa(200, {"models": []}), None, "sem lista `data`"),
    ])
    async def test_falha_levanta_e_nunca_devolve_vazio(self, resposta, erro, pedaco):
        with pytest.raises(sc.CatalogoIndisponivel) as e:
            await sc.catalogo("http://speaches:8000", client=_ClienteFalso(resposta, erro))
        assert pedaco in str(e.value)

    async def test_sem_url_nomeia_a_env(self):
        with pytest.raises(sc.CatalogoIndisponivel) as e:
            await sc.catalogo("", client=_ClienteFalso(_RespostaFalsa(200, {"data": MODELOS})))
        assert "PLUGHUB_WEBRTC_SPEACHES_URL" in str(e.value)
