"""Independently startable Data Agent application."""

from hennongxi_contracts import AgentName
from hennongxi_observability import create_observed_agent_app

PORT = 8001
app = create_observed_agent_app(AgentName.DATA, PORT)
