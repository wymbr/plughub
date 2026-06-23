"""
test_prompt_composer_r8a.py — R8a (controles de viés na rubrica).

Garante que os controles de viés (verbosity, self-enhancement, surface-fluency,
authority) são SEMPRE anexados ao body efetivo (mesmo sob rubrica do tenant) e que
o anexo é idempotente.
"""
from plughub_evaluation_api.prompt_composer import (
    with_bias_controls,
    compose_rubric_prompt,
    BIAS_CONTROLS,
    _BIAS_HEADER,
    DEFAULT_RUBRIC_BODY,
)


def test_with_bias_controls_appends_to_tenant_body():
    out = with_bias_controls("Regra própria do tenant.")
    assert "Regra própria do tenant." in out          # body do tenant preservado
    assert _BIAS_HEADER in out                          # bias controls anexados
    assert "Verbosity" in out and "Self-enhancement" in out
    assert "Authority" in out


def test_with_bias_controls_is_idempotent():
    once = with_bias_controls("x")
    twice = with_bias_controls(once)
    assert once == twice
    assert once.count(_BIAS_HEADER) == 1                # não duplica


def test_with_bias_controls_empty_uses_default_body():
    out = with_bias_controls("")
    assert DEFAULT_RUBRIC_BODY[:24] in out              # cai no built-in
    assert _BIAS_HEADER in out


def test_compose_rubric_prompt_includes_bias_section():
    res = compose_rubric_prompt(rubric_body="R", rubric_source="tenant", form=None)
    assert "bias_controls" in res["sections"]
    assert res["sections"]["bias_controls"] == BIAS_CONTROLS
    assert _BIAS_HEADER in res["composed_prompt"]
    assert "surface-fluency" in res["composed_prompt"].lower()


if __name__ == "__main__":
    for fn in [
        test_with_bias_controls_appends_to_tenant_body,
        test_with_bias_controls_is_idempotent,
        test_with_bias_controls_empty_uses_default_body,
        test_compose_rubric_prompt_includes_bias_section,
    ]:
        fn()
        print(f"  PASS {fn.__name__}")
    print("ALL PASS")
