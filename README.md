# FAITH

Framework AI Team Hive.

FAITH is a system for running a small team of AI agents that work together on a software 
project under the control of a single coordinator called the Project Agent. In plain terms, 
you give FAITH a job like "add login with password reset and update the tests", it can 
break that work into parts, assign them to one or more specialist agents, keep track of 
what each one is doing, ask you for approval before risky actions, and show the whole 
process in a web UI.

But this is what Claude Code and Codex already does so why do I need it?

Because this runs 100% locally on your machine using Ollama and models of your choice,
and it can also integrate with OpenRouter if you want access to paid-for models like GPT
or anthropic.

Example: if you ask FAITH to add a new API endpoint, the Project Agent could have one
agent inspect the existing code, another update the endpoint implementation, another 
update tests, and then bring the results back together. If one agent needs to edit 
files or run a command that should be approved first, FAITH pauses and asks you instead 
of doing it silently.

FAITH uses disposable Linux Docker sandboxes for agent execution. The Project Agent may
assign a shared sandbox or an isolated sandbox per sub-agent when isolation is needed.
Agents can have root access inside that sandbox container so they can install packages,
modify the container, and recover from normal sandbox permission problems. The safety 
boundary is the container boundary: only the PA gets Docker socket access, sandboxes do 
not get the Docker socket, sandboxes do not run in privileged mode, sandboxes do not
use host networking, and they only receive explicitly approved mounts. If a sandbox is
polluted or broken, the PA destroys it and creates a fresh one instead of trying to
repair it in place.

The Web UI polls the Project Agent and shows Redis/config status from the current 
`config/` directory.

## CLI

The repo now includes a `faith-cli` bootstrap package that wraps the current POC 
compose stack.

```powershell
faith init
faith status
faith stop
```

`faith init` creates `~/.faith/`, copies config templates and archetypes there, starts the repo-backed Docker Compose stack, and opens `http://localhost:8080` when the Web UI responds.


