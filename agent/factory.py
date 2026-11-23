"""Wire the agent stack together from Settings/AgentConfig (used by the web server and scripts)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agent.calendar_integration import CalendarService, CalendarWorker
from agent.clickup import ClickUpClient, ClickUpSync, ClickUpWorker
from agent.config import AgentConfig
from agent.delegation import Dispatcher
from agent.email_integration import EmailService, EmailWorker
from agent.llm import OllamaChat, OllamaEmbedder
from agent.loop import Agent
from agent.orchestrator import Orchestrator
from agent.store import AgentStore
from agent.workers import CodeWorker, ResearchWorker
from config import Settings

logger = logging.getLogger(__name__)


@dataclass
class AgentStack:
    agent: Agent
    dispatcher: Dispatcher
    clickup: ClickUpClient
    email: EmailService
    calendar: CalendarService


def build_agent_stack(
    settings: Settings,
    config: AgentConfig | None = None,
    *,
    clickup: ClickUpClient | None = None,
    email: EmailService | None = None,
    calendar: CalendarService | None = None,
) -> AgentStack:
    config = config or AgentConfig.from_env()
    store = AgentStore(config.db_path)
    embedder = OllamaEmbedder(settings.ollama_host, config.embed_model)
    llm = OllamaChat(settings.ollama_host, settings.ollama_model, model_getter=lambda: settings.ollama_model)
    agent = Agent(settings, config, store, embedder, llm)
    dispatcher = Dispatcher(store)
    clickup = clickup or ClickUpClient.from_settings(settings)
    email = email or EmailService.from_settings(settings, store=store, llm=llm)
    calendar = calendar or CalendarService(settings)
    dispatcher.register(ResearchWorker(agent.rag, llm))
    dispatcher.register(CodeWorker(settings))
    dispatcher.register(ClickUpWorker(clickup))
    dispatcher.register(EmailWorker(email, llm=llm))
    dispatcher.register(CalendarWorker(calendar))
    dispatcher.result_hooks.append(ClickUpSync(clickup))
    agent.orchestrator = Orchestrator(settings, dispatcher)
    agent._integration_email = email  # noqa: SLF001 — numbered action dispatch
    agent._integration_calendar = calendar  # noqa: SLF001
    logger.info(
        "Agent stack ready: db=%s vector=%s workers=%s clickup=%s email=%s calendar=%s",
        config.db_path,
        store.vector_backend,
        list(dispatcher.workers),
        "configured" if clickup.configured else "no token",
        "configured" if email.configured else "no creds",
        "configured" if calendar.configured else "no creds",
    )
    return AgentStack(agent=agent, dispatcher=dispatcher, clickup=clickup, email=email, calendar=calendar)
