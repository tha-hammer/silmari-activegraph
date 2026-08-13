# BabyAGI on Active Graph

`../babyagi.py` is a reimplementation of the original BabyAGI autonomous
agent loop, rebuilt on Active Graph. The three behaviors (`initializer`,
`executor`, `task_creator`) replace the original loop's three steps.
The graph holds tasks and results. The event log records every action.
The frame carries the objective into every LLM call's system prompt.

## What's different

The original BabyAGI was about 100 lines of Python with task-list state
managed in a global variable. This version is roughly the same length
and trades the global variable for a typed graph, a persistent event
log, and reactive behaviors. The same loop, with continuity.

Practically that means:

- You can pause the run and resume it later from the trace file.
- You can fork the run mid-execution to try a different prompt for the
  executor.
- You can inspect every decision the system made by reading the event
  log.
- Tasks and results are first-class objects, not strings in a list.

After a run, the trace file is the source of truth — `activegraph
inspect <trace-file>` walks it interactively, and `graph.all_objects()`
on a loaded runtime gives you the typed objects (tasks, results, and
their `produced` relations) for programmatic summarization. The example
keeps its own output minimal so the trace is the deliverable, not
console scrollback.

## Running it

```bash
export ANTHROPIC_API_KEY='your-key-here'
python examples/babyagi.py "Write a comprehensive guide to ..."
```

Choose another API-key provider with the same example:

```bash
export OPENAI_API_KEY='your-key-here'
python examples/babyagi.py --provider openai "Write a comprehensive guide to ..."

export OPENROUTER_API_KEY='your-key-here'
python examples/babyagi.py --provider openrouter "Write a comprehensive guide to ..."
```

The objective is a CLI argument. The script runs until the event budget
is exhausted (default: 100 events, 60 seconds). The trace lands in
`traces/babyagi-<timestamp>.sqlite` and can be inspected with
`activegraph inspect <trace-file>`.

## Further reading

- *A Continuity Layer for Long-Running Agents* — the conceptual essay.
- *Active Graph: Event-Sourced Reactive State for Long-Running Agents*
  — the technical essay.
