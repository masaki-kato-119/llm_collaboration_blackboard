# LLM Collaboration Blackboard

An implementation of version 1 of the [LLM Collaboration Blackboard Specification](llm_collaboration_blackboard_spec_final_v1.0_en.md).

LLM Collaboration Blackboard is a collaboration platform centered on Markdown that allows multiple LLMs and humans to share the same work context. Plans, tasks, events, memory, and state are collected in a single Blackboard and can be operated through an MCP server or a local dashboard.

The goal of this project is to preserve work as verifiable Plans and Events rather than as scattered chat logs. This makes it easier to track who is responsible for what, which tasks are in progress, and what changes have been made.

![LLM Collaboration Blackboard overview](llm-collaboration-blackboard-overview.png)

## What It Provides

- Plan management centered on Markdown and YAML Front Matter
- Task state transitions through claim / update / cancel / recover
- Recording and replay of audit events for each change
- Role-based operation restrictions (Roles are self-declared)
- Visualization of Plans and progress through a local dashboard

## Typical Workflow

1. Create a Plan and add tasks.
2. Assign roles such as Researcher, Implementer, and Reviewer.
3. Claim the tasks that are needed and begin work.
4. Update task state, and use blocked / recover to pause or resume work as needed.
5. Use Events and the Plan to preserve the work history and the background behind decisions.

This workflow is suitable for experiments running multiple LLMs in parallel, collaborative development, and implementation flows with review.

## Quick Start

### 1. Prerequisites

- Python 3.10 or later (3.13 recommended)
- Available on Windows, macOS, and Linux

### 2. Installation

```powershell
py -3.13 -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -e .[dev]
```

### 3. Prepare a Blackboard Root

```powershell
$env:BLACKBOARD_ROOT = "C:\\path\\to\\blackboard"
```

### 4. Start the Server

```powershell
.\\.venv\\Scripts\\blackboard-server
```

### 5. Start the Dashboard (Optional)

```powershell
.\\.venv\\Scripts\\blackboard-dashboard --config .\\dashboard.yaml
```

Open http://127.0.0.1:8765/ in a browser to view Plan and task status.

![Blackboard workflow](blackboard-workflow.png)

## Using MCP

This repository supports operating the Blackboard through MCP. Copy [.mcp.json.example](.mcp.json.example) in the repository root to `.mcp.json`, then replace the paths with absolute paths.

```json
{
  "mcpServers": {
    "blackboard": {
      "command": "<ABSOLUTE_PATH_TO_THIS_REPO>/.venv/Scripts/python.exe",
      "args": ["-m", "blackboard.server"],
      "env": {
        "BLACKBOARD_ROOT": "<ABSOLUTE_PATH_TO_THIS_REPO>/demo_blackboard"
      }
    }
  }
}
```

Representative tools include:

- `read_plan`: retrieve Plan contents and executable tasks
- `claim_task`: claim a task and begin work
- `update_task`: update task status to done / blocked / cancelled
- `add_task` / `edit_task` / `cancel_task`: update the Plan
- `recover_task`: return a blocked task to pending
- `read_memory` / `write_memory`: work with memory documents
- `read_state` / `write_state`: work with state documents

## Project Structure

- [src/blackboard](src/blackboard): server implementation, models, permission controls, and dashboard implementation
- [scripts](scripts): demos and utility scripts
- `blackboard`: Blackboard data used for examples and runtime
- [dashboard.yaml](dashboard.yaml): sample local dashboard configuration

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development rules and commit conventions.

```powershell
.\\.venv\\Scripts\\python.exe -m pytest
.\\.venv\\Scripts\\python.exe -m ruff check src tests scripts
```

## License

This project is released under the MIT License. See [LICENSE](LICENSE) for details.
