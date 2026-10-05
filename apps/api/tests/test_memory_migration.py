"""Execute the new revision on a disposable predecessor schema, never production."""
import importlib.util
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.autogenerate import compare_metadata
from sqlalchemy import create_engine, inspect

from app.db.base import Base
from app.models import Memory
from app.conversation.understanding import PERSONAL_SCOPES, safe_world_payload
from app.conversation.resolver import MemoryRef, WorldView
from app.conversation.plans.base import ProposedPlan, ProposedPlanStep
from app.conversation.plans.memory_confirmation import MemoryForgetPlan
from app.conversation.plans.compiler import PlanCompiler
from pydantic import ValidationError
import pytest
import uuid


def test_memory_model_and_migration_match_and_downgrade_cleanly():
    path = Path(__file__).parents[1] / "alembic/versions/0017_personal_memory.py"
    spec = importlib.util.spec_from_file_location("memory_revision", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    assert revision.revision == "0017_personal_memory"
    assert revision.down_revision == "0016_conversation_pending_plans"
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name != Memory.__tablename__])
    with engine.begin() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        with Operations.context(context):
            revision.upgrade()
        assert compare_metadata(context, Base.metadata) == []
        inspector = inspect(connection)
        fks = {fk["constrained_columns"][0]: fk for fk in inspector.get_foreign_keys("personal_memories")}
        assert fks["user_id"]["options"]["ondelete"] == "CASCADE"
        assert fks["source_thread_id"]["options"]["ondelete"] == "SET NULL"
        assert len(inspector.get_check_constraints("personal_memories")) == 6
        with Operations.context(context):
            revision.downgrade()
        assert "personal_memories" not in inspect(connection).get_table_names()
    engine.dispose()


def test_phase_a_does_not_expose_memory_to_ci6_or_world_serialization():
    assert PERSONAL_SCOPES == ("projects", "tasks", "reminders", "notifications", "notes", "lists")
    world = WorldView(projects=(), tasks=(), memories=(MemoryRef(uuid.uuid4(), "Private", "fact", "active"),))
    assert "memories" not in safe_world_payload(world)
    assert "Private" not in str(safe_world_payload(world))


def test_single_forget_envelope_does_not_widen_provider_plan_contract():
    step = ProposedPlanStep(id="step_1", action="memory.forget", reference="Deployment")
    with pytest.raises(ValidationError):
        ProposedPlan(steps=[step])
    internal = MemoryForgetPlan(steps=[step])
    assert PlanCompiler().compile(internal).requires_confirmation is True
    with pytest.raises(ValidationError):
        MemoryForgetPlan(steps=[ProposedPlanStep(id="step_1", action="project.create", arguments={"name": "Unsafe"})])


@pytest.mark.asyncio
async def test_memory_plan_failure_logs_do_not_include_private_exception_text(caplog):
    import logging
    from unittest.mock import AsyncMock
    from app.conversation.plans.executor import PlanExecutor

    private = "PRIVATE-MEMORY-CONTENT"
    proposed = ProposedPlan(steps=[
        ProposedPlanStep(id="step_1", action="memory.remember", arguments={
            "kind": "fact", "subject": "Private", "content": private,
        }),
        ProposedPlanStep(id="step_2", action="project.create", arguments={"name": "Later"}),
    ])
    plan = PlanCompiler().compile(proposed)
    with caplog.at_level(logging.WARNING):
        result = await PlanExecutor().execute(
            plan, prepare_step=AsyncMock(return_value=(None, None)),
            execute_step=AsyncMock(side_effect=RuntimeError(private)), remember_result=AsyncMock(),
        )
    assert result.failed_step == "step_1"
    assert private not in caplog.text
    event = next(r for r in caplog.records if r.message == "Plan step failed")
    assert not event.exc_info
