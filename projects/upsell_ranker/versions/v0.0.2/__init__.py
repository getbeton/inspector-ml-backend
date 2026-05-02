from shared.observability import init_observability

init_observability()

from upsell_agent.agent import root_agent  # noqa: E402  must run after observability init
