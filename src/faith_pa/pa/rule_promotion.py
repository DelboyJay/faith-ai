"""Description:
    Detect durable user rule language and extract a deterministic promotion result.

Requirements:
    - Recognise clearly declarative durable-instruction phrasing without fuzzy scoring.
    - Reject temporary, one-off, or observational language that should not be appended to ``AGENTS.md``.
    - Return a structured result that downstream PA code can audit and use directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WHITESPACE_RE = re.compile(r"\s+")
_LEADING_JUNK_RE = re.compile(r"^[\s,:;—–-]+")

_BLOCKING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "one_off_request",
        re.compile(
            r"\b(?:for this (?:task|request|turn|one|time)|"
            r"just this once|only this time|for now|temporary|"
            r"single[- ]use|this one time)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "observational_statement",
        re.compile(r"\bI always\b|\bI usually\b|\bI often\b", re.IGNORECASE),
    ),
)

_EXPLICIT_PROMOTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("new_rule", re.compile(r"(?:^|\b)(?:i have a )?new rule(?: for you)?\b", re.IGNORECASE)),
    ("from_now_on", re.compile(r"\bfrom now on\b", re.IGNORECASE)),
    ("going_forward", re.compile(r"\bgoing forward\b", re.IGNORECASE)),
    (
        "add_as_instruction",
        re.compile(r"\b(?:please\s+)?add this as an instruction\b", re.IGNORECASE),
    ),
    (
        "treat_as_instruction",
        re.compile(r"\b(?:please\s+)?treat this as an instruction\b", re.IGNORECASE),
    ),
    ("standing_instruction", re.compile(r"\bstanding instruction\b", re.IGNORECASE)),
    (
        "permanent_instruction",
        re.compile(r"\b(?:permanent(?:ly)?|for all future|in future)\b", re.IGNORECASE),
    ),
)

_IMPERATIVE_PROMOTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "always_directive",
        re.compile(
            r"^(?:please\s+)?always\s+"
            r"(?:be|keep|stay|remain|use|do|write|answer|respond|format|"
            r"avoid|include|mention|call|say|follow|make|put|start|end|"
            r"prefer|treat)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "every_time_directive",
        re.compile(
            r"^(?:please\s+)?every time\s+"
            r"(?:you\s+)?(?:use|answer|respond|format|write|keep|avoid|include|"
            r"mention|call|say|follow|make|put|start|end|prefer|be|stay|remain|do)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "whenever_directive",
        re.compile(
            r"^(?:please\s+)?whenever\s+"
            r"(?:you\s+)?(?:use|answer|respond|format|write|keep|avoid|include|"
            r"mention|call|say|follow|make|put|start|end|prefer|be|stay|remain|do)\b",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(slots=True)
class RulePromotionAssessment:
    """Description:
        Record the deterministic outcome of durable-rule promotion detection.

    Requirements:
        - Preserve the final yes/no decision.
        - Keep the matched and blocked signals visible for auditability.
        - Provide the extracted candidate rule text only when promotion is supported.

    :param should_promote: ``True`` when the input should be appended to ``AGENTS.md``.
    :param matched_signals: Rule signals that justified promotion.
    :param blocked_signals: Rule signals that argue against promotion.
    :param candidate_rule_text: Extracted instruction text suitable for persistence.
    :param reason: Human-readable deterministic explanation of the outcome.
    """

    should_promote: bool
    matched_signals: list[str] = field(default_factory=list)
    blocked_signals: list[str] = field(default_factory=list)
    candidate_rule_text: str = ""
    reason: str = ""


def _normalize_text(text: str) -> str:
    """Description:
        Collapse whitespace so the rule checks operate on stable text.

    Requirements:
        - Preserve the original wording while removing layout noise.

    :param text: Raw user inference text.
    :returns: Normalised text with repeated whitespace collapsed.
    """

    return _WHITESPACE_RE.sub(" ", text).strip()


def _collect_matches(
    text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]
) -> list[tuple[int, int, str]]:
    """Description:
        Collect the spans for every matching rule signal in deterministic order.

    Requirements:
        - Keep the discovered signal name and match bounds for later extraction.

    :param text: Normalised text to inspect.
    :param patterns: Named regular expressions to evaluate.
    :returns: Sorted list of ``(start, end, signal_name)`` matches.
    """

    matches: list[tuple[int, int, str]] = []
    for signal_name, pattern in patterns:
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), signal_name))
    return sorted(matches, key=lambda item: (item[0], item[1], item[2]))


def _strip_instruction_prefix(text: str) -> str:
    """Description:
        Remove punctuation and boilerplate prefixes from extracted rule text.

    Requirements:
        - Leave the substantive instruction intact.
        - Avoid guessing beyond explicit durable-rule phrasing.

    :param text: Candidate rule text extracted from the original input.
    :returns: Cleaned candidate rule text.
    """

    current = text
    changed = True
    while changed:
        changed = False
        current = _LEADING_JUNK_RE.sub("", current)
        for prefix in (
            "that ",
            "to ",
            "please ",
            "the rule is ",
            "this rule is ",
            "my rule is ",
            "instruction ",
            "instruction: ",
            "rule: ",
        ):
            if current.lower().startswith(prefix):
                current = current[len(prefix) :]
                changed = True
                break
    return current.strip()


def _extract_candidate_rule_text(text: str, matches: list[tuple[int, int, str]]) -> str:
    """Description:
        Derive a persistence-ready instruction fragment from the detected cue.

    Requirements:
        - Prefer the trailing clause after the latest matching durable cue.
        - Keep the extraction deterministic and rule-based.

    :param text: Normalised input text.
    :param matches: Ordered list of matching signal spans.
    :returns: Candidate rule text, or an empty string when no promotion is justified.
    """

    if not matches:
        return ""
    _, end, _signal_name = matches[-1]
    return _strip_instruction_prefix(text[end:])


def assess_rule_promotion(user_text: str) -> RulePromotionAssessment:
    """Description:
        Evaluate whether one user utterance contains a durable rule suitable for ``AGENTS.md``.

    Requirements:
        - Use explicit rule cues and explicit blockers rather than fuzzy heuristics.
        - Return a structured result that downstream PA code can use without re-parsing.
        - Reject observational or one-off requests even when they contain nearby instruction words.

    :param user_text: Raw user inference text to classify.
    :returns: Deterministic durable-rule promotion assessment.
    """

    normalized_text = _normalize_text(user_text)
    blocking_matches = _collect_matches(normalized_text, _BLOCKING_PATTERNS)
    explicit_matches = _collect_matches(normalized_text, _EXPLICIT_PROMOTION_PATTERNS)
    imperative_matches = _collect_matches(normalized_text, _IMPERATIVE_PROMOTION_PATTERNS)

    matched_signals = [signal_name for _start, _end, signal_name in explicit_matches]
    matched_signals.extend(signal_name for _start, _end, signal_name in imperative_matches)
    blocked_signals = [signal_name for _start, _end, signal_name in blocking_matches]

    if blocking_matches:
        return RulePromotionAssessment(
            should_promote=False,
            matched_signals=matched_signals,
            blocked_signals=blocked_signals,
            reason="The text contains one-off or temporary scope markers, so it is not safe to promote.",
        )

    if not explicit_matches and not imperative_matches:
        return RulePromotionAssessment(
            should_promote=False,
            blocked_signals=blocked_signals,
            reason="No durable-rule cue was present in the text.",
        )

    candidate_rule_text = _extract_candidate_rule_text(
        normalized_text,
        explicit_matches if explicit_matches else imperative_matches,
    )
    return RulePromotionAssessment(
        should_promote=True,
        matched_signals=matched_signals,
        blocked_signals=blocked_signals,
        candidate_rule_text=candidate_rule_text,
        reason="The text contains explicit durable-rule language and no blocking one-off scope.",
    )
