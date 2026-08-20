# LLM Collaboration Blackboard Specification

**Status:** Final Draft  
**Version:** 1.0  
**Date:** 2026-08-10

## 1. Purpose

This specification defines a minimal collaboration mechanism in which multiple LLMs collaborate through a shared Blackboard.

The goal is not to build a centralized LLM orchestrator. Each LLM has a predefined Role, independently observes the Blackboard, and decides what it can do next.

The Blackboard uses Markdown as its persistent representation and is accessed by LLMs through MCP.

The core model is as follows.

```text
Memory = known information
Plan   = work to be performed
State  = work currently in progress
Event  = things that have happened
Role   = responsibilities assigned to this LLM
LLM    = judgment and behavior
MCP    = means of accessing the Blackboard
```

The system is intentionally designed to be small. Orchestration of external tools is outside the scope of the core specification and can be connected later.

---

# 2. Design Principles

1. Multiple LLMs collaborate through a shared Blackboard.
2. LLMs do not need to communicate directly with one another.
3. Each LLM has a predefined Role.
4. The Blackboard is the shared source of coordination information.
5. Memory, Plan, State, and Event have distinct meanings.
6. The Plan is a shared checklist and the primary representation of work order.
7. LLMs select the next executable task from the Plan rather than receiving commands from a central authority.
8. MCP provides access to the Blackboard; it is not an orchestrator.
9. Markdown is the persistent representation.
10. The implementation should be as simple as possible.
11. Human users can inspect and modify the Blackboard.
12. External tools and systems are connected outside the core collaboration model.

---

# 3. Architecture

```text
                    ┌─────────────────────────────┐
                    │         Blackboard          │
                    │                             │
                    │  Memory                     │
                    │  Plan                       │
                    │  State                      │
                    │  Event                      │
                    │                             │
                    │          Markdown            │
                    └──────────────┬──────────────┘
                                   │
                                  MCP
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
        ┌───────────┐       ┌───────────┐       ┌───────────┐
        │   LLM A   │       │   LLM B   │       │   LLM C   │
        │ Researcher│       │Implementer│       │ Reviewer  │
        └───────────┘       └───────────┘       └───────────┘
```

The Blackboard is the center of collaboration.

There is no required central component that decides which LLM acts next.

---

# 4. Blackboard

The Blackboard is a shared information space containing Markdown documents.

The recommended structure is as follows.

```text
blackboard/
├── memory/
├── plan/
├── state/
└── event/
```

The physical directory structure is an implementation recommendation, not a protocol requirement.

Each document may contain a Markdown body preceded by YAML Front Matter.

---

# 5. Memory

## 5.1 Purpose

Memory stores knowledge, experience, decisions, observations, and other information that may be useful for future work.

Example:

```yaml
---
id: architecture_decision_001
type: memory
created: 2026-08-10
updated: 2026-08-10
importance: high
tags:
  - architecture
  - decision
---
```

Memory is persistent knowledge.

The core specification does not prescribe a particular retrieval mechanism. RAG, search indexes, GraphRAG, and other mechanisms can be added independently.

---

# 6. Plan

## 6.1 Purpose

The Plan is a representation for sharing the work that should be performed.

It is intentionally implemented as a checklist rather than as a complex workflow engine.

A Plan records:

- Which tasks exist
- Which Role is responsible
- The task status
- Who started the task
- When it was started
- Who completed it
- When it was completed
- Task dependencies
- Optional grouping for parallel work

Example:

```markdown
# Plan: Project Alpha

| ID | Task | Role | Status | Started By | Started | Completed By | Completed |
|---|---|---|---|---|---|---|---|
| 1 | Research | Researcher | done | llm-a | 09:00 | llm-a | 09:30 |
| 2 | Architecture | Architect | done | llm-b | 09:40 | llm-b | 10:20 |
| 3 | Implementation | Implementer | in_progress | llm-c | 10:30 | | |
| 4 | Security Review | Reviewer | pending | | | | |
| 5 | Performance Review | Reviewer | pending | | | | |
| 6 | Final Review | Reviewer | pending | | | | |
```

Dependencies can be represented separately.

```yaml
dependencies:
  4:
    - 3
  5:
    - 3
  6:
    - 4
    - 5
```

This means that tasks 4 and 5 can proceed in parallel after task 3 is complete, while task 6 waits for both of them to finish.

## 6.2 Task States

The minimum task states are:

- `pending`
- `in_progress`
- `done`
- `blocked`
- `cancelled`

Additional states may be added as needed, but the core model should remain small.

## 6.3 Selecting the Next Task

An LLM determines which task to perform next by checking:

1. Its own Role
2. Pending tasks
3. Task dependencies
4. The current State
5. Related Memory

A task is executable when:

- Its status is `pending`
- Its Role matches the LLM's Role
- All required dependencies are `done`
- There are no blocking conditions

The LLM then changes the task to `in_progress` and claims it.

This is the basic collaboration mechanism.

---

# 7. State

## 7.1 Purpose

State represents the current situation of a task or of the collaboration as a whole.

Example:

```yaml
---
id: project_alpha
type: state
created: 2026-08-10
updated: 2026-08-10T14:30:00+09:00
revision: 3
status: in_progress
current_task: 3
---
```

State answers the question:

> What is currently in progress?

The Plan answers the question:

> What should be done?

They are intentionally separate.

---

# 8. Event

## 8.1 Purpose

An Event records that something happened on the Blackboard.

An Event is not a command addressed to a specific LLM.

Example:

```yaml
---
id: event_000123
type: event
created: 2026-08-10T14:35:00+09:00
event_type: task_completed
source: llm-c
task_id: 3
---
```

Events may record:

- Task started
- Task completed
- Task blocked
- State changed
- Plan changed
- Human intervention
- Other relevant facts

In the initial implementation, Events are used mainly for observation and auditing.

The system does not require an Event to include a specific target LLM.

---

# 9. Role

Each LLM has a predefined Role.

Example:

```text
LLM A = Researcher
LLM B = Architect
LLM C = Implementer
LLM D = Reviewer
```

A Role defines the responsibilities and capabilities expected of that LLM.

A Role is not an instruction dynamically sent by another LLM.

An LLM uses its Role to determine which Plan tasks are relevant to it.

---

# 10. Collaboration Model

The basic collaboration loop is as follows.

```text
        ┌─────────────────────┐
        │      Blackboard     │
        │                     │
        │ Plan / State /      │
        │ Memory / Event      │
        └──────────┬──────────┘
                   │
                   ▼
              LLM observes
                   │
                   ▼
          Select executable task
                   │
                   ▼
              Claim task
                   │
                   ▼
                Work
                   │
                   ▼
          Update Blackboard
                   │
                   ▼
               Emit Event
                   │
                   └───────────────► next LLM
```

The next LLM to act is not selected by a central orchestrator.

It discovers work by inspecting the shared Plan.

---

# 11. Parallelism and Synchronization

The system does not initially require a dedicated scheduler.

Parallelism arises from the Plan.

Example:

```text
                 Implementation
                       │
              ┌────────┴────────┐
              ▼                 ▼
        Security Review   Performance Review
              │                 │
              └────────┬────────┘
                       ▼
                  Final Review
```

Tasks 4 and 5 can be independently claimed by compatible LLMs.

Task 6 becomes executable only after both required dependencies are `done`.

This provides the following minimum capabilities:

- Ordering
- Dependencies
- Parallel execution
- Synchronization

However, it does not introduce a workflow engine.

---

# 12. Timing

Timing is represented mainly by the Plan and task history.

Each task may record:

- `started`
- `completed`
- `started_by`
- `completed_by`

The Plan therefore provides a shared execution timeline.

More advanced scheduling, deadlines, periodic triggers, timeouts, and temporal constraints are outside the MVP scope and can be added later as needed.

---

# 13. MCP

MCP provides access to the Blackboard.

The initial logical operations are:

```text
read_memory
write_memory
read_plan
claim_task
update_task

read_state
write_state

read_event
emit_event
```

The exact MCP tool and resource schemas are implementation-dependent.

MCP does not:

- Select an LLM
- Assign Roles
- Decompose tasks
- Decide what an LLM should do
- Act as a workflow orchestrator
- Manage external tools

MCP is the access layer for the shared Blackboard.

---

# 14. Concurrency

Multiple LLMs may try to claim or update the same task.

At a minimum, the implementation must prevent two LLMs from claiming the same task at the same time.

A simple compare-and-set or revision mechanism is sufficient.

Example:

```text
LLM A reads the task and determines that it is pending
LLM B also reads the task and determines that it is pending

LLM A claims the task
Task = in_progress

LLM B tries to claim it
The claim is rejected

LLM B reads the Plan again
It selects another executable task
```

The implementation should prefer the simplest reliable mechanism possible.

A complete distributed locking system is unnecessary.

---

# 15. Human-in-the-loop

Humans are first-class participants in the Blackboard.

Humans can:

- Create and modify Plans
- Change task status
- Add Memory
- Change State
- Inspect Events
- Pause and resume work
- Add and remove tasks

Example:

```yaml
status: blocked
reason: "Architecture decision required"
```

This brings human judgment into the same collaboration space as the LLMs.

---

# 16. Session and Environment Continuity

Because the Blackboard is persistent, work can continue across:

- LLM sessions
- Different LLM products
- Desktop and CLI environments
- Different machines
- Human and LLM sessions

Example:

```text
Claude
   │
   ▼
Blackboard
   │
   ├── Plan
   ├── State
   ├── Memory
   └── Event
   │
   ▼
Other LLM
```

A new LLM can determine the current work without the previous conversation history, as long as the relevant information has been recorded in the Blackboard.

---

# 17. External Tools

External tools are kept outside the core collaboration model.

Later, Blackboard participants can be connected to tools such as the following through MCP or other adapters.

```text
Browser
Blender
Unity
GitHub
OS Puppeteer
Mesen
SysML tools
etc.
```

The architecture is as follows.

```text
                LLM Collaboration
                      │
                 Blackboard
                      │
                     MCP
                      │
              External adapters
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
       Browser      Blender      GitHub
```

The collaboration model remains independent of external tool implementations.

---

# 18. Security and Permissions

The core specification does not define a complete security model.

However, implementations should assume the following:

- Not every LLM needs write access to every Blackboard document
- Roles can be mapped to permissions
- External tools may require separate authorization
- Destructive operations should not be implicitly permitted

A permission system can be added without changing the Blackboard data model.

---

# 19. Files and Data Format

Markdown is the standard human-readable representation.

YAML Front Matter can contain machine-readable metadata.

Example:

```markdown
---
id: task_003
type: task
role: implementer
status: in_progress
started_by: llm-c
started: 2026-08-10T10:30:00+09:00
---

# Implementation

Implement the API error handling.

## Notes

...
```

Implementations may use indexes or caches for performance, but the Markdown representation must remain recoverable and understandable.

---

# 20. MVP

The MVP is intended to demonstrate the basic collaboration model, not to provide a complete orchestration platform.

## 20.1 MVP Scope

The MVP shall support:

1. Markdown Blackboard
2. Plan checklist
3. Multiple predefined LLM Roles
4. MCP access
5. Reading the Plan
6. Selecting executable tasks
7. Atomic task claiming
8. Updating task status
9. Recording start and completion information
10. Basic State
11. Basic Memory
12. Basic Event logging

## 20.2 MVP Scenario

The minimal demo is as follows.

```text
LLM A = Researcher
LLM B = Implementer
LLM C = Reviewer

Plan:
  1. Research
  2. Implement
  3. Review
```

Execution:

```text
LLM A
  ├─ claims Research
  ├─ performs research
  ├─ writes Memory
  ├─ marks Research done
  └─ emits task_completed

LLM B
  ├─ sees Implementation is now executable
  ├─ claims Implementation
  ├─ performs implementation
  ├─ updates State
  └─ marks Implementation done

LLM C
  ├─ sees Review is executable
  ├─ claims Review
  ├─ performs review
  └─ marks Review done
```

A central LLM orchestrator is unnecessary.

## 20.3 MVP Acceptance Criteria

The MVP is considered successful when:

- Two or more independent LLM processes can share one Blackboard
- Each LLM can identify tasks matching its Role
- Two LLMs cannot successfully claim the same task
- Completing a task makes dependent tasks executable
- Different LLMs can claim parallel tasks
- Memory written by one LLM can be read by another
- State persists after an LLM session restarts
- Events provide an audit record of important operations
- Humans can inspect and modify the Markdown Blackboard

---

# 21. Full Implementation

A full implementation after the MVP should preserve the same conceptual model.

Possible additions include:

- Richer Plan dependency representations
- Task priorities
- Deadlines and timeouts
- Scheduled tasks
- Richer Event filtering
- Event subscriptions
- Permission management
- Blackboard indexes
- RAG integration
- Graph-based Memory
- Audit history
- Recovery and rollback
- External MCP adapters
- Multiple Blackboard instances
- Project and workspace separation

These are extensions, not prerequisites of the core model.

---

# 22. Implementation Phases

## Phase 1 — Blackboard Core

Implement:

- Markdown file storage
- Memory CRUD
- Plan CRUD
- State CRUD
- Event creation and reading
- YAML Front Matter parsing

## Phase 2 — MCP Server

Expose:

- `read_memory`
- `write_memory`
- `read_plan`
- `claim_task`
- `update_task`
- `read_state`
- `write_state`
- `read_event`
- `emit_event`

## Phase 3 — Task Claiming

Implement atomic claiming and revision/conflict detection.

## Phase 4 — Multi-LLM Test

Run at least two independent LLM clients with different Roles against the same Blackboard.

## Phase 5 — Parallelism

Demonstrate that independent tasks run in parallel and dependent tasks wait for required predecessor tasks to complete.

## Phase 6 — Full Implementation

Add advanced features without changing the core Blackboard model.

---

# 23. Out of Scope

The following are explicitly out of scope for version 1.0:

- Building a general-purpose LLM agent framework
- Replacing MCP
- Creating a centralized super-agent
- Dynamically routing every task to a central LLM
- Building a general-purpose workflow engine
- Making a database mandatory
- Depending on a specific LLM vendor
- Depending on a specific RAG implementation
- Integrating all external tools from the beginning

---

# 24. Core Concepts

The overall model can be summarized as follows.

```text
                  LLM Collaboration Blackboard

    Memory       Plan        State        Event
      │            │           │            │
      └────────────┴───────────┴────────────┘
                         │
                     Blackboard
                         │
                        MCP
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
      LLM A            LLM B            LLM C
    Researcher       Implementer       Reviewer
        │                │                │
        └────────────────┼────────────────┘
                         │
                  shared progress
```

The basic principle is:

> LLMs do not need to be orchestrated by another LLM. They can collaborate by observing and updating a shared Blackboard according to their predefined Roles.

The Plan provides a shared work timeline, State represents the current situation, Memory provides knowledge, and Events record what has happened.

MCP provides a common means of access.

This is the minimum system required to enable LLM collaboration.
