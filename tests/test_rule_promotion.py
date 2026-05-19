"""Description:
    Verify the durable-rule promotion helper recognises explicit permanent instruction language.

Requirements:
    - Prove clearly declarative durable-rule phrasing is promoted.
    - Prove one-off or purely descriptive language is not promoted.
"""

from __future__ import annotations

from faith_pa.pa.rule_promotion import assess_rule_promotion


def test_promotes_explicit_new_rule_phrasing() -> None:
    """Description:
    Verify an explicit new-rule declaration is detected as durable guidance.

    Requirements:
        - This test is needed to prove the helper recognises direct permanent-instruction language.
        - Verify the extracted rule text preserves the actionable instruction.
    """

    result = assess_rule_promotion("I have a new rule for you: always answer in bullet points.")

    assert result.should_promote is True
    assert "new_rule" in result.matched_signals
    assert result.candidate_rule_text == "always answer in bullet points."


def test_promotes_from_now_on_instruction_language() -> None:
    """Description:
    Verify future-scope instruction phrasing is promoted deterministically.

    Requirements:
        - This test is needed to prove durable future-facing language is accepted.
        - Verify the helper records the cue that justified promotion.
    """

    result = assess_rule_promotion("From now on, add this as an instruction: keep replies concise.")

    assert result.should_promote is True
    assert "from_now_on" in result.matched_signals
    assert "add_as_instruction" in result.matched_signals
    assert result.candidate_rule_text == "keep replies concise."


def test_rejects_one_off_request_language() -> None:
    """Description:
    Verify a temporary request is not mistaken for a durable project rule.

    Requirements:
        - This test is needed to prove one-off instructions stay out of AGENTS.md promotion.
        - Verify the helper explains that the request is scoped to a single task.
    """

    result = assess_rule_promotion("For this task, please use bullet points.")

    assert result.should_promote is False
    assert "one_off_request" in result.blocked_signals
    assert result.candidate_rule_text == ""


def test_rejects_mixed_one_off_and_instruction_language() -> None:
    """Description:
    Verify one-off scope markers still block promotion when durable wording is mixed in.

    Requirements:
        - This test is needed to prove the helper stays conservative around ambiguous requests.
        - Verify the result does not auto-promote a request scoped to one task.
    """

    result = assess_rule_promotion(
        "For this task, please add this as an instruction: keep replies concise."
    )

    assert result.should_promote is False
    assert "one_off_request" in result.blocked_signals
    assert result.candidate_rule_text == ""


def test_rejects_descriptive_always_statement() -> None:
    """Description:
    Verify descriptive statements that happen to use the word always are not promoted.

    Requirements:
        - This test is needed to prove the helper avoids matching non-instructional wording.
        - Verify the result remains a negative classification when the sentence is observational.
    """

    result = assess_rule_promotion("I always use bullet points in my notes.")

    assert result.should_promote is False
    assert "durable_cue" not in result.matched_signals
    assert result.candidate_rule_text == ""
