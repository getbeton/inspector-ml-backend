# Agentic upsale opportunities system

## Terms
User - user of our agentic system, usually a company that wants to find upsell opportunities based on various signals from their customers.
May be a B2B or PLG company.  
Agent - our system that analyzes customer behavior and provides upsell opportunities based on the data provided by the user.
Client - company(B2B) or user(PLG) of our user services that may be potential upsell targets we want to highlight.

## Description
This system will rank upsell opportunities based on the client's behavior that is observed in our user Posthog DWH. 
It uses basic signal analysis to observe clients interactions with users product and predict which products or services are most likely to be upsold to each client.


Entrypoint of this system is upsell_agent agent.
Since we consider two main ways of upsale at the moment: B2B and PLG we also analyze what exactly our user sells and how before proceeding.
We gather this data based on user input parameter for company website or try to deduce it via email.
Then we make initial assumptions on what and how to upsell and forward this summary to data analyst agent.  

Data analyst performs basic EDA in Posthog DWH, connects datasources to provide comprehensive signal summary on each client. 
Consider it a basic version of pd.describe() but with capabilities to adapt to different parameter namings that may be
tailored to specific user database naming conventions. Summary and assumptions on available data is made by this agent as
well as suggestions on possible joins and filtering. 

This is forwarded to the Signal agent that picks most promising
upsell opportunities according to generic analysis and writes SQL queries that perform basic signal analysis. Since such
queries may be harmfull we pair it with LLM judge that checks each query for potential harmful patterns, to prevent their
execution. Queries are then executed in search of possible signals to which then signal agent writes a short description
of the signal and its significance. 

Finally this summary, paired with EDA summary and information about the company is returned
back to the upsell_agent to generate final rankings for upsell opportunities, based on signals, combined with a sales
readable explanation, potential salesplays and next steps for the sales team to perform.

## Agent schema

upsell_agent(user exploration) -> data analyst(eda) -> signal agent(signal analysis) -> upsell_agent(rankings+steps)

## Next steps

Left off status and next focus (for tomorrow):

  - upsell_ranker_full/v0.0.2/upsell_agent/agent.py now uses fetch_company_homepage(url) instead of google_search, builds a company summary from homepage HTML, and then calls dwh_analyst (sub-agent) before emitting JSON.
  - dwh_analyst and signal_agent are wired as sub-agents, but dwh_analyst currently returns without doing real DWH work, so the output falls back to generic upsell proposals.
  - We agreed next work is on dwh_analyst to implement:
      - PostHog DWH access (read-only).
      - EDA: table discovery, schema inspection, event/user/client table identification.
      - Join strategy across sources to map users/clients.
      - Caching to avoid repeated calls (check v0.0.1 for existing caching approach).

  Notes for tomorrow:

  - Extend dwh_analyst with a real PostHog DWH tool (stub if MCP not ready).
  - Add minimal caching for homepage fetch + DWH metadata/queries (reuse v0.0.1 patterns).
  - Ensure the chain completes: upsell_agent → dwh_analyst (EDA) → signal_agent (SQL signals) → upsell_agent (rankings).

  If you want me to prep a caching helper and PostHog tool stub in v0.0.2 to start the next session, just say the word.