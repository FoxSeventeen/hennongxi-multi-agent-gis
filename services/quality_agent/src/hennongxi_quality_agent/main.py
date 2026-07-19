"""Independently startable Quality Agent application."""

from hennongxi_contracts import AgentName
from hennongxi_observability import create_observed_agent_app

PORT = 8003
app = create_observed_agent_app(AgentName.QUALITY, PORT)
