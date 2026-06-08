from shared.observability import init_observability

# Initialize AgentOps + Langfuse before any agent is constructed. Each agent
# module also calls this (ADK can load agent modules without importing this
# package); init_observability() is idempotent so the duplicate call is a no-op.
init_observability()
