"""Hermetic tests for the optional TypeSafe structured-decision adapter."""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.conversation.dependencies import get_decision_policy, get_decision_provider
from app.intelligence.decision import (
    ConfirmationKind,
    RouteKind,
)
from app.conversation.plans.base import ProposedPlan, ProposedPlanStep
from app.conversation.plans.compiler import PlanCompiler
from app.intelligence.typesafe.client import (
    TypeSafeAuthenticationError,
    TypeSafeClient,
    TypeSafeInvalidResponse,
    TypeSafeModelUnavailable,
    TypeSafeRateLimitError,
    TypeSafeTimeout,
    TypeSafeUnavailable,
)
from app.intelligence.typesafe.models import ChoiceQuestion, SystemOneRequest
from app.intelligence.typesafe.provider import TypeSafeDecisionProvider

API_KEY = "test-typesafe-secret"
MODEL = "jev-latest"
SERVING_MODEL = "jev-1.13.0"


def _client(handler) -> tuple[TypeSafeClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        TypeSafeClient(
            api_key=API_KEY,
            base_url="https://api.typesafe.ai",
            timeout_seconds=0.25,
            client=http,
        ),
        http,
    )


def _choice_response(choice: str, confidence: float = 0.93) -> dict[str, object]:
    return {
        "model": SERVING_MODEL,
        "answers": {
            "decision": {
                "type": "choice",
                "choice": choice,
                "confidence": confidence,
                "probabilities": {choice: confidence},
            }
        },
        "usage": {"input_tokens": 20, "output_tokens": 4},
    }


def _noul_response(
    *,
    unrequested_action: float,
    omitted_action: float,
    excessive_mutation: float,
    faithful: float,
) -> dict[str, object]:
    probabilities = {
        "unrequested_action": unrequested_action,
        "omitted_action": omitted_action,
        "excessive_mutation": excessive_mutation,
        "faithful": faithful,
    }
    return {
        "model": SERVING_MODEL,
        "answers": {
            name: {"type": "noul", "noul": probability}
            for name, probability in probabilities.items()
        },
        "usage": {"input_tokens": 50, "output_tokens": 4},
    }


@pytest.mark.asyncio
async def test_models_endpoint_parses_and_uses_bearer_auth() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": MODEL,
                        "description": "Structured decisions",
                        "release_date": "2026-01-01",
                    }
                ]
            },
        )

    client, http = _client(handler)
    try:
        models = await client.list_models()
        await client.require_model(MODEL)
    finally:
        await http.aclose()

    assert models.models[0].name == MODEL
    assert seen[0].url.path == "/v1/models"
    assert seen[0].headers["authorization"] == f"Bearer {API_KEY}"


@pytest.mark.asyncio
async def test_system_one_valid_choice_and_confidence_parsing() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.url.path == "/v1/systemone"
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        assert json.loads(request.content)["model"] == "jev-latest"
        return httpx.Response(200, json=_choice_response("multi_step_plan", 0.91))

    client, http = _client(handler)
    try:
        provider = TypeSafeDecisionProvider(client, model=MODEL)
        decision = await provider.classify_route("create a project then add a task")
    finally:
        await http.aclose()

    assert decision.kind is RouteKind.MULTI_STEP_PLAN
    assert decision.confidence == 0.91
    assert paths == ["/v1/systemone"]


@pytest.mark.asyncio
async def test_models_401_does_not_block_system_one_inference() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(401, json={"detail": "unauthorized"})
        return httpx.Response(200, json=_choice_response("conversation"))

    client, http = _client(handler)
    try:
        with pytest.raises(TypeSafeAuthenticationError):
            await client.list_models()
        decision = await TypeSafeDecisionProvider(
            client, model="jev-latest"
        ).classify_route("hello")
    finally:
        await http.aclose()

    assert decision.kind is RouteKind.CONVERSATION
    assert paths == ["/v1/models", "/v1/systemone"]


@pytest.mark.asyncio
async def test_returned_serving_model_may_differ_from_requested_alias() -> None:
    requested_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_models.append(json.loads(request.content)["model"])
        return httpx.Response(200, json=_choice_response("conversation"))

    client, http = _client(handler)
    try:
        raw = await client.system_one(
            SystemOneRequest(
                state={"message": "hello"},
                model="jev-latest",
                questions={
                    "decision": ChoiceQuestion(
                        criteria={"conversation": "ordinary conversation"}
                    )
                },
            )
        )
    finally:
        await http.aclose()

    assert requested_models == ["jev-latest"]
    assert raw.model == "jev-1.13.0"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, TypeSafeAuthenticationError),
        (429, TypeSafeRateLimitError),
        (503, TypeSafeUnavailable),
    ],
)
async def test_statuses_map_to_typed_errors(status: int, error: type[Exception]) -> None:
    client, http = _client(lambda request: httpx.Response(status, json={"secret": API_KEY}))
    try:
        with pytest.raises(error):
            await client.list_models()
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_timeout_maps_to_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client, http = _client(handler)
    try:
        with pytest.raises(TypeSafeTimeout):
            await client.list_models()
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_malformed_response_fails_closed() -> None:
    client, http = _client(lambda request: httpx.Response(200, json={"model": MODEL}))
    try:
        with pytest.raises(TypeSafeInvalidResponse):
            await client.system_one(
                SystemOneRequest(
                    state={"message": "hello"},
                    model=MODEL,
                    questions={"decision": ChoiceQuestion(criteria={"yes": "yes"})},
                )
            )
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_optional_model_discovery_can_reject_unknown_model() -> None:
    client, http = _client(
        lambda request: httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "different-model",
                        "description": "Other",
                        "release_date": "2026-01-01",
                    }
                ]
            },
        )
    )
    try:
        with pytest.raises(TypeSafeModelUnavailable):
            await client.require_model(MODEL)
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_unknown_decision_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_choice_response("execute_everything"))

    client, http = _client(handler)
    try:
        with pytest.raises(TypeSafeInvalidResponse):
            await TypeSafeDecisionProvider(client, model=MODEL).classify_route("do it")
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_confirmation_decision_is_rocky_owned_enum() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_choice_response("amend"))

    client, http = _client(handler)
    try:
        decision = await TypeSafeDecisionProvider(
            client, model=MODEL
        ).classify_confirmation("except the archive", "1. create\n2. archive")
    finally:
        await http.aclose()
    assert decision.kind is ConfirmationKind.AMEND


@pytest.mark.asyncio
async def test_plan_verification_uses_system_one_without_discovery() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        payload = json.loads(request.content)
        assert set(payload["questions"]) == {
            "unrequested_action",
            "omitted_action",
            "excessive_mutation",
            "faithful",
        }
        assert all(
            question["type"] == "noul"
            for question in payload["questions"].values()
        )
        return httpx.Response(
            200,
            json=_noul_response(
                unrequested_action=0.04,
                omitted_action=0.03,
                excessive_mutation=0.02,
                faithful=0.91,
            ),
        )

    compiled = PlanCompiler().compile(
        ProposedPlan(
            steps=[
                ProposedPlanStep(
                    id="step_1",
                    action="project.create",
                    arguments={"name": "One"},
                    purpose="Create One",
                ),
                ProposedPlanStep(
                    id="step_2",
                    action="project.create",
                    arguments={"name": "Two"},
                    purpose="Create Two",
                ),
            ]
        )
    )
    client, http = _client(handler)
    try:
        decision = await TypeSafeDecisionProvider(
            client, model=MODEL
        ).verify_plan("Create projects One and Two", compiled)
    finally:
        await http.aclose()

    assert decision.faithful_probability == 0.91
    assert decision.concern_probabilities == {
        "unrequested_action": 0.04,
        "omitted_action": 0.03,
        "excessive_mutation": 0.02,
    }
    assert paths == ["/v1/systemone"]


@pytest.mark.asyncio
async def test_malformed_noul_plan_verification_fails_closed() -> None:
    response = _noul_response(
        unrequested_action=0.90,
        omitted_action=0.02,
        excessive_mutation=0.02,
        faithful=0.10,
    )
    response["answers"]["unrequested_action"] = {
        "type": "choice",
        "choice": "yes",
        "confidence": 0.9,
        "probabilities": {"yes": 0.9},
    }
    client, http = _client(
        lambda request: httpx.Response(200, json=response)
    )
    compiled = PlanCompiler().compile(
        ProposedPlan(
            steps=[
                ProposedPlanStep(
                    id="step_1", action="project.create",
                    arguments={"name": "One"},
                ),
                ProposedPlanStep(
                    id="step_2", action="project.create",
                    arguments={"name": "Two"},
                ),
            ]
        )
    )
    try:
        with pytest.raises(TypeSafeInvalidResponse):
            await TypeSafeDecisionProvider(
                client, model=MODEL
            ).verify_plan("Create One and Two", compiled)
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_system_one_model_rejection_is_typed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "loc": ["body", "model"],
                        "msg": "Unknown model alias",
                        "type": "value_error",
                    }
                ]
            },
        )

    client, http = _client(handler)
    try:
        with pytest.raises(TypeSafeModelUnavailable):
            await TypeSafeDecisionProvider(
                client, model="not-a-model"
            ).classify_route("hello")
    finally:
        await http.aclose()


def test_provider_disabled_and_missing_configuration_fail_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TYPESAFE_ENABLED", "false")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_MODEL", raising=False)
    assert get_decision_provider() is None
    assert get_decision_policy().plan_verification_enabled is False

    monkeypatch.setenv("TYPESAFE_ENABLED", "true")
    assert get_decision_provider() is None


@pytest.mark.asyncio
async def test_api_key_never_appears_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client, http = _client(
        lambda request: httpx.Response(401, json={"detail": API_KEY})
    )
    try:
        with pytest.raises(TypeSafeAuthenticationError):
            await TypeSafeDecisionProvider(client, model=MODEL).classify_route("hello")
    finally:
        await http.aclose()
    assert API_KEY not in caplog.text
