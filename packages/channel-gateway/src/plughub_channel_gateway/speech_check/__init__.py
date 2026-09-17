"""
speech_check — verificação ativa do caminho de áudio, a pedido (VOZ-23, camada B da recalibragem).

Responde UMA pergunta: *o caminho de fala desta instalação regrediu?* (modelo, versão do SFU, codec,
reamostragem, perfil). Uma chamada real, com voz sintetizada, pelo endpoint TEMPORÁRIO que aponta o pool
de calibração com o perfil a verificar; o resultado (só números) sai em `speech.metrics` e é comparado,
no relatório, à linha de base que UMA PESSOA marcou. Não propõe limite de cliente — voz sintetizada não
os decide (VOZ-18/19) — e não aplica nada.

Roda em processo próprio (`speech-check` no compose), com a imagem do gateway: fala com o gateway de
fora, como um cliente.
"""
