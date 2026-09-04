# -*- coding: utf-8 -*-
"""DIALOG_FORMAT_CATALOG — o espelho Python do catálogo de formatos de entrada.

⚠️ **GERADO, nunca digitado à mão.** Autoridade:
`packages/schemas/src/dialog-format.ts`. Regenerar com
`infra/scripts/gen_dialog_formats_py.ts`; a divergência é reprovada por
`infra/test/probe_dialog_formats_parity.sh`.

Ele mora aqui — e não no `seed.py` do config-api — pelo mesmo motivo do
`default_map.py`: são DOIS consumidores Python (o seed do config-api e o
interpretador da página de survey no channel-gateway), e uma cópia por
consumidor divergiria justamente na hora em que as duas superfícies
precisassem concordar sobre o mesmo veredicto.

⚠️ **Fonte de verdade em runtime é o STORE** (config-api, namespace `dialog`,
chave `formats`). O seed é seed-if-absent: editar aqui depois de a base estar
semeada é NO-OP. Este valor serve para (a) semear base vazia e (b) ser o
fallback de quem não conseguiu falar com o config-api.

Ver `docs/adr/adr-dialog-input-format-catalog.md`.
"""
from __future__ import annotations

from typing import Any

DIALOG_FORMAT_CATALOG: dict[str, Any] = {
    "formats": [
        {
            "id": "text",
            "label": "Texto livre",
            "affordance": {
                "inputmode": "text",
            },
            "verdict": {
                "semantic": "none",
            },
            "vectors": {
                "valid": [
                    "qualquer coisa",
                    "123",
                ],
                "invalid": [],
            },
        },
        {
            "id": "digits",
            "label": "Somente dígitos",
            "affordance": {
                "inputmode": "numeric",
            },
            "verdict": {
                "shape": "^[0-9]+$",
                "semantic": "none",
                "error": {
                    "pt-BR": "Use somente números.",
                    "en": "Digits only.",
                },
            },
            "vectors": {
                "valid": [
                    "0",
                    "123456",
                ],
                "invalid": [
                    "12a",
                    "1 2",
                    "",
                    "-1",
                ],
            },
        },
        {
            "id": "integer",
            "label": "Número inteiro",
            "affordance": {
                "inputmode": "numeric",
            },
            "verdict": {
                "shape": "^-?[0-9]+$",
                "semantic": "none",
                "error": {
                    "pt-BR": "Informe um número inteiro.",
                    "en": "Enter a whole number.",
                },
            },
            "vectors": {
                "valid": [
                    "0",
                    "-7",
                    "42",
                ],
                "invalid": [
                    "1.5",
                    "1,5",
                    "abc",
                    "",
                ],
            },
        },
        {
            "id": "decimal",
            "label": "Número decimal",
            "affordance": {
                "inputmode": "decimal",
            },
            "verdict": {
                "shape": "^-?[0-9]+([.,][0-9]+)?$",
                "semantic": "none",
                "error": {
                    "pt-BR": "Informe um número.",
                    "en": "Enter a number.",
                },
            },
            "vectors": {
                "valid": [
                    "0",
                    "-7",
                    "1.5",
                    "1,5",
                    "1234",
                ],
                "invalid": [
                    "1.2.3",
                    "R$ 10",
                    "",
                ],
            },
        },
        {
            "id": "alphanumeric",
            "label": "Letras e números",
            "affordance": {
                "inputmode": "text",
            },
            "verdict": {
                "shape": "^[A-Za-z0-9]+$",
                "semantic": "none",
                "error": {
                    "pt-BR": "Use apenas letras e números, sem espaços.",
                    "en": "Letters and digits only, no spaces.",
                },
            },
            "vectors": {
                "valid": [
                    "AB12",
                    "abc",
                    "999",
                ],
                "invalid": [
                    "AB 12",
                    "ab-12",
                    "ção",
                    "",
                ],
            },
        },
        {
            "id": "date_br",
            "label": "Data (dd/mm/aaaa)",
            "affordance": {
                "mask": "##/##/####",
                "inputmode": "numeric",
                "maxlength": 10,
                "placeholder": {
                    "pt-BR": "dd/mm/aaaa",
                    "en": "dd/mm/yyyy",
                },
            },
            "verdict": {
                "shape": "^[0-9]{2}/[0-9]{2}/[0-9]{4}$",
                "semantic": "calendar_date",
                "error": {
                    "pt-BR": "Informe uma data válida no formato dd/mm/aaaa.",
                    "en": "Enter a valid date as dd/mm/yyyy.",
                },
            },
            "vectors": {
                "valid": [
                    "01/01/2026",
                    "29/02/2024",
                    "31/12/1999",
                ],
                "invalid": [
                    "31/02/2026",
                    "29/02/2026",
                    "00/01/2026",
                    "01/13/2026",
                    "1/1/2026",
                    "",
                ],
            },
        },
        {
            "id": "time_hm",
            "label": "Hora (hh:mm)",
            "affordance": {
                "mask": "##:##",
                "inputmode": "numeric",
                "maxlength": 5,
                "placeholder": {
                    "pt-BR": "hh:mm",
                    "en": "hh:mm",
                },
            },
            "verdict": {
                "shape": "^[0-9]{2}:[0-9]{2}$",
                "semantic": "clock_time",
                "error": {
                    "pt-BR": "Informe um horário válido (hh:mm).",
                    "en": "Enter a valid time (hh:mm).",
                },
            },
            "vectors": {
                "valid": [
                    "00:00",
                    "23:59",
                ],
                "invalid": [
                    "24:00",
                    "12:60",
                    "9:30",
                    "",
                ],
            },
        },
        {
            "id": "time_hms",
            "label": "Hora com segundos (hh:mm:ss)",
            "affordance": {
                "mask": "##:##:##",
                "inputmode": "numeric",
                "maxlength": 8,
                "placeholder": {
                    "pt-BR": "hh:mm:ss",
                    "en": "hh:mm:ss",
                },
            },
            "verdict": {
                "shape": "^[0-9]{2}:[0-9]{2}:[0-9]{2}$",
                "semantic": "clock_time",
                "error": {
                    "pt-BR": "Informe um horário válido (hh:mm:ss).",
                    "en": "Enter a valid time (hh:mm:ss).",
                },
            },
            "vectors": {
                "valid": [
                    "00:00:00",
                    "23:59:59",
                ],
                "invalid": [
                    "23:59:60",
                    "24:00:00",
                    "",
                ],
            },
        },
        {
            "id": "cpf",
            "label": "CPF",
            "from_masked_type": "cpf",
            "affordance": {
                "inputmode": "numeric",
                "maxlength": 14,
            },
            "verdict": {
                "shape": "^[0-9]{3}\\.[0-9]{3}\\.[0-9]{3}-[0-9]{2}$",
                "semantic": "cpf_checkdigit",
                "error": {
                    "pt-BR": "CPF inválido.",
                    "en": "Invalid CPF.",
                },
            },
            "vectors": {
                "valid": [
                    "529.982.247-25",
                    "111.444.777-35",
                ],
                "invalid": [
                    "000.000.000-00",
                    "111.111.111-11",
                    "529.982.247-26",
                    "52998224725",
                    "",
                ],
            },
        },
        {
            "id": "credit_card",
            "label": "Cartão de crédito",
            "from_masked_type": "credit_card",
            "affordance": {
                "inputmode": "numeric",
                "maxlength": 19,
            },
            "verdict": {
                "shape": "^[0-9]{4} [0-9]{4} [0-9]{4} [0-9]{4}$",
                "semantic": "luhn",
                "error": {
                    "pt-BR": "Número de cartão inválido.",
                    "en": "Invalid card number.",
                },
            },
            "vectors": {
                "valid": [
                    "4539 1488 0343 6467",
                    "5500 0055 5555 5559",
                ],
                "invalid": [
                    "4539 1488 0343 6468",
                    "1234 5678 9012 3456",
                    "",
                ],
            },
        },
        {
            "id": "card_expiry",
            "label": "Vencimento do cartão",
            "from_masked_type": "card_expiry",
            "affordance": {
                "inputmode": "numeric",
                "maxlength": 5,
                "placeholder": {
                    "pt-BR": "mm/aa",
                    "en": "mm/yy",
                },
            },
            "verdict": {
                "shape": "^[0-9]{2}/[0-9]{2}$",
                "semantic": "month_year",
                "error": {
                    "pt-BR": "Informe o vencimento como mm/aa.",
                    "en": "Enter expiry as mm/yy.",
                },
            },
            "vectors": {
                "valid": [
                    "01/26",
                    "12/30",
                ],
                "invalid": [
                    "13/26",
                    "00/26",
                    "1/26",
                    "",
                ],
            },
        },
        {
            "id": "phone_br",
            "label": "Telefone (Brasil)",
            "from_masked_type": "phone",
            "affordance": {
                "inputmode": "tel",
                "maxlength": 15,
            },
            "verdict": {
                "shape": "^\\([0-9]{2}\\) [0-9]{4,5}-[0-9]{4}$",
                "semantic": "none",
                "error": {
                    "pt-BR": "Informe o telefone com DDD.",
                    "en": "Enter the phone with area code.",
                },
            },
            "vectors": {
                "valid": [
                    "(11) 98765-4321",
                    "(11) 3456-7890",
                ],
                "invalid": [
                    "11987654321",
                    "(11) 987-654",
                    "",
                ],
            },
        },
        {
            "id": "email",
            "label": "E-mail",
            "from_masked_type": "email_addr",
            "affordance": {
                "inputmode": "email",
            },
            "verdict": {
                "shape": "^[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Za-z]{2,}$",
                "semantic": "none",
                "error": {
                    "pt-BR": "Informe um e-mail válido.",
                    "en": "Enter a valid email.",
                },
            },
            "vectors": {
                "valid": [
                    "a@b.co",
                    "nome.sobrenome+tag@exemplo.com.br",
                ],
                "invalid": [
                    "a@b",
                    "sem-arroba.com",
                    "a b@c.co",
                    "",
                ],
            },
        },
    ],
}
