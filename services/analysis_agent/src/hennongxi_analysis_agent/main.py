"""Independently startable Analysis Agent application."""

from hennongxi_contracts import AgentName
from hennongxi_observability import create_observed_agent_app

PORT = 8002
app = create_observed_agent_app(AgentName.ANALYSIS, PORT)
