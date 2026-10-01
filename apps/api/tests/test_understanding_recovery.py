"""CI-7 strict clarification contract and finite pass state."""
import pytest

from app.conversation.recovery import (
    MAX_UNDERSTANDING_PASSES, ContextSource, PassKind, UnderstandingPassState,
    recovery_context,
)
from app.conversation.understanding import (
    CLARIFICATION_CANDIDATE_CHARS, CLARIFICATION_CANDIDATE_MAX,
    CLARIFICATION_PROMPT_CHARS, UNDERSTANDING_JSON_SCHEMA, Clarification,
    UnderstandingProviderError, parse_understanding_payload,
)


def test_clarification_contract_boundaries_and_schema_agree():
    candidates = ["x" * CLARIFICATION_CANDIDATE_CHARS] * CLARIFICATION_CANDIDATE_MAX
    result = parse_understanding_payload({
        "kind": "clarification", "prompt": "p" * CLARIFICATION_PROMPT_CHARS,
        "candidates": candidates,
    })
    assert result.candidates == tuple(candidates)
    assert result.validated().candidates == candidates
    properties = UNDERSTANDING_JSON_SCHEMA["properties"]
    assert properties["candidates"]["maxItems"] == CLARIFICATION_CANDIDATE_MAX
    assert properties["candidates"]["items"]["maxLength"] == CLARIFICATION_CANDIDATE_CHARS
    assert properties["candidates"]["items"]["minLength"] == 1
    assert properties["prompt"]["maxLength"] == CLARIFICATION_PROMPT_CHARS
    assert set(UNDERSTANDING_JSON_SCHEMA["required"]) == set(properties)


@pytest.mark.parametrize("fields", [
    {"candidates": ["a"] * (CLARIFICATION_CANDIDATE_MAX + 1)},
    {"candidates": ["a" * (CLARIFICATION_CANDIDATE_CHARS + 1)]},
    {"candidates": [42]}, {"candidates": [None]}, {"candidates": [""]},
    {"candidates": "one"}, {"candidates": {"one": "two"}},
    {"prompt": "p" * (CLARIFICATION_PROMPT_CHARS + 1)}, {"prompt": 42},
    {"action": "project.create"}, {"arguments": {}}, {"reference": "Private"},
    {"recall_window": "recent"}, {"steps": []}, {"summary": "Plan"},
    {"retrieval": {"query": "*", "scopes": ["notes"]}},
    {"reply": "Answer"}, {"reason": "Unsupported"}, {"extra": "unknown"},
])
def test_malformed_clarification_is_provider_error(fields):
    with pytest.raises(UnderstandingProviderError):
        parse_understanding_payload({"kind": "clarification", **fields})


@pytest.mark.parametrize("candidates", [None, []])
def test_optional_clarification_and_nullable_current_output_remain_valid(candidates):
    result = parse_understanding_payload({
        "kind": "clarification", "prompt": None, "candidates": candidates,
        "action": None, "reference": None, "arguments": None,
        "recall_window": None, "steps": None, "summary": None,
        "reply": None, "reason": None, "retrieval": None,
    })
    assert result == Clarification(kind="clarification")


def test_non_json_candidate_container_cannot_be_coerced():
    with pytest.raises(UnderstandingProviderError):
        parse_understanding_payload({"kind": "clarification", "candidates": ("one",)})
    with pytest.raises(UnderstandingProviderError):
        Clarification(kind="clarification", candidates=("x" * 201,)).validated()


def test_state_allows_only_one_retrieval_and_one_recovery():
    state = UnderstandingPassState(ContextSource.NONE)
    assert state.can_retrieve and not state.can_recover
    retrieved = state.after_retrieval()
    assert retrieved.kind is PassKind.RETRIEVED
    assert not retrieved.can_retrieve and retrieved.can_recover
    recovered = retrieved.after_recovery()
    assert recovered.context_source is ContextSource.RETRIEVED
    assert not recovered.can_retrieve and not recovered.can_recover
    assert MAX_UNDERSTANDING_PASSES == 3
    trusted = UnderstandingPassState(ContextSource.TRUSTED)
    assert not trusted.can_retrieve and trusted.can_recover
    assert not trusted.after_recovery().can_recover


def test_recovery_requires_additional_continuity_not_identical_input():
    clarification = Clarification(kind="clarification", prompt="Which topic?")
    original = {"recent_turns": [{"role": "user", "content": "Alpha"}]}
    assert recovery_context(original, original, clarification) is None
    assert recovery_context(None, None, clarification) is None
    assert recovery_context(None, {"last_grounded_entity": {"title": "private"}}, clarification) is None
    context = recovery_context({"last_grounded_project": {"name": "Alpha"}}, original, clarification)
    assert context["recent_turns"] == original["recent_turns"]
    assert context["understanding_recovery"] == {"prompt": "Which topic?", "candidates": []}


def test_state_refuses_repeated_or_unauthorized_transitions():
    state = UnderstandingPassState(ContextSource.NONE)
    with pytest.raises(ValueError):
        state.after_recovery()
    recovery = state.after_retrieval().after_recovery()
    with pytest.raises(ValueError):
        recovery.after_retrieval()
    with pytest.raises(ValueError):
        recovery.after_recovery()


def test_recovery_rebounds_a_single_huge_stored_turn():
    from app.conversation.context import RECENT_CHARACTER_BUDGET, RECENT_TURN_LIMIT

    turns = [{"role": "user", "content": "x" * 10000, "language": "en"}] * 20
    context = recovery_context(None, {"recent_turns": turns}, Clarification(kind="clarification"))
    assert len(context["recent_turns"]) <= RECENT_TURN_LIMIT
    assert sum(len(t["content"]) for t in context["recent_turns"]) == RECENT_CHARACTER_BUDGET
    assert len(turns[0]["content"]) == 10000


def test_recovery_prior_result_is_bounded_without_internal_metadata():
    from app.conversation.recovery import RECOVERY_PRIOR_TEXT_CHARS

    context = recovery_context(None, {"previous_general_turn": {
        "message": "m" * 10000, "reply": "r" * 10000, "language": "en",
        "owner_id": "internal",
    }}, Clarification(kind="clarification"))
    prior = context["previous_general_turn"]
    assert len(prior["message"]) == RECOVERY_PRIOR_TEXT_CHARS
    assert len(prior["reply"]) == RECOVERY_PRIOR_TEXT_CHARS
    assert "owner_id" not in prior
