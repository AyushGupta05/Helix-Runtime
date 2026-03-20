from __future__ import annotations

import asyncio

import typer
from langgraph.prebuilt import create_react_agent

from arbiter.civic import load_civic_tools
from arbiter.models import build_chat_model
from arbiter.settings import load_settings

app = typer.Typer(add_completion=False)


SYSTEM_PROMPT = """
You are Arbiter, an autonomous code-governance runtime.
Use Civic-provided tools carefully and stay within the user's stated objective and constraints.
Prefer auditability, minimal diff scope, and explicit validation.
""".strip()


async def run_agent(prompt: str):
    settings = load_settings()
    model = build_chat_model(settings)
    tools = await load_civic_tools(settings)
    agent = create_react_agent(model=model, tools=tools, prompt=SYSTEM_PROMPT)
    return await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})


@app.command()
def main(
    prompt: str = "List the Civic tools available to this profile.",
) -> None:
    result = asyncio.run(run_agent(prompt))
    final_message = result["messages"][-1]
    print(final_message.content)


if __name__ == "__main__":
    app()
