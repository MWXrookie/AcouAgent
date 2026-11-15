# jwave_flow migration source

The workflow files in `workflow/`, the read-only knowledge base in `kb/`, and
the bridge were migrated from the `jwave_codeagent` project at commit
`07c0355` on 2026-10-29. Both projects use the MIT license.

Only the agent workflow was retained. During migration:

- `.env` loading and environment-driven model selection were removed;
- subprocess, Jupyter and MCP executor implementations were removed;
- TUI, history, memory, session storage, panels and the ordinary web frontend
  were not copied;
- the workflow now requires an injected executor supplied by the restricted
  bridge;
- runtime model, knowledge-base and executor values are fixed by the bridge at
  process startup.

There is no runtime or Docker build reference to another repository.
