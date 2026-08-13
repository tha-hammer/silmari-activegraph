"""BabyAGI's autonomous agent loop, rebuilt on Active Graph.

The original BabyAGI (Nakajima, 2023) was a while-true loop with three
steps: execute the current task, summarize against the objective,
generate follow-up tasks. State lived in a global list.

Here each step is a reactive behavior over a shared graph. The loop is
event propagation; the state is the graph.

    original step             becomes                     reacts to
    -----------------         -----------------           --------------
    seed the first task       @behavior initializer       goal.created
    execute current task      @llm_behavior executor      object.created
                                                          (type=task)
    generate follow-ups       @llm_behavior task_creator  task.executed

Each step is a subscription, not a function call, so the loop runs as
long as new task objects keep landing on the graph. The trace records
every mutation; the frame carries the objective into every LLM call.

Run it (defaults to Anthropic):

    export ANTHROPIC_API_KEY='your-key-here'
    python examples/babyagi.py "Plan a 3-day intro to Rust"

Switch providers with --provider openai (CONTRACT v1.0.1 #5):

    export OPENAI_API_KEY='your-key-here'
    python examples/babyagi.py --provider openai "Plan a 3-day intro to Rust"

Or use OpenRouter:

    export OPENROUTER_API_KEY='your-key-here'
    python examples/babyagi.py --provider openrouter "Plan a 3-day intro to Rust"
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from activegraph import (
    Frame,
    Graph,
    Runtime,
    behavior,
    clear_registry,
    llm_behavior,
    register,
)
from activegraph.llm import AnthropicProvider, OpenAIProvider, OpenRouterProvider


# Edit to taste — these ride along on every LLM call as frame constraints.
DEFAULT_CONSTRAINTS = [
    "Be specific and actionable — avoid vague generalities.",
    "Build incrementally on previous results rather than repeating them.",
]

# One source of truth for this API-key example's provider selection,
# construction, and credential lookup. ClaudeCodeProvider is deliberately
# absent because it uses subscription/CLI auth rather than this flow.
PROVIDER_SPECS = {
    "anthropic": {
        "factory": AnthropicProvider,
        "env": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "factory": OpenAIProvider,
        "env": "OPENAI_API_KEY",
    },
    "openrouter": {
        "factory": OpenRouterProvider,
        "env": "OPENROUTER_API_KEY",
    },
}


class TaskResult(BaseModel):
    result: str = Field(description="A detailed answer to the task, 2-5 concrete sentences.")


class NewTasks(BaseModel):
    tasks: list[str] = Field(
        description=(
            "0-4 follow-up tasks, each an imperative sentence. Empty list "
            "only if the objective is fully accomplished."
        )
    )


@behavior(name="initializer", on=["goal.created"])
def initializer(event, graph, ctx):
    # Bootstraps the loop: a single seed task so executor has something to fire on.
    goal = event.payload.get("goal", "")
    graph.add_object("task", {"title": f"Plan the first step toward: {goal}", "status": "pending"})


@llm_behavior(
    name="executor",
    on=["object.created"],
    where={"object.type": "task"},
    description=(
        "You are the EXECUTION agent in a BabyAGI loop. Carry out the task "
        "concretely. Do NOT plan further steps — another behavior handles that."
    ),
    output_schema=TaskResult,
    creates=["result"],
)
def executor(event, graph, ctx, llm_output: TaskResult):
    # Reacts to every newly-created task. No central scheduler picks what
    # runs next — the graph itself is the queue, and the event log is the order.
    task = event.payload["object"]
    if task["data"].get("status") != "pending":
        return
    graph.patch_object(task["id"], {"status": "completed"})
    result = graph.add_object("result", {"task_id": task["id"], "content": llm_output.result})
    graph.add_relation(task["id"], result.id, "produced")
    graph.emit("task.executed", {"task_id": task["id"], "result": llm_output.result})


@llm_behavior(
    name="task_creator",
    on=["task.executed"],
    description=(
        "You are the TASK-CREATION agent in a BabyAGI loop. Given the last "
        "result and the overall objective, propose 0-4 follow-ups. Return "
        "an empty list ONLY when the objective is fully accomplished."
    ),
    output_schema=NewTasks,
    creates=["task"],
)
def task_creator(event, graph, ctx, llm_output: NewTasks):
    # Each new task fires `executor` again — that propagation IS the loop.
    # Returning an empty list is how it terminates without a while-condition.
    for title in llm_output.tasks:
        if title.strip():
            graph.add_object("task", {"title": title.strip(), "status": "pending"})


def run_babyagi(
    objective: str,
    *,
    provider: str = "anthropic",
    constraints: list[str] | None = None,
    max_events: int = 100,
    max_seconds: int = 60,
) -> str:
    try:
        provider_spec = PROVIDER_SPECS[provider]
    except KeyError as exc:
        choices = ", ".join(PROVIDER_SPECS)
        raise ValueError(
            f"unknown LLM provider {provider!r}; choose one of: {choices}"
        ) from exc

    # Decorators register on import; clear+re-register makes this function
    # safe to call multiple times — see docs/cookbook/multi-run-scripts.md.
    clear_registry()
    for b in (initializer, executor, task_creator):
        register(b)

    # v1.0.2 #1: the @llm_behaviors above omit model=, so the runtime
    # resolves each provider's default_model at registration time —
    # "claude-sonnet-4-5" for Anthropic, "gpt-4o-mini" for OpenAI, and
    # "openrouter/free" for OpenRouter. No model table is needed here.
    llm_provider = provider_spec["factory"]()

    os.makedirs("traces", exist_ok=True)
    trace_path = f"traces/babyagi-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.sqlite"
    runtime = Runtime(
        Graph(),
        frame=Frame(goal=objective, constraints=list(constraints or DEFAULT_CONSTRAINTS)),
        llm_provider=llm_provider,
        budget={"max_events": max_events, "max_seconds": max_seconds},
        persist_to=trace_path,
    )
    runtime.run_goal(objective)
    return trace_path


def main() -> int:
    parser = argparse.ArgumentParser(description="BabyAGI on Active Graph.")
    parser.add_argument("objective", nargs="?",
                        default="Plan a 3-day intro to Rust programming")
    parser.add_argument("--provider", choices=tuple(PROVIDER_SPECS),
                        default="anthropic",
                        help="LLM provider; defaults to anthropic")
    args = parser.parse_args()

    env = PROVIDER_SPECS[args.provider]["env"]
    if not os.environ.get(env):
        print(
            f"{env} environment variable not set. Set it with:\n"
            f"  export {env}='your-key-here'",
            file=sys.stderr,
        )
        return 1

    trace_path = run_babyagi(args.objective, provider=args.provider)
    print(f"\ntrace: {trace_path}")
    print(f"inspect with: activegraph inspect {trace_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
