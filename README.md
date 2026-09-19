# fx (fork)

This repository is a personal fork of [vercel-labs/fx](https://github.com/vercel-labs/fx), maintained by [ahrorbeksoft](https://github.com/ahrorbeksoft). It exists for experimentation and contributions. For the canonical project, documentation, releases, and issue tracking, use the upstream repository.

## What is fx?

fx is a coding agent CLI written in Zig: a small native binary that is open source (Apache-2.0), model-agnostic, and embeddable as a harness in larger systems. Its interface stays closer to a Unix shell than an IDE in the terminal.

- **Any model:** Vercel AI Gateway, CLIProxyAPI, ChatGPT or Grok subscriptions, or your own OpenAI-compatible endpoint such as Ollama or OpenRouter
- **Any interface:** interactive shell, one-shot `fx ask` for scripts, or embedded through libfx and ACP
- **Shell-like output:** inline rendering that preserves your terminal scrollback
- **Extensible:** skills, MCP servers, and subagents

## About this fork

This fork adds support for [CLIProxyAPI](https://github.com/luispater/CLIProxyAPI) as a model provider, which upstream does not include. It lets fx discover models and reasoning levels from a local CLIProxyAPI server (default `http://localhost:8317/v1`), authenticate with a pasted API key or `FX_CLIPROXYAPI_KEY`, and override the server URL with `FX_CLIPROXYAPI_BASE_URL`.

Other changes here are experimental and may diverge from upstream behavior.

To build and test from this checkout:

```bash
zig build          # build the binary to zig-out/bin/fx
zig build test     # run all unit tests
```

## More information

- Upstream repository: [vercel-labs/fx](https://github.com/vercel-labs/fx)
- Full documentation: [fx.sh/docs](https://fx.sh/docs)
- License: [Apache-2.0](LICENSE)
