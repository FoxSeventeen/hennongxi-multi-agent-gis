"""Independently startable Master Agent application."""

from hennongxi_contracts import AgentName
from hennongxi_observability import create_observed_agent_app

from hennongxi_master.health import install_master_health_routes

PORT = 8000
app = create_observed_agent_app(AgentName.MASTER, PORT)
install_master_health_routes(app)
