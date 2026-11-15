# Agent cores

All selectable orchestration implementations are grouped here so core-specific
code does not leak into the web or storage layers.

```text
cores/
├── local/          project-native local orchestration adapter
├── dify/           optional Dify compatibility adapter
└── jwave_flow/     migrated jwave_flow workflow, bridge and adapter
```

`app/adapters.py` is now only the registry and the non-core execution/dashboard
boundary. The selected core still comes from `ORCHESTRATION_BACKEND`; the HTTP
contract remains `CoreBackend.info()` plus `CoreBackend.events(ChatRequest)`.

`MODEL_CONFIG_MODE` describes model ownership rather than core identity:
`managed` cores consume gateway profiles/BYOK; `self` cores own their model
configuration. Local is always managed, Dify is always self, and jwave_flow can
use either mode.

The `jwave_flow` core contains a migrated copy of the required workflow source.
Its Docker build reads only the current repository and has no path dependency on
another checkout.
