"""Unit tests for ORQEN Agent configuration and lifecycle state machine."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from orqen.agents import Agent, AgentConfig, AgentState
from orqen.exceptions import LifecycleError, ValidationError
from orqen.models import MockModel


def test_agent_initial_lifecycle_and_transitions() -> None:
    agent = Agent(name="researcher", model=MockModel(responses=["ok"]))
    assert agent.state == AgentState.READY

    agent.transition_to(AgentState.RUNNING)
    assert agent.state == AgentState.RUNNING

    agent.transition_to(AgentState.COMPLETED)
    assert agent.state == AgentState.COMPLETED

    agent.reset()
    assert agent.state == AgentState.READY

    agent.terminate()
    assert agent.state == AgentState.TERMINATED

    with pytest.raises(LifecycleError, match="Invalid agent state transition"):
        agent.transition_to(AgentState.READY)


def test_agent_rejects_empty_name_and_invalid_reset() -> None:
    with pytest.raises(ValidationError, match="non-empty"):
        Agent(name="  ", model=MockModel())

    agent = Agent(name="worker", model=MockModel())
    agent.transition_to(AgentState.RUNNING)
    with pytest.raises(LifecycleError, match="Cannot reset agent"):
        agent.reset()


def test_agent_config_validation() -> None:
    cfg = AgentConfig(max_iterations=5, max_tool_calls=10)
    assert cfg.max_iterations == 5
    with pytest.raises(PydanticValidationError):
        AgentConfig(max_iterations=0)
