"""Description:
    Verify Project Agent active-context compaction threshold and retention logic.

Requirements:
    - Prove hard and soft compaction thresholds are classified deterministically.
    - Prove deterministic retention keeps blocker and approval messages while trimming old resolved history.
"""

from __future__ import annotations

from faith_pa.pa.context_compaction import (
    ContextCompactionController,
    ContextCompactionMode,
)


def test_context_compaction_controller_classifies_soft_and_hard_thresholds() -> None:
    """Description:
        Verify the compaction controller classifies context usage into none, soft, or hard modes.

    Requirements:
        - This test is needed to prove the PA can distinguish between background compaction and hard pre-turn compaction.
        - Verify usage below the soft threshold does nothing, usage above the soft threshold triggers soft compaction, and usage at or above 95 percent triggers hard compaction.
    """

    controller = ContextCompactionController(
        model_name="ollama/llama3:8b",
        soft_threshold_pct=80,
        hard_threshold_pct=95,
    )

    assert (
        controller.classify_usage(usage_percentage=79, context_window_limit=1000).mode
        is ContextCompactionMode.NONE
    )
    assert (
        controller.classify_usage(usage_percentage=80, context_window_limit=1000).mode
        is ContextCompactionMode.SOFT
    )
    assert (
        controller.classify_usage(usage_percentage=95, context_window_limit=1000).mode
        is ContextCompactionMode.HARD
    )
    assert (
        controller.classify_usage(usage_percentage=None, context_window_limit=None).mode
        is ContextCompactionMode.NONE
    )


def test_context_compaction_controller_retains_recent_and_still_relevant_messages() -> None:
    """Description:
        Verify deterministic retention keeps still-relevant history messages during compaction.

    Requirements:
        - This test is needed to prove unresolved blockers, approvals, and the newest turns survive compaction.
        - Verify old resolved chatter is selected for summarisation while the retained set keeps blocker-like and approval-like history plus the newest recent messages.
    """

    controller = ContextCompactionController(
        model_name="ollama/llama3:8b",
        retain_recent_messages=2,
    )
    history = [
        {"role": "user", "content": "Earlier greeting."},
        {"role": "assistant", "content": "Resolved setup note."},
        {"role": "user", "content": "Pending approval to delete the generated files."},
        {"role": "assistant", "content": "There is still an unresolved blocker with the API key."},
        {"role": "user", "content": "Newest question."},
        {"role": "assistant", "content": "Newest answer."},
    ]

    selection = controller.select_history_for_compaction(history)

    retained_text = "\n".join(message["content"] for message in selection.retained_messages)
    compacted_text = "\n".join(message["content"] for message in selection.compacted_messages)

    assert "Pending approval to delete the generated files." in retained_text
    assert "There is still an unresolved blocker with the API key." in retained_text
    assert "Newest question." in retained_text
    assert "Newest answer." in retained_text
    assert "Earlier greeting." in compacted_text
    assert "Resolved setup note." in compacted_text
