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

## License
FAITH is source-available, not open source. Personal, non-commercial use is allowed under
the [Business Source License 1.1](LICENSE). Company
or other organizational use requires a separate commercial license. See
[LICENSING.md](LICENSING.md).

Config files are monitored and loaded dynamically on change: you don't have to stop 
and restart FAITH to make changes.

The Web UI polls the Project Agent and shows Redis/config status from the current 
`config/` directory.

## Prerequisites
You need the following on the host machine before running FAITH:

- Docker Engine / Docker Desktop with Docker Compose available
- Python 3.10 or newer
- Git

Docker must be running before you use `faith init`.

## Optional host prerequisite scripts
The helper scripts install host prerequisites only. They do **not** install the FAITH
Python package for you.

### PowerShell
```powershell
.\setup.ps1
```

### Bash (Ubuntu / Debian)
```bash
sudo ./setup.sh
```

## Install FAITH from a source checkout
The verified source-install path is an editable install from a cloned checkout.

### Linux / macOS
```bash
git clone https://github.com/DelboyJay/faith-ai.git
cd "AI Agent Framework"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
faith --help
faith init
```

### Windows PowerShell
```powershell
git clone https://github.com/DelboyJay/faith-ai.git
cd "AI Agent Framework"
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
faith --help
faith init
```

`faith init` creates `~/.faith/`, copies config templates and archetypes there, starts
the FAITH Docker Compose stack, installs the default Ollama Project Agent model, and
opens `http://localhost:8080` when the Web UI responds.

Useful follow-up commands:

```bash
faith status
faith stop
faith restart
faith show-urls
```

`faith status` returns runtime information such as container state, Redis health, and
Project Agent status.

## Configuration
By default, the configuration files live under the /config folder.
These configuration files can be manually changed by the user or just ask the PA to
modify them for you.

