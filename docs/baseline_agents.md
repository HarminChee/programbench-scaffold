# Baseline Agent And Model Options

Status: checked on 2026-06-19.

The project should separate two axes:

```text
agent harness: how the model reads files, runs commands, edits code, and manages the loop
model: the underlying LLM used by the harness
```

For this project, the cleanest scientific comparison is to hold the model and harness fixed, then add or remove our scaffold.

## Recommended Harness Baselines

### 1. mini-swe-agent

Use this as the primary baseline.

Why:

- ProgramBench officially documents `mini-extra programbench`;
- output is directly compatible with `programbench eval`;
- the ProgramBench paper uses mini-SWE-agent as the main scaffold;
- it is deliberately minimal, which helps isolate the value of our scaffold.

Command entry point:

```bash
uvx --from mini-swe-agent mini-extra programbench --help
```

Source: [mini-swe-agent ProgramBench docs](https://mini-swe-agent.com/latest/usage/programbench/).

### 2. Claude Agent SDK / Claude Code

Use as a secondary engineering baseline if we want a stronger production-style agent loop.

Why:

- provides a programmable agent loop;
- includes built-in file read, command execution, and code editing tools;
- useful if we want to inject scaffold summaries into a well-supported coding-agent framework.

Risk:

- less directly comparable to the ProgramBench paper than mini-swe-agent.

Source: [Claude Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview).

### 3. Aider

Use only as an optional editing-agent comparison.

Why:

- popular terminal-based coding assistant;
- strong model compatibility;
- public coding leaderboards.

Risk:

- not designed specifically for ProgramBench batch submission;
- may require more adapter work than mini-swe-agent.

Source: [Aider GitHub](https://github.com/aider-ai/aider), [Aider leaderboards](https://aider.chat/docs/leaderboards/).

## Recommended Model Set

### Frontier closed-model track

Use one strong model for the main paper-quality scaffold comparison.

Good candidates:

```text
OpenAI GPT-5.5
Anthropic Claude Opus 4.8
Anthropic Claude Sonnet 4.6
Google Gemini 3.1 Pro
```

Rationale:

- OpenAI docs describe GPT-5.5 as the flagship model for complex reasoning and coding.
- Anthropic docs list Opus 4.8 and Sonnet 4.6 as current coding/agentic models.
- Google DeepMind positions Gemini 3.1 Pro as strong for agentic coding and tool use.

Sources:

- [OpenAI models](https://developers.openai.com/api/docs/models)
- [OpenAI GPT-5.5 guide](https://developers.openai.com/api/docs/guides/latest-model)
- [Claude models](https://platform.claude.com/docs/en/intro)
- [Gemini 3.1 Pro](https://deepmind.google/models/gemini/pro/)

### Cost/weak-agent track

This track is important because Robin's scaffold framing is especially interesting if the component helps weaker agents.

Good candidates:

```text
OpenAI GPT-5.4 mini
Anthropic Claude Haiku 4.5
Gemini Flash-family model available in the API/account
```

Use this track after the main scaffold works on one strong model.

### Open-weight / lower-cost track

Optional, useful if we want a reproducible or cheaper model comparison.

Good candidates:

```text
Qwen3-Coder-Next
Qwen3-Coder-30B-A3B-Instruct
DeepSeek-V4-Flash
DeepSeek-V4-Pro
```

Notes:

- Qwen3-Coder is explicitly positioned for coding agents and local development.
- DeepSeek-V4 is positioned as a long-context, cost-effective agent/coding model.
- Large open-weight models may not fit locally on this MacBook; use cloud inference or a server.

Sources:

- [Qwen3-Coder GitHub](https://github.com/QwenLM/Qwen3-Coder)
- [DeepSeek V4 release](https://api-docs.deepseek.com/news/news260424)

## Suggested MVP Choices

Start with two runs:

```text
Harness: mini-swe-agent
Strong model: Claude Sonnet 4.6 or GPT-5.5
Weak/cost model: GPT-5.4 mini or Claude Haiku 4.5
```

Run conditions:

```text
baseline mini-swe-agent
baseline mini-swe-agent + our scaffold
```

Why this is solid:

- mini-swe-agent keeps us aligned with ProgramBench;
- one strong model shows whether scaffold helps at the frontier;
- one cheaper/weaker model tests whether scaffold compensates for weaker exploration.

## Models Mentioned In The ProgramBench Paper

The ProgramBench paper reports experiments with models including:

```text
Claude Opus 4.7
Claude Opus 4.6
Claude Sonnet 4.6
Claude Haiku 4.5
Gemini 3.1 Pro
Gemini 3 Flash
GPT 5.4
GPT 5.4 mini
GPT 5 mini
```

This makes `mini-swe-agent + current Claude/OpenAI/Gemini models` the most defensible starting point.

Source: [ProgramBench paper](https://arxiv.org/abs/2605.03546).

