import uuid

from app.services.default_agents import build_default_agents


def test_default_agents_do_not_impersonate_another_office():
    agents = build_default_agents(uuid.uuid4())
    assert len(agents) == 4
    for agent in agents:
        assert "GVA" not in agent.instructions
        assert "Gilberto Vieira" not in agent.instructions
