# FAITH Epic — Dependency Graph & Implementation Schedule

**Generated from:** `epic.yaml`
**Date:** 2026-05-26

---

## Task Registry

| Task ID | Task Name | Phase | Status | Dependencies | Complexity | Model |
| --------- | ----------- | ------- | -------- | -------------- | ------------ | ------- |
| FAITH-001 | Project Directory Structure & Base Scaffolding | 1 (Foundation) | DONE | None | S | Haiku / GPT-5.4-mini |
| FAITH-002 | Redis Container Setup | 1 (Foundation) | DONE | FAITH-001 | S | Haiku / GPT-5.4-mini |
| FAITH-003 | Configuration System: YAML Loading & Pydantic Models | 1 (Foundation) | DONE | FAITH-001 | M | Sonnet / GPT-5.4 |
| FAITH-004 | Config Hot-Reload Watcher | 1 (Foundation) | DONE | FAITH-003, FAITH-002 | M | Sonnet / GPT-5.4 |
| FAITH-005 | FAITH CLI (`faith-cli` Package) | 1 (Foundation) | DONE | FAITH-001, FAITH-002 | M | Sonnet / GPT-5.4 |
| FAITH-006 | Config Migration System | 1 (Foundation) | DONE | FAITH-003 | S | Haiku / GPT-5.4-mini |
| FAITH-007 | Compact Protocol Data Models & Serialisation | 2 (Protocol & Events) | DONE | FAITH-002 | M | Sonnet / GPT-5.4 |
| FAITH-008 | Event System Data Models & Publisher | 2 (Protocol & Events) | DONE | FAITH-002 | M | Sonnet / GPT-5.4 |
| FAITH-009 | Event Subscriber & Dispatcher | 2 (Protocol & Events) | DONE | FAITH-008 | M | Sonnet / GPT-5.4 |
| FAITH-010 | Base Agent Class | 3 (Agent Runtime) | DONE | FAITH-007, FAITH-008 | L | Opus / GPT-5.4 high reasoning |
| FAITH-011 | Rolling Context Summary & Compaction | 3 (Agent Runtime) | DONE | FAITH-010 | M | Sonnet / GPT-5.4 |
| FAITH-012 | MCP Adapter Layer | 3 (Agent Runtime) | DONE | FAITH-010 | L | Sonnet / GPT-5.4 |
| FAITH-013 | LLM API Client (Ollama + OpenRouter) | 3 (Agent Runtime) | DONE | FAITH-010 | M | Sonnet / GPT-5.4 |
| FAITH-014 | PA Container Setup & Docker SDK Integration | 4 (PA Core) | DONE | FAITH-001, FAITH-002, FAITH-010 | M | Opus / GPT-5.4 high reasoning |
| FAITH-015 | PA Session & Task Management | 4 (PA Core) | DONE | FAITH-014, FAITH-057, FAITH-009 | L | Opus / GPT-5.4 high reasoning |
| FAITH-016 | PA Event Dispatcher & Intervention Logic | 4 (PA Core) | DONE | FAITH-015, FAITH-009 | L | Opus / GPT-5.4 high reasoning |
| FAITH-017 | Loop Detection | 4 (PA Core) | DONE | FAITH-016 | M | Sonnet / GPT-5.4 |
| FAITH-018 | Living FRS Management | 4 (PA Core) | DONE | FAITH-015 | M | Opus / GPT-5.4 high reasoning |
| FAITH-019 | Security YAML Schema & Regex Approval Engine | 5 (Security) | DONE | FAITH-003 | M | Opus / GPT-5.4 high reasoning |
| FAITH-020 | Approval Request/Response Flow | 5 (Security) | DONE | FAITH-019, FAITH-008 | M | Opus / GPT-5.4 high reasoning |
| FAITH-021 | Audit Log System | 5 (Security) | DONE | FAITH-008 | M | Sonnet / GPT-5.4 |
| FAITH-022 | Filesystem MCP Server | 6 (Tool Servers) | DONE | FAITH-003, FAITH-008, FAITH-057 | L | Opus / GPT-5.4 high reasoning |
| FAITH-023 | Filesystem File History | 6 (Tool Servers) | DONE | FAITH-022 | M | Sonnet / GPT-5.4 |
| FAITH-024 | Python Execution MCP Server | 6 (Tool Servers) | DONE | FAITH-003, FAITH-008, FAITH-057 | M | Opus / GPT-5.4 high reasoning |
| FAITH-025 | PostgreSQL Database MCP Server | 6 (Tool Servers) | DONE | FAITH-003, FAITH-008 | M | Sonnet / GPT-5.4 |
| FAITH-026 | Browser Automation MCP Server (Playwright) | 6 (Tool Servers) | DONE | FAITH-003, FAITH-008, FAITH-035 | M | Sonnet / GPT-5.4 |
| FAITH-027 | Code Index MCP Server (tree-sitter) | 6 (Tool Servers) | DONE | FAITH-022 | L | Opus / GPT-5.4 high reasoning |
| FAITH-028 | RAG / ChromaDB MCP Server | 6 (Tool Servers) | DONE | FAITH-002, FAITH-022 | L | Sonnet / GPT-5.4 |
| FAITH-029 | Git MCP Server | 6 (Tool Servers) | DONE | FAITH-019 | M | Sonnet / GPT-5.4 |
| FAITH-030 | Pricing MCP Server | 6 (Tool Servers) | DONE | FAITH-026, FAITH-008 | M | Sonnet / GPT-5.4 |
| FAITH-031 | Web Search MCP Server | 6 (Tool Servers) | DONE | FAITH-003, FAITH-035 | M | Sonnet / GPT-5.4 |
| FAITH-032 | Full-Text Search MCP Server | 6 (Tool Servers) | DONE | FAITH-022 | S | Haiku / GPT-5.4-mini |
| FAITH-033 | Key-Value Store MCP Server | 6 (Tool Servers) | DONE | FAITH-002 | S | Haiku / GPT-5.4-mini |
| FAITH-034 | CAG Implementation | 7 (CAG & External MCP) | DONE | FAITH-010, FAITH-022 | M | Sonnet / GPT-5.4 |
| FAITH-035 | External MCP Server Registration & Lifecycle | 7 (CAG & External MCP) | DONE | FAITH-014, FAITH-003 | M | Opus / GPT-5.4 high reasoning |
| FAITH-036 | FastAPI Server Setup & WebSocket Endpoints | 8 (Web UI) | DONE | FAITH-002, FAITH-008 | M | Sonnet / GPT-5.4 |
| FAITH-038 | Agent Panel Component (Rich Transcript + React) | 8 (Web UI) | DONE | FAITH-074, FAITH-078 | M | Sonnet / GPT-5.4 |
| FAITH-039 | Approval Panel Component | 8 (Web UI) | DONE | FAITH-020, FAITH-074 | M | Sonnet / GPT-5.4 |
| FAITH-040 | System Status Panel & Health Summary | 8 (Web UI) | DONE | FAITH-036, FAITH-074 | S | Haiku / GPT-5.4-mini |
| FAITH-041 | Input Panel & File Upload | 8 (Web UI) | DONE | FAITH-074, FAITH-078 | S | Haiku / GPT-5.4-mini |
| FAITH-042 | Shared Web UI Theme System | 8 (Web UI) | DONE | FAITH-074, FAITH-077 | S | Haiku / GPT-5.4-mini |
| FAITH-043 | Project Switcher UI | 8 (Web UI) | DONE | FAITH-015, FAITH-074 | S | Haiku / GPT-5.4-mini |
| FAITH-044 | Web UI Log Views | 8 (Web UI) | DONE | FAITH-021, FAITH-074 | M | Opus / GPT-5.4 high reasoning |
| FAITH-045 | Event Log Writer | 9 (Logging) | DONE | FAITH-009 | S | Haiku / GPT-5.4-mini |
| FAITH-046 | Session & Task Log Writer | 9 (Logging) | DONE | FAITH-015 | M | Sonnet / GPT-5.4 |
| FAITH-047 | Token & Cost Log | 9 (Logging) | DONE | FAITH-013, FAITH-030 | S | Haiku / GPT-5.4-mini |
| FAITH-048 | Log Retention & Rotation | 9 (Logging) | DONE | FAITH-021, FAITH-045, FAITH-047 | S | Haiku / GPT-5.4-mini |
| FAITH-049 | First-Run Wizard: Multi-Step UI | 10 (First Run) | IN PROGRESS | FAITH-036, FAITH-003, FAITH-014, FAITH-057 | L | Opus / GPT-5.4 high reasoning |
| FAITH-050 | Privacy Profile Enforcement & Provider Knowledge Base | 10 (First Run) | TODO | FAITH-049, FAITH-057, FAITH-003 | M | Sonnet / GPT-5.4 |
| FAITH-051 | Ollama Model Download Integration | 10 (First Run) | DONE | FAITH-049, FAITH-057 | S | Sonnet / GPT-5.4 |
| FAITH-052 | Cloud Deployment Architecture | 12 (Cloud) | TODO | FAITH-001, FAITH-002, FAITH-003, FAITH-004, FAITH-005, FAITH-006, FAITH-007, FAITH-008, FAITH-009, FAITH-010, FAITH-011, FAITH-012, FAITH-013, FAITH-014, FAITH-015, FAITH-016, FAITH-017, FAITH-018, FAITH-019, FAITH-020, FAITH-021, FAITH-022, FAITH-023, FAITH-024, FAITH-025, FAITH-026, FAITH-027, FAITH-028, FAITH-029, FAITH-030, FAITH-031, FAITH-032, FAITH-033, FAITH-034, FAITH-035, FAITH-036, FAITH-038, FAITH-039, FAITH-040, FAITH-041, FAITH-042, FAITH-043, FAITH-044, FAITH-045, FAITH-046, FAITH-047, FAITH-048, FAITH-049, FAITH-050, FAITH-051, FAITH-053, FAITH-054, FAITH-055, FAITH-056, FAITH-057, FAITH-058, FAITH-059, FAITH-062, FAITH-063, FAITH-064, FAITH-065, FAITH-066, FAITH-067, FAITH-068, FAITH-069, FAITH-070, FAITH-071, FAITH-072, FAITH-073, FAITH-074, FAITH-075, FAITH-076, FAITH-077, FAITH-078, FAITH-079, FAITH-080, FAITH-081, FAITH-082, FAITH-083, FAITH-084, FAITH-085, FAITH-086, FAITH-087, FAITH-088, FAITH-089, FAITH-090, FAITH-091, FAITH-092, FAITH-093, FAITH-094, FAITH-095, FAITH-096, FAITH-097, FAITH-098, FAITH-099, FAITH-100, FAITH-101, FAITH-102, FAITH-103, FAITH-104, FAITH-105, FAITH-106, FAITH-107, FAITH-108, FAITH-109, FAITH-110, FAITH-111, FAITH-112, FAITH-113, FAITH-114, FAITH-115, FAITH-116, FAITH-117, FAITH-118, FAITH-119, FAITH-120, FAITH-121, FAITH-122, FAITH-123, FAITH-124, FAITH-125, FAITH-126, FAITH-127, FAITH-128 | XL | Opus / GPT-5.4 high reasoning |
| FAITH-053 | First-Run Wizard: Detailed Specification | 10 (First Run) | TODO | FAITH-049, FAITH-057 | M | Sonnet / GPT-5.4 |
| FAITH-054 | `faith run` Command & Task API | 11 (CLI & Skills) | TODO | FAITH-005, FAITH-036, FAITH-015 | M | Sonnet / GPT-5.4 |
| FAITH-055 | Skill Definitions & Unattended Execution | 11 (CLI & Skills) | TODO | FAITH-054, FAITH-019 | M | Opus / GPT-5.4 high reasoning |
| FAITH-056 | Built-in Skill Scheduler | 11 (CLI & Skills) | TODO | FAITH-055, FAITH-004 | M | Opus / GPT-5.4 high reasoning |
| FAITH-057 | Disposable Sandbox Lifecycle & Scheduling | 4 (PA Core) | DONE | FAITH-014 | L | Opus / GPT-5.4 high reasoning |
| FAITH-058 | Docker Runtime & Image Panel | 8 (Web UI) | DONE | FAITH-014, FAITH-036, FAITH-074, FAITH-078 | M | Sonnet / GPT-5.4 |
| FAITH-059 | Service Route Discovery & `faith show-urls` | 11 (CLI & Skills) | DONE | FAITH-005, FAITH-036 | S | Sonnet / GPT-5.4 |
| FAITH-062 | Panel Lifecycle & Deduping | 8 (Web UI) | DONE | FAITH-074, FAITH-075 | S | Haiku / GPT-5.4-mini |
| FAITH-063 | Snap-Grid Panel Layout Refinement | 13 (Web UI Workspace Migration) | DONE | FAITH-062, FAITH-074, FAITH-075 | M | Sonnet / GPT-5.4 |
| FAITH-064 | Panel Title-Bar Actions | 8 (Web UI) | DONE | FAITH-074, FAITH-062 | S | Haiku / GPT-5.4-mini |
| FAITH-065 | Docker Daemon Not Running Guidance | 10 (First Run) | TODO | FAITH-005 | S | Haiku / GPT-5.4-mini |
| FAITH-066 | Project `cag/` Auto-Loading & Budget Guidance | 7 (CAG & External MCP) | DONE | FAITH-034, FAITH-022 | M | Sonnet / GPT-5.4 |
| FAITH-067 | Ollama Management MCP Server | 10 (First Run) | DONE | FAITH-004, FAITH-013, FAITH-019, FAITH-051 | M | Sonnet / GPT-5.4 |
| FAITH-068 | PA Chat MCP Tool-Calling Loop | 7 (CAG & External MCP) | DONE | FAITH-012, FAITH-016, FAITH-022, FAITH-036, FAITH-038, FAITH-081 | M | Sonnet / GPT-5.4 |
| FAITH-069 | PA MCP Inventory Grounding | 7 (CAG & External MCP) | DONE | FAITH-068, FAITH-081 | S | Haiku / GPT-5.4-mini |
| FAITH-070 | Theme-Aware Chat Transcript Bubbles | 8 (Web UI) | DONE | FAITH-038, FAITH-041, FAITH-064, FAITH-069 | M | Sonnet / GPT-5.4 |
| FAITH-071 | PA System Prompt Editor Panel | 8 (Web UI) | DONE | FAITH-036, FAITH-038, FAITH-074, FAITH-078 | M | Sonnet / GPT-5.4 |
| FAITH-072 | PA Transcript Scroll Containment | 8 (Web UI) | DONE | FAITH-038, FAITH-070 | S | Haiku / GPT-5.4-mini |
| FAITH-073 | Agent Runtime Date Time Prompt Injection | 8 (Web UI) | DONE | FAITH-010, FAITH-038, FAITH-071 | S | Haiku / GPT-5.4-mini |
| FAITH-074 | React + Dockview Workspace Shell Migration | 13 (Web UI Workspace Migration) | DONE | FAITH-036, FAITH-078 | L | Sonnet / GPT-5.4 |
| FAITH-075 | Dockview Default Layout & Panel Constraints | 13 (Web UI Workspace Migration) | DONE | FAITH-074, FAITH-084 | M | Sonnet / GPT-5.4 |
| FAITH-076 | Minimized Panel Tray for Dockview | 13 (Web UI Workspace Migration) | DONE | FAITH-074 | M | Sonnet / GPT-5.4 |
| FAITH-077 | Radix UI Menubar & Context Menu Integration | 13 (Web UI Workspace Migration) | DONE | FAITH-074 | M | Sonnet / GPT-5.4 |
| FAITH-078 | Frontend Build Pipeline & Bundled Asset Integration | 13 (Web UI Workspace Migration) | DONE | FAITH-036 | M | Sonnet / GPT-5.4 |
| FAITH-079 | Runtime Badge & Container Status Sync | 8 (Web UI) | DONE | FAITH-038, FAITH-040, FAITH-058, FAITH-074 | S | Sonnet / GPT-5.4 |
| FAITH-080 | Speech-to-Text Dictation Input | 8 (Web UI) | DONE | FAITH-041, FAITH-074, FAITH-078 | M | Sonnet / GPT-5.4 |
| FAITH-081 | Canonical MCP Registry & Agent Tool Manifest Propagation | 7 (CAG & External MCP) | DONE | FAITH-012, FAITH-014, FAITH-035 | L | Opus / GPT-5.4 high reasoning |
| FAITH-082 | Project Agent Transcript Rehydration on Restart | 8 (Web UI) | DONE | FAITH-015, FAITH-038, FAITH-046, FAITH-074 | S | Sonnet / GPT-5.4 |
| FAITH-083 | User Timezone Preference Resolution & Persistence | 10 (First Run) | IN PROGRESS | FAITH-003, FAITH-049, FAITH-073 | S | Sonnet / GPT-5.4 |
| FAITH-084 | User Settings Window & Profile Preferences | 8 (Web UI) | DONE | FAITH-003, FAITH-004, FAITH-049, FAITH-074, FAITH-078, FAITH-083 | M | Sonnet / GPT-5.4 |
| FAITH-085 | Input Panel Enter-to-Send & Newline Hint | 8 (Web UI) | DONE | FAITH-041, FAITH-074, FAITH-078 | S | Haiku / GPT-5.4-mini |
| FAITH-086 | Host-Backed Web UI Saved State Persistence | 8 (Web UI) | DONE | FAITH-015, FAITH-071, FAITH-084 | S | Sonnet / GPT-5.4 |
| FAITH-087 | Locale & Timezone Fixed-Option Selectors | 8 (Web UI) | DONE | FAITH-083, FAITH-084 | S | Sonnet / GPT-5.4 |
| FAITH-088 | Runtime Specialist-Agent Materialisation & Lifecycle | 14 (Specialist-Agent Delegation from PA Chat) | TODO | FAITH-014, FAITH-015, FAITH-049 | L | Opus / GPT-5.4 high reasoning |
| FAITH-089 | PA Chat Specialist Delegation Loop | 14 (Specialist-Agent Delegation from PA Chat) | TODO | FAITH-015, FAITH-016, FAITH-068, FAITH-088 | L | Opus / GPT-5.4 high reasoning |
| FAITH-090 | Delegated Specialist Result Relay & Persistence | 14 (Specialist-Agent Delegation from PA Chat) | TODO | FAITH-046, FAITH-082, FAITH-089 | M | Sonnet / GPT-5.4 |
| FAITH-091 | Canonical Specialist-Agent Team Manifest & Delegation Grounding | 14 (Specialist-Agent Delegation from PA Chat) | TODO | FAITH-015, FAITH-081, FAITH-088 | M | Sonnet / GPT-5.4 |
| FAITH-092 | Containerised Avatar Runtime & Service Contract | 15 (Optional Voice & Avatar Experience) | TODO | FAITH-001, FAITH-005, FAITH-036, FAITH-095 | L | Opus / GPT-5.4 high reasoning |
| FAITH-093 | Avatar Panel, Speech Playback, and Voice Chat Integration | 15 (Optional Voice & Avatar Experience) | TODO | FAITH-080, FAITH-084, FAITH-092 | L | Opus / GPT-5.4 high reasoning |
| FAITH-094 | Avatar Runtime Install, Removal, and Preference Management | 15 (Optional Voice & Avatar Experience) | TODO | FAITH-049, FAITH-084, FAITH-092, FAITH-095 | M | Sonnet / GPT-5.4 |
| FAITH-095 | Optional Text-to-Speech Runtime & Spoken Reply Integration | 15 (Optional Voice & Avatar Experience) | TODO | FAITH-036, FAITH-084 | M | Sonnet / GPT-5.4 |
| FAITH-096 | Deterministic User-Requested Tool Selection in PA Chat | 8 (Web UI) | DONE | FAITH-068, FAITH-069, FAITH-081 | M | Sonnet / GPT-5.4 |
| FAITH-097 | Project-Workspace Absolute Path Normalisation for Chat Tool Calls | 8 (Web UI) | DONE | FAITH-022, FAITH-068 | S | Sonnet / GPT-5.4 |
| FAITH-098 | PA Chat Tool Call Audit & Session Visibility | 8 (Web UI) | DONE | FAITH-021, FAITH-044, FAITH-046, FAITH-068 | M | Sonnet / GPT-5.4 |
| FAITH-099 | Session History Live Session Creation & Default Placement | 8 (Web UI) | DONE | FAITH-015, FAITH-044, FAITH-074, FAITH-082 | M | Sonnet / GPT-5.4 |
| FAITH-100 | PA Project-Root AGENTS.md Instruction Source | 16 (Project Instruction Context & Model Intelligence) | DONE | FAITH-071, FAITH-073, FAITH-086 | M | Sonnet / GPT-5.4 |
| FAITH-101 | AGENTS.md Include Resolution & Reference Normalisation | 16 (Project Instruction Context & Model Intelligence) | DONE | FAITH-100, FAITH-022 | L | Opus / GPT-5.4 high reasoning |
| FAITH-102 | Effective PA Context Compiler, Hash Cache, and Persistence | 16 (Project Instruction Context & Model Intelligence) | DONE | FAITH-100, FAITH-101, FAITH-082, FAITH-086 | L | Opus / GPT-5.4 high reasoning |
| FAITH-103 | Effective Context Debug Panel & Redacted Snapshot Inspection | 16 (Project Instruction Context & Model Intelligence) | DONE | FAITH-044, FAITH-084, FAITH-102 | M | Sonnet / GPT-5.4 |
| FAITH-104 | Model Catalog, Context Metadata, and Manual Override Management | 16 (Project Instruction Context & Model Intelligence) | DONE | FAITH-067, FAITH-084 | L | Opus / GPT-5.4 high reasoning |
| FAITH-105 | Token Panel Context Diagnostics & Per-File Attribution | 16 (Project Instruction Context & Model Intelligence) | DONE | FAITH-047, FAITH-103, FAITH-104 | M | Sonnet / GPT-5.4 |
| FAITH-106 | Context-Fit Warnings, VRAM Heuristics, and Early Compaction Guidance | 16 (Project Instruction Context & Model Intelligence) | DONE | FAITH-013, FAITH-104, FAITH-105 | M | Sonnet / GPT-5.4 |
| FAITH-107 | Automatic OpenRouter Prompt-Caching Optimisation | 16 (Project Instruction Context & Model Intelligence) | DONE | FAITH-013, FAITH-102, FAITH-104, FAITH-105 | M | Sonnet / GPT-5.4 |
| FAITH-108 | Registry-Driven Tools Menu & Manage Tools Panel | 17 (Managed MCP Tool Acquisition & Governance) | TODO | FAITH-044, FAITH-081, FAITH-084, FAITH-074 | M | Sonnet / GPT-5.4 |
| FAITH-109 | GitHub and ZIP MCP Tool Acquisition Review Flow | 17 (Managed MCP Tool Acquisition & Governance) | TODO | FAITH-035, FAITH-108 | L | Opus / GPT-5.4 high reasoning |
| FAITH-110 | Managed Tools Directory, Trust Badges, Update Notifications, and Rollback Retention | 17 (Managed MCP Tool Acquisition & Governance) | TODO | FAITH-109 | M | Sonnet / GPT-5.4 |
| FAITH-111 | Per-Function Tool Permissions, Health States, and Local Failure Classification | 17 (Managed MCP Tool Acquisition & Governance) | TODO | FAITH-109, FAITH-110 | L | Opus / GPT-5.4 high reasoning |
| FAITH-112 | Dynamic Tool Lifecycle Activation on Next Inference Turn | 17 (Managed MCP Tool Acquisition & Governance) | TODO | FAITH-081, FAITH-108, FAITH-111 | M | Sonnet / GPT-5.4 |
| FAITH-113 | Active Context Usage Tracking & Compaction Thresholds | 18 (Runtime Context Compaction & Rule Promotion) | DONE | FAITH-013, FAITH-102, FAITH-104, FAITH-105 | M | Sonnet / GPT-5.4 |
| FAITH-114 | Deterministic Retention Rules & Durable History Preservation for Compaction | 18 (Runtime Context Compaction & Rule Promotion) | DONE | FAITH-046, FAITH-082, FAITH-113 | M | Sonnet / GPT-5.4 |
| FAITH-115 | Local-Ollama History Compaction Summariser | 18 (Runtime Context Compaction & Rule Promotion) | DONE | FAITH-113, FAITH-114 | L | Opus / GPT-5.4 high reasoning |
| FAITH-116 | Hard Compaction UX Blocking, Buffering Indicator, and Diagnostics | 18 (Runtime Context Compaction & Rule Promotion) | DONE | FAITH-085, FAITH-103, FAITH-113, FAITH-115 | M | Sonnet / GPT-5.4 |
| FAITH-117 | Explicit Durable Rule Promotion from Inference to AGENTS.md | 18 (Runtime Context Compaction & Rule Promotion) | DONE | FAITH-100, FAITH-102, FAITH-098 | M | Sonnet / GPT-5.4 |
| FAITH-118 | Filetype Resolver Framework for Deterministic Excerpt Boundaries | 19 (Scoped File Storage & Deterministic Excerpt Retrieval) | TODO | FAITH-027, FAITH-032 | L | Opus / GPT-5.4 high reasoning |
| FAITH-119 | Excerpt Discovery Summary MCP Function | 19 (Scoped File Storage & Deterministic Excerpt Retrieval) | TODO | FAITH-118 | M | Sonnet / GPT-5.4 |
| FAITH-120 | Excerpt Retrieval MCP Function for Multi-Format Files | 19 (Scoped File Storage & Deterministic Excerpt Retrieval) | TODO | FAITH-118, FAITH-119 | L | Opus / GPT-5.4 high reasoning |
| FAITH-121 | Scoped Attachment Ingestion and Deduplicated Storage Lifecycle | 19 (Scoped File Storage & Deterministic Excerpt Retrieval) | TODO | FAITH-099, FAITH-118 | L | Opus / GPT-5.4 high reasoning |
| FAITH-122 | Storage Inventory, Trash, and Export Panels | 19 (Scoped File Storage & Deterministic Excerpt Retrieval) | TODO | FAITH-121, FAITH-084, FAITH-074 | L | Opus / GPT-5.4 high reasoning |
| FAITH-123 | Session Naming, Scoped File Access, and Session Export Controls | 19 (Scoped File Storage & Deterministic Excerpt Retrieval) | TODO | FAITH-099, FAITH-121, FAITH-122 | M | Sonnet / GPT-5.4 |
| FAITH-124 | Replay-Friendly MCP Tool Audit Artifacts | 19 (Scoped File Storage & Deterministic Excerpt Retrieval) | TODO | FAITH-021, FAITH-098, FAITH-120 | M | Sonnet / GPT-5.4 |
| FAITH-125 | Session Selector, Session Details Panel, and Effective Context UX Cleanup | 8 (Web UI) | DONE | FAITH-044, FAITH-099, FAITH-103, FAITH-084 | M | Sonnet / GPT-5.4 |
| FAITH-126 | User Terminal Panel in Active Agent Runtime Context | 20 (Interactive User Terminal & Runtime Console) | TODO | FAITH-038, FAITH-041, FAITH-057, FAITH-074, FAITH-098 | L | Opus / GPT-5.4 high reasoning |
| FAITH-127 | Project Agent Fenced Code Block Styling Polish | 21 (Transcript Rendering Polish) | DONE | FAITH-038 | S | Haiku / GPT-5.4-mini |
| FAITH-128 | Project Agent Responsive Bubble Width Behaviour | 22 (Responsive Transcript Bubble Layout) | DONE | FAITH-038, FAITH-127 | S | Haiku / GPT-5.4-mini |

---

## Mermaid Dependency Diagram

```mermaid
flowchart TD
    %% ── Phase 1: Foundation ──
    FAITH-001["FAITH-001<br/>Project Directory Structure & Base Scaffolding<br/>Phase 1 | DONE | S"]
    FAITH-002["FAITH-002<br/>Redis Container Setup<br/>Phase 1 | DONE | S"]
    FAITH-003["FAITH-003<br/>Configuration System: YAML Loading & Pydantic Models<br/>Phase 1 | DONE | M"]
    FAITH-004["FAITH-004<br/>Config Hot-Reload Watcher<br/>Phase 1 | DONE | M"]
    FAITH-005["FAITH-005<br/>FAITH CLI (`faith-cli` Package)<br/>Phase 1 | DONE | M"]
    FAITH-006["FAITH-006<br/>Config Migration System<br/>Phase 1 | DONE | S"]

    %% ── Phase 2: Protocol & Events ──
    FAITH-007["FAITH-007<br/>Compact Protocol Data Models & Serialisation<br/>Phase 2 | DONE | M"]
    FAITH-008["FAITH-008<br/>Event System Data Models & Publisher<br/>Phase 2 | DONE | M"]
    FAITH-009["FAITH-009<br/>Event Subscriber & Dispatcher<br/>Phase 2 | DONE | M"]

    %% ── Phase 3: Agent Runtime ──
    FAITH-010["FAITH-010<br/>Base Agent Class<br/>Phase 3 | DONE | L"]
    FAITH-011["FAITH-011<br/>Rolling Context Summary & Compaction<br/>Phase 3 | DONE | M"]
    FAITH-012["FAITH-012<br/>MCP Adapter Layer<br/>Phase 3 | DONE | L"]
    FAITH-013["FAITH-013<br/>LLM API Client (Ollama + OpenRouter)<br/>Phase 3 | DONE | M"]

    %% ── Phase 4: PA Core ──
    FAITH-014["FAITH-014<br/>PA Container Setup & Docker SDK Integration<br/>Phase 4 | DONE | M"]
    FAITH-015["FAITH-015<br/>PA Session & Task Management<br/>Phase 4 | DONE | L"]
    FAITH-016["FAITH-016<br/>PA Event Dispatcher & Intervention Logic<br/>Phase 4 | DONE | L"]
    FAITH-017["FAITH-017<br/>Loop Detection<br/>Phase 4 | DONE | M"]
    FAITH-018["FAITH-018<br/>Living FRS Management<br/>Phase 4 | DONE | M"]
    FAITH-057["FAITH-057<br/>Disposable Sandbox Lifecycle & Scheduling<br/>Phase 4 | DONE | L"]

    %% ── Phase 5: Security ──
    FAITH-019["FAITH-019<br/>Security YAML Schema & Regex Approval Engine<br/>Phase 5 | DONE | M"]
    FAITH-020["FAITH-020<br/>Approval Request/Response Flow<br/>Phase 5 | DONE | M"]
    FAITH-021["FAITH-021<br/>Audit Log System<br/>Phase 5 | DONE | M"]

    %% ── Phase 6: Tool Servers ──
    FAITH-022["FAITH-022<br/>Filesystem MCP Server<br/>Phase 6 | DONE | L"]
    FAITH-023["FAITH-023<br/>Filesystem File History<br/>Phase 6 | DONE | M"]
    FAITH-024["FAITH-024<br/>Python Execution MCP Server<br/>Phase 6 | DONE | M"]
    FAITH-025["FAITH-025<br/>PostgreSQL Database MCP Server<br/>Phase 6 | DONE | M"]
    FAITH-026["FAITH-026<br/>Browser Automation MCP Server (Playwright)<br/>Phase 6 | DONE | M"]
    FAITH-027["FAITH-027<br/>Code Index MCP Server (tree-sitter)<br/>Phase 6 | DONE | L"]
    FAITH-028["FAITH-028<br/>RAG / ChromaDB MCP Server<br/>Phase 6 | DONE | L"]
    FAITH-029["FAITH-029<br/>Git MCP Server<br/>Phase 6 | DONE | M"]
    FAITH-030["FAITH-030<br/>Pricing MCP Server<br/>Phase 6 | DONE | M"]
    FAITH-031["FAITH-031<br/>Web Search MCP Server<br/>Phase 6 | DONE | M"]
    FAITH-032["FAITH-032<br/>Full-Text Search MCP Server<br/>Phase 6 | DONE | S"]
    FAITH-033["FAITH-033<br/>Key-Value Store MCP Server<br/>Phase 6 | DONE | S"]

    %% ── Phase 7: CAG & External MCP ──
    FAITH-034["FAITH-034<br/>CAG Implementation<br/>Phase 7 | DONE | M"]
    FAITH-035["FAITH-035<br/>External MCP Server Registration & Lifecycle<br/>Phase 7 | DONE | M"]
    FAITH-066["FAITH-066<br/>Project `cag/` Auto-Loading & Budget Guidance<br/>Phase 7 | DONE | M"]
    FAITH-068["FAITH-068<br/>PA Chat MCP Tool-Calling Loop<br/>Phase 7 | DONE | M"]
    FAITH-069["FAITH-069<br/>PA MCP Inventory Grounding<br/>Phase 7 | DONE | S"]
    FAITH-081["FAITH-081<br/>Canonical MCP Registry & Agent Tool Manifest Propagation<br/>Phase 7 | DONE | L"]

    %% ── Phase 8: Web UI ──
    FAITH-036["FAITH-036<br/>FastAPI Server Setup & WebSocket Endpoints<br/>Phase 8 | DONE | M"]
    FAITH-038["FAITH-038<br/>Agent Panel Component (Rich Transcript + React)<br/>Phase 8 | DONE | M"]
    FAITH-039["FAITH-039<br/>Approval Panel Component<br/>Phase 8 | DONE | M"]
    FAITH-040["FAITH-040<br/>System Status Panel & Health Summary<br/>Phase 8 | DONE | S"]
    FAITH-041["FAITH-041<br/>Input Panel & File Upload<br/>Phase 8 | DONE | S"]
    FAITH-042["FAITH-042<br/>Shared Web UI Theme System<br/>Phase 8 | DONE | S"]
    FAITH-043["FAITH-043<br/>Project Switcher UI<br/>Phase 8 | DONE | S"]
    FAITH-044["FAITH-044<br/>Web UI Log Views<br/>Phase 8 | DONE | M"]
    FAITH-058["FAITH-058<br/>Docker Runtime & Image Panel<br/>Phase 8 | DONE | M"]
    FAITH-062["FAITH-062<br/>Panel Lifecycle & Deduping<br/>Phase 8 | DONE | S"]
    FAITH-064["FAITH-064<br/>Panel Title-Bar Actions<br/>Phase 8 | DONE | S"]
    FAITH-070["FAITH-070<br/>Theme-Aware Chat Transcript Bubbles<br/>Phase 8 | DONE | M"]
    FAITH-071["FAITH-071<br/>PA System Prompt Editor Panel<br/>Phase 8 | DONE | M"]
    FAITH-072["FAITH-072<br/>PA Transcript Scroll Containment<br/>Phase 8 | DONE | S"]
    FAITH-073["FAITH-073<br/>Agent Runtime Date Time Prompt Injection<br/>Phase 8 | DONE | S"]
    FAITH-079["FAITH-079<br/>Runtime Badge & Container Status Sync<br/>Phase 8 | DONE | S"]
    FAITH-080["FAITH-080<br/>Speech-to-Text Dictation Input<br/>Phase 8 | DONE | M"]
    FAITH-082["FAITH-082<br/>Project Agent Transcript Rehydration on Restart<br/>Phase 8 | DONE | S"]
    FAITH-084["FAITH-084<br/>User Settings Window & Profile Preferences<br/>Phase 8 | DONE | M"]
    FAITH-085["FAITH-085<br/>Input Panel Enter-to-Send & Newline Hint<br/>Phase 8 | DONE | S"]
    FAITH-086["FAITH-086<br/>Host-Backed Web UI Saved State Persistence<br/>Phase 8 | DONE | S"]
    FAITH-087["FAITH-087<br/>Locale & Timezone Fixed-Option Selectors<br/>Phase 8 | DONE | S"]
    FAITH-096["FAITH-096<br/>Deterministic User-Requested Tool Selection in PA Chat<br/>Phase 8 | DONE | M"]
    FAITH-097["FAITH-097<br/>Project-Workspace Absolute Path Normalisation for Chat Tool Calls<br/>Phase 8 | DONE | S"]
    FAITH-098["FAITH-098<br/>PA Chat Tool Call Audit & Session Visibility<br/>Phase 8 | DONE | M"]
    FAITH-099["FAITH-099<br/>Session History Live Session Creation & Default Placement<br/>Phase 8 | DONE | M"]
    FAITH-125["FAITH-125<br/>Session Selector, Session Details Panel, and Effective Context UX Cleanup<br/>Phase 8 | DONE | M"]

    %% ── Phase 9: Logging ──
    FAITH-045["FAITH-045<br/>Event Log Writer<br/>Phase 9 | DONE | S"]
    FAITH-046["FAITH-046<br/>Session & Task Log Writer<br/>Phase 9 | DONE | M"]
    FAITH-047["FAITH-047<br/>Token & Cost Log<br/>Phase 9 | DONE | S"]
    FAITH-048["FAITH-048<br/>Log Retention & Rotation<br/>Phase 9 | DONE | S"]

    %% ── Phase 10: First Run ──
    FAITH-049["FAITH-049<br/>First-Run Wizard: Multi-Step UI<br/>Phase 10 | IN PROGRESS | L"]
    FAITH-050["FAITH-050<br/>Privacy Profile Enforcement & Provider Knowledge Base<br/>Phase 10 | TODO | M"]
    FAITH-051["FAITH-051<br/>Ollama Model Download Integration<br/>Phase 10 | DONE | S"]
    FAITH-053["FAITH-053<br/>First-Run Wizard: Detailed Specification<br/>Phase 10 | TODO | M"]
    FAITH-065["FAITH-065<br/>Docker Daemon Not Running Guidance<br/>Phase 10 | TODO | S"]
    FAITH-067["FAITH-067<br/>Ollama Management MCP Server<br/>Phase 10 | DONE | M"]
    FAITH-083["FAITH-083<br/>User Timezone Preference Resolution & Persistence<br/>Phase 10 | IN PROGRESS | S"]

    %% ── Phase 11: CLI & Skills ──
    FAITH-054["FAITH-054<br/>`faith run` Command & Task API<br/>Phase 11 | TODO | M"]
    FAITH-055["FAITH-055<br/>Skill Definitions & Unattended Execution<br/>Phase 11 | TODO | M"]
    FAITH-056["FAITH-056<br/>Built-in Skill Scheduler<br/>Phase 11 | TODO | M"]
    FAITH-059["FAITH-059<br/>Service Route Discovery & `faith show-urls`<br/>Phase 11 | DONE | S"]

    %% ── Phase 12: Cloud ──
    FAITH-052["FAITH-052<br/>Cloud Deployment Architecture<br/>Phase 12 | TODO | XL"]

    %% ── Phase 13: Web UI Workspace Migration ──
    FAITH-063["FAITH-063<br/>Snap-Grid Panel Layout Refinement<br/>Phase 13 | DONE | M"]
    FAITH-074["FAITH-074<br/>React + Dockview Workspace Shell Migration<br/>Phase 13 | DONE | L"]
    FAITH-075["FAITH-075<br/>Dockview Default Layout & Panel Constraints<br/>Phase 13 | DONE | M"]
    FAITH-076["FAITH-076<br/>Minimized Panel Tray for Dockview<br/>Phase 13 | DONE | M"]
    FAITH-077["FAITH-077<br/>Radix UI Menubar & Context Menu Integration<br/>Phase 13 | DONE | M"]
    FAITH-078["FAITH-078<br/>Frontend Build Pipeline & Bundled Asset Integration<br/>Phase 13 | DONE | M"]

    %% ── Phase 14: Specialist-Agent Delegation from PA Chat ──
    FAITH-088["FAITH-088<br/>Runtime Specialist-Agent Materialisation & Lifecycle<br/>Phase 14 | TODO | L"]
    FAITH-089["FAITH-089<br/>PA Chat Specialist Delegation Loop<br/>Phase 14 | TODO | L"]
    FAITH-090["FAITH-090<br/>Delegated Specialist Result Relay & Persistence<br/>Phase 14 | TODO | M"]
    FAITH-091["FAITH-091<br/>Canonical Specialist-Agent Team Manifest & Delegation Grounding<br/>Phase 14 | TODO | M"]

    %% ── Phase 15: Optional Voice & Avatar Experience ──
    FAITH-092["FAITH-092<br/>Containerised Avatar Runtime & Service Contract<br/>Phase 15 | TODO | L"]
    FAITH-093["FAITH-093<br/>Avatar Panel, Speech Playback, and Voice Chat Integration<br/>Phase 15 | TODO | L"]
    FAITH-094["FAITH-094<br/>Avatar Runtime Install, Removal, and Preference Management<br/>Phase 15 | TODO | M"]
    FAITH-095["FAITH-095<br/>Optional Text-to-Speech Runtime & Spoken Reply Integration<br/>Phase 15 | TODO | M"]

    %% ── Phase 16: Project Instruction Context & Model Intelligence ──
    FAITH-100["FAITH-100<br/>PA Project-Root AGENTS.md Instruction Source<br/>Phase 16 | DONE | M"]
    FAITH-101["FAITH-101<br/>AGENTS.md Include Resolution & Reference Normalisation<br/>Phase 16 | DONE | L"]
    FAITH-102["FAITH-102<br/>Effective PA Context Compiler, Hash Cache, and Persistence<br/>Phase 16 | DONE | L"]
    FAITH-103["FAITH-103<br/>Effective Context Debug Panel & Redacted Snapshot Inspection<br/>Phase 16 | DONE | M"]
    FAITH-104["FAITH-104<br/>Model Catalog, Context Metadata, and Manual Override Management<br/>Phase 16 | DONE | L"]
    FAITH-105["FAITH-105<br/>Token Panel Context Diagnostics & Per-File Attribution<br/>Phase 16 | DONE | M"]
    FAITH-106["FAITH-106<br/>Context-Fit Warnings, VRAM Heuristics, and Early Compaction Guidance<br/>Phase 16 | DONE | M"]
    FAITH-107["FAITH-107<br/>Automatic OpenRouter Prompt-Caching Optimisation<br/>Phase 16 | DONE | M"]

    %% ── Phase 17: Managed MCP Tool Acquisition & Governance ──
    FAITH-108["FAITH-108<br/>Registry-Driven Tools Menu & Manage Tools Panel<br/>Phase 17 | TODO | M"]
    FAITH-109["FAITH-109<br/>GitHub and ZIP MCP Tool Acquisition Review Flow<br/>Phase 17 | TODO | L"]
    FAITH-110["FAITH-110<br/>Managed Tools Directory, Trust Badges, Update Notifications, and Rollback Retention<br/>Phase 17 | TODO | M"]
    FAITH-111["FAITH-111<br/>Per-Function Tool Permissions, Health States, and Local Failure Classification<br/>Phase 17 | TODO | L"]
    FAITH-112["FAITH-112<br/>Dynamic Tool Lifecycle Activation on Next Inference Turn<br/>Phase 17 | TODO | M"]

    %% ── Phase 18: Runtime Context Compaction & Rule Promotion ──
    FAITH-113["FAITH-113<br/>Active Context Usage Tracking & Compaction Thresholds<br/>Phase 18 | DONE | M"]
    FAITH-114["FAITH-114<br/>Deterministic Retention Rules & Durable History Preservation for Compaction<br/>Phase 18 | DONE | M"]
    FAITH-115["FAITH-115<br/>Local-Ollama History Compaction Summariser<br/>Phase 18 | DONE | L"]
    FAITH-116["FAITH-116<br/>Hard Compaction UX Blocking, Buffering Indicator, and Diagnostics<br/>Phase 18 | DONE | M"]
    FAITH-117["FAITH-117<br/>Explicit Durable Rule Promotion from Inference to AGENTS.md<br/>Phase 18 | DONE | M"]

    %% ── Phase 19: Scoped File Storage & Deterministic Excerpt Retrieval ──
    FAITH-118["FAITH-118<br/>Filetype Resolver Framework for Deterministic Excerpt Boundaries<br/>Phase 19 | TODO | L"]
    FAITH-119["FAITH-119<br/>Excerpt Discovery Summary MCP Function<br/>Phase 19 | TODO | M"]
    FAITH-120["FAITH-120<br/>Excerpt Retrieval MCP Function for Multi-Format Files<br/>Phase 19 | TODO | L"]
    FAITH-121["FAITH-121<br/>Scoped Attachment Ingestion and Deduplicated Storage Lifecycle<br/>Phase 19 | TODO | L"]
    FAITH-122["FAITH-122<br/>Storage Inventory, Trash, and Export Panels<br/>Phase 19 | TODO | L"]
    FAITH-123["FAITH-123<br/>Session Naming, Scoped File Access, and Session Export Controls<br/>Phase 19 | TODO | M"]
    FAITH-124["FAITH-124<br/>Replay-Friendly MCP Tool Audit Artifacts<br/>Phase 19 | TODO | M"]

    %% ── Phase 20: Interactive User Terminal & Runtime Console ──
    FAITH-126["FAITH-126<br/>User Terminal Panel in Active Agent Runtime Context<br/>Phase 20 | TODO | L"]

    %% ── Phase 21: Transcript Rendering Polish ──
    FAITH-127["FAITH-127<br/>Project Agent Fenced Code Block Styling Polish<br/>Phase 21 | DONE | S"]

    %% ── Phase 22: Responsive Transcript Bubble Layout ──
    FAITH-128["FAITH-128<br/>Project Agent Responsive Bubble Width Behaviour<br/>Phase 22 | DONE | S"]

    %% ════════════════════════════════════
    %% DEPENDENCY ARROWS
    %% ════════════════════════════════════

    FAITH-001 --> FAITH-002
    FAITH-001 --> FAITH-003
    FAITH-003 --> FAITH-004
    FAITH-002 --> FAITH-004
    FAITH-001 --> FAITH-005
    FAITH-002 --> FAITH-005
    FAITH-003 --> FAITH-006
    FAITH-002 --> FAITH-007
    FAITH-002 --> FAITH-008
    FAITH-008 --> FAITH-009
    FAITH-007 --> FAITH-010
    FAITH-008 --> FAITH-010
    FAITH-010 --> FAITH-011
    FAITH-010 --> FAITH-012
    FAITH-010 --> FAITH-013
    FAITH-001 --> FAITH-014
    FAITH-002 --> FAITH-014
    FAITH-010 --> FAITH-014
    FAITH-014 --> FAITH-015
    FAITH-057 --> FAITH-015
    FAITH-009 --> FAITH-015
    FAITH-015 --> FAITH-016
    FAITH-009 --> FAITH-016
    FAITH-016 --> FAITH-017
    FAITH-015 --> FAITH-018
    FAITH-003 --> FAITH-019
    FAITH-019 --> FAITH-020
    FAITH-008 --> FAITH-020
    FAITH-008 --> FAITH-021
    FAITH-003 --> FAITH-022
    FAITH-008 --> FAITH-022
    FAITH-057 --> FAITH-022
    FAITH-022 --> FAITH-023
    FAITH-003 --> FAITH-024
    FAITH-008 --> FAITH-024
    FAITH-057 --> FAITH-024
    FAITH-003 --> FAITH-025
    FAITH-008 --> FAITH-025
    FAITH-003 --> FAITH-026
    FAITH-008 --> FAITH-026
    FAITH-035 --> FAITH-026
    FAITH-022 --> FAITH-027
    FAITH-002 --> FAITH-028
    FAITH-022 --> FAITH-028
    FAITH-019 --> FAITH-029
    FAITH-026 --> FAITH-030
    FAITH-008 --> FAITH-030
    FAITH-003 --> FAITH-031
    FAITH-035 --> FAITH-031
    FAITH-022 --> FAITH-032
    FAITH-002 --> FAITH-033
    FAITH-010 --> FAITH-034
    FAITH-022 --> FAITH-034
    FAITH-014 --> FAITH-035
    FAITH-003 --> FAITH-035
    FAITH-002 --> FAITH-036
    FAITH-008 --> FAITH-036
    FAITH-074 --> FAITH-038
    FAITH-078 --> FAITH-038
    FAITH-020 --> FAITH-039
    FAITH-074 --> FAITH-039
    FAITH-036 --> FAITH-040
    FAITH-074 --> FAITH-040
    FAITH-074 --> FAITH-041
    FAITH-078 --> FAITH-041
    FAITH-074 --> FAITH-042
    FAITH-077 --> FAITH-042
    FAITH-015 --> FAITH-043
    FAITH-074 --> FAITH-043
    FAITH-021 --> FAITH-044
    FAITH-074 --> FAITH-044
    FAITH-009 --> FAITH-045
    FAITH-015 --> FAITH-046
    FAITH-013 --> FAITH-047
    FAITH-030 --> FAITH-047
    FAITH-021 --> FAITH-048
    FAITH-045 --> FAITH-048
    FAITH-047 --> FAITH-048
    FAITH-036 --> FAITH-049
    FAITH-003 --> FAITH-049
    FAITH-014 --> FAITH-049
    FAITH-057 --> FAITH-049
    FAITH-049 --> FAITH-050
    FAITH-057 --> FAITH-050
    FAITH-003 --> FAITH-050
    FAITH-049 --> FAITH-051
    FAITH-057 --> FAITH-051
    FAITH-001 --> FAITH-052
    FAITH-002 --> FAITH-052
    FAITH-003 --> FAITH-052
    FAITH-004 --> FAITH-052
    FAITH-005 --> FAITH-052
    FAITH-006 --> FAITH-052
    FAITH-007 --> FAITH-052
    FAITH-008 --> FAITH-052
    FAITH-009 --> FAITH-052
    FAITH-010 --> FAITH-052
    FAITH-011 --> FAITH-052
    FAITH-012 --> FAITH-052
    FAITH-013 --> FAITH-052
    FAITH-014 --> FAITH-052
    FAITH-015 --> FAITH-052
    FAITH-016 --> FAITH-052
    FAITH-017 --> FAITH-052
    FAITH-018 --> FAITH-052
    FAITH-019 --> FAITH-052
    FAITH-020 --> FAITH-052
    FAITH-021 --> FAITH-052
    FAITH-022 --> FAITH-052
    FAITH-023 --> FAITH-052
    FAITH-024 --> FAITH-052
    FAITH-025 --> FAITH-052
    FAITH-026 --> FAITH-052
    FAITH-027 --> FAITH-052
    FAITH-028 --> FAITH-052
    FAITH-029 --> FAITH-052
    FAITH-030 --> FAITH-052
    FAITH-031 --> FAITH-052
    FAITH-032 --> FAITH-052
    FAITH-033 --> FAITH-052
    FAITH-034 --> FAITH-052
    FAITH-035 --> FAITH-052
    FAITH-036 --> FAITH-052
    FAITH-038 --> FAITH-052
    FAITH-039 --> FAITH-052
    FAITH-040 --> FAITH-052
    FAITH-041 --> FAITH-052
    FAITH-042 --> FAITH-052
    FAITH-043 --> FAITH-052
    FAITH-044 --> FAITH-052
    FAITH-045 --> FAITH-052
    FAITH-046 --> FAITH-052
    FAITH-047 --> FAITH-052
    FAITH-048 --> FAITH-052
    FAITH-049 --> FAITH-052
    FAITH-050 --> FAITH-052
    FAITH-051 --> FAITH-052
    FAITH-053 --> FAITH-052
    FAITH-054 --> FAITH-052
    FAITH-055 --> FAITH-052
    FAITH-056 --> FAITH-052
    FAITH-057 --> FAITH-052
    FAITH-058 --> FAITH-052
    FAITH-059 --> FAITH-052
    FAITH-062 --> FAITH-052
    FAITH-063 --> FAITH-052
    FAITH-064 --> FAITH-052
    FAITH-065 --> FAITH-052
    FAITH-066 --> FAITH-052
    FAITH-067 --> FAITH-052
    FAITH-068 --> FAITH-052
    FAITH-069 --> FAITH-052
    FAITH-070 --> FAITH-052
    FAITH-071 --> FAITH-052
    FAITH-072 --> FAITH-052
    FAITH-073 --> FAITH-052
    FAITH-074 --> FAITH-052
    FAITH-075 --> FAITH-052
    FAITH-076 --> FAITH-052
    FAITH-077 --> FAITH-052
    FAITH-078 --> FAITH-052
    FAITH-079 --> FAITH-052
    FAITH-080 --> FAITH-052
    FAITH-081 --> FAITH-052
    FAITH-082 --> FAITH-052
    FAITH-083 --> FAITH-052
    FAITH-084 --> FAITH-052
    FAITH-085 --> FAITH-052
    FAITH-086 --> FAITH-052
    FAITH-087 --> FAITH-052
    FAITH-088 --> FAITH-052
    FAITH-089 --> FAITH-052
    FAITH-090 --> FAITH-052
    FAITH-091 --> FAITH-052
    FAITH-092 --> FAITH-052
    FAITH-093 --> FAITH-052
    FAITH-094 --> FAITH-052
    FAITH-095 --> FAITH-052
    FAITH-096 --> FAITH-052
    FAITH-097 --> FAITH-052
    FAITH-098 --> FAITH-052
    FAITH-099 --> FAITH-052
    FAITH-100 --> FAITH-052
    FAITH-101 --> FAITH-052
    FAITH-102 --> FAITH-052
    FAITH-103 --> FAITH-052
    FAITH-104 --> FAITH-052
    FAITH-105 --> FAITH-052
    FAITH-106 --> FAITH-052
    FAITH-107 --> FAITH-052
    FAITH-108 --> FAITH-052
    FAITH-109 --> FAITH-052
    FAITH-110 --> FAITH-052
    FAITH-111 --> FAITH-052
    FAITH-112 --> FAITH-052
    FAITH-113 --> FAITH-052
    FAITH-114 --> FAITH-052
    FAITH-115 --> FAITH-052
    FAITH-116 --> FAITH-052
    FAITH-117 --> FAITH-052
    FAITH-118 --> FAITH-052
    FAITH-119 --> FAITH-052
    FAITH-120 --> FAITH-052
    FAITH-121 --> FAITH-052
    FAITH-122 --> FAITH-052
    FAITH-123 --> FAITH-052
    FAITH-124 --> FAITH-052
    FAITH-125 --> FAITH-052
    FAITH-126 --> FAITH-052
    FAITH-127 --> FAITH-052
    FAITH-128 --> FAITH-052
    FAITH-049 --> FAITH-053
    FAITH-057 --> FAITH-053
    FAITH-005 --> FAITH-054
    FAITH-036 --> FAITH-054
    FAITH-015 --> FAITH-054
    FAITH-054 --> FAITH-055
    FAITH-019 --> FAITH-055
    FAITH-055 --> FAITH-056
    FAITH-004 --> FAITH-056
    FAITH-014 --> FAITH-057
    FAITH-014 --> FAITH-058
    FAITH-036 --> FAITH-058
    FAITH-074 --> FAITH-058
    FAITH-078 --> FAITH-058
    FAITH-005 --> FAITH-059
    FAITH-036 --> FAITH-059
    FAITH-074 --> FAITH-062
    FAITH-075 --> FAITH-062
    FAITH-062 --> FAITH-063
    FAITH-074 --> FAITH-063
    FAITH-075 --> FAITH-063
    FAITH-074 --> FAITH-064
    FAITH-062 --> FAITH-064
    FAITH-005 --> FAITH-065
    FAITH-034 --> FAITH-066
    FAITH-022 --> FAITH-066
    FAITH-004 --> FAITH-067
    FAITH-013 --> FAITH-067
    FAITH-019 --> FAITH-067
    FAITH-051 --> FAITH-067
    FAITH-012 --> FAITH-068
    FAITH-016 --> FAITH-068
    FAITH-022 --> FAITH-068
    FAITH-036 --> FAITH-068
    FAITH-038 --> FAITH-068
    FAITH-081 --> FAITH-068
    FAITH-068 --> FAITH-069
    FAITH-081 --> FAITH-069
    FAITH-038 --> FAITH-070
    FAITH-041 --> FAITH-070
    FAITH-064 --> FAITH-070
    FAITH-069 --> FAITH-070
    FAITH-036 --> FAITH-071
    FAITH-038 --> FAITH-071
    FAITH-074 --> FAITH-071
    FAITH-078 --> FAITH-071
    FAITH-038 --> FAITH-072
    FAITH-070 --> FAITH-072
    FAITH-010 --> FAITH-073
    FAITH-038 --> FAITH-073
    FAITH-071 --> FAITH-073
    FAITH-036 --> FAITH-074
    FAITH-078 --> FAITH-074
    FAITH-074 --> FAITH-075
    FAITH-084 --> FAITH-075
    FAITH-074 --> FAITH-076
    FAITH-074 --> FAITH-077
    FAITH-036 --> FAITH-078
    FAITH-038 --> FAITH-079
    FAITH-040 --> FAITH-079
    FAITH-058 --> FAITH-079
    FAITH-074 --> FAITH-079
    FAITH-041 --> FAITH-080
    FAITH-074 --> FAITH-080
    FAITH-078 --> FAITH-080
    FAITH-012 --> FAITH-081
    FAITH-014 --> FAITH-081
    FAITH-035 --> FAITH-081
    FAITH-015 --> FAITH-082
    FAITH-038 --> FAITH-082
    FAITH-046 --> FAITH-082
    FAITH-074 --> FAITH-082
    FAITH-003 --> FAITH-083
    FAITH-049 --> FAITH-083
    FAITH-073 --> FAITH-083
    FAITH-003 --> FAITH-084
    FAITH-004 --> FAITH-084
    FAITH-049 --> FAITH-084
    FAITH-074 --> FAITH-084
    FAITH-078 --> FAITH-084
    FAITH-083 --> FAITH-084
    FAITH-041 --> FAITH-085
    FAITH-074 --> FAITH-085
    FAITH-078 --> FAITH-085
    FAITH-015 --> FAITH-086
    FAITH-071 --> FAITH-086
    FAITH-084 --> FAITH-086
    FAITH-083 --> FAITH-087
    FAITH-084 --> FAITH-087
    FAITH-014 --> FAITH-088
    FAITH-015 --> FAITH-088
    FAITH-049 --> FAITH-088
    FAITH-015 --> FAITH-089
    FAITH-016 --> FAITH-089
    FAITH-068 --> FAITH-089
    FAITH-088 --> FAITH-089
    FAITH-046 --> FAITH-090
    FAITH-082 --> FAITH-090
    FAITH-089 --> FAITH-090
    FAITH-015 --> FAITH-091
    FAITH-081 --> FAITH-091
    FAITH-088 --> FAITH-091
    FAITH-001 --> FAITH-092
    FAITH-005 --> FAITH-092
    FAITH-036 --> FAITH-092
    FAITH-095 --> FAITH-092
    FAITH-080 --> FAITH-093
    FAITH-084 --> FAITH-093
    FAITH-092 --> FAITH-093
    FAITH-049 --> FAITH-094
    FAITH-084 --> FAITH-094
    FAITH-092 --> FAITH-094
    FAITH-095 --> FAITH-094
    FAITH-036 --> FAITH-095
    FAITH-084 --> FAITH-095
    FAITH-068 --> FAITH-096
    FAITH-069 --> FAITH-096
    FAITH-081 --> FAITH-096
    FAITH-022 --> FAITH-097
    FAITH-068 --> FAITH-097
    FAITH-021 --> FAITH-098
    FAITH-044 --> FAITH-098
    FAITH-046 --> FAITH-098
    FAITH-068 --> FAITH-098
    FAITH-015 --> FAITH-099
    FAITH-044 --> FAITH-099
    FAITH-074 --> FAITH-099
    FAITH-082 --> FAITH-099
    FAITH-071 --> FAITH-100
    FAITH-073 --> FAITH-100
    FAITH-086 --> FAITH-100
    FAITH-100 --> FAITH-101
    FAITH-022 --> FAITH-101
    FAITH-100 --> FAITH-102
    FAITH-101 --> FAITH-102
    FAITH-082 --> FAITH-102
    FAITH-086 --> FAITH-102
    FAITH-044 --> FAITH-103
    FAITH-084 --> FAITH-103
    FAITH-102 --> FAITH-103
    FAITH-067 --> FAITH-104
    FAITH-084 --> FAITH-104
    FAITH-047 --> FAITH-105
    FAITH-103 --> FAITH-105
    FAITH-104 --> FAITH-105
    FAITH-013 --> FAITH-106
    FAITH-104 --> FAITH-106
    FAITH-105 --> FAITH-106
    FAITH-013 --> FAITH-107
    FAITH-102 --> FAITH-107
    FAITH-104 --> FAITH-107
    FAITH-105 --> FAITH-107
    FAITH-044 --> FAITH-108
    FAITH-081 --> FAITH-108
    FAITH-084 --> FAITH-108
    FAITH-074 --> FAITH-108
    FAITH-035 --> FAITH-109
    FAITH-108 --> FAITH-109
    FAITH-109 --> FAITH-110
    FAITH-109 --> FAITH-111
    FAITH-110 --> FAITH-111
    FAITH-081 --> FAITH-112
    FAITH-108 --> FAITH-112
    FAITH-111 --> FAITH-112
    FAITH-013 --> FAITH-113
    FAITH-102 --> FAITH-113
    FAITH-104 --> FAITH-113
    FAITH-105 --> FAITH-113
    FAITH-046 --> FAITH-114
    FAITH-082 --> FAITH-114
    FAITH-113 --> FAITH-114
    FAITH-113 --> FAITH-115
    FAITH-114 --> FAITH-115
    FAITH-085 --> FAITH-116
    FAITH-103 --> FAITH-116
    FAITH-113 --> FAITH-116
    FAITH-115 --> FAITH-116
    FAITH-100 --> FAITH-117
    FAITH-102 --> FAITH-117
    FAITH-098 --> FAITH-117
    FAITH-027 --> FAITH-118
    FAITH-032 --> FAITH-118
    FAITH-118 --> FAITH-119
    FAITH-118 --> FAITH-120
    FAITH-119 --> FAITH-120
    FAITH-099 --> FAITH-121
    FAITH-118 --> FAITH-121
    FAITH-121 --> FAITH-122
    FAITH-084 --> FAITH-122
    FAITH-074 --> FAITH-122
    FAITH-099 --> FAITH-123
    FAITH-121 --> FAITH-123
    FAITH-122 --> FAITH-123
    FAITH-021 --> FAITH-124
    FAITH-098 --> FAITH-124
    FAITH-120 --> FAITH-124
    FAITH-044 --> FAITH-125
    FAITH-099 --> FAITH-125
    FAITH-103 --> FAITH-125
    FAITH-084 --> FAITH-125
    FAITH-038 --> FAITH-126
    FAITH-041 --> FAITH-126
    FAITH-057 --> FAITH-126
    FAITH-074 --> FAITH-126
    FAITH-098 --> FAITH-126
    FAITH-038 --> FAITH-127
    FAITH-038 --> FAITH-128
    FAITH-127 --> FAITH-128

    %% ════════════════════════════════════
    %% PHASE COLOUR CODING
    %% ════════════════════════════════════

    classDef phase1 fill:#1e3a5f,stroke:#4a90d9,color:#ffffff
    classDef phase2 fill:#2d4a22,stroke:#6abf4b,color:#ffffff
    classDef phase3 fill:#5c3d1e,stroke:#d4943a,color:#ffffff
    classDef phase4 fill:#4a1942,stroke:#b84daf,color:#ffffff
    classDef phase5 fill:#5a1a1a,stroke:#e04040,color:#ffffff
    classDef phase6 fill:#1a4a4a,stroke:#40c0c0,color:#ffffff
    classDef phase7 fill:#4a4a1a,stroke:#c0c040,color:#ffffff
    classDef phase8 fill:#2a1a4a,stroke:#8040c0,color:#ffffff
    classDef phase9 fill:#3a2a1a,stroke:#c09040,color:#ffffff
    classDef phase10 fill:#1a3a3a,stroke:#40a0a0,color:#ffffff
    classDef phase11 fill:#3a1a3a,stroke:#a040a0,color:#ffffff
    classDef phase12 fill:#4a0a0a,stroke:#ff4040,color:#ffffff
    classDef phase13 fill:#22315a,stroke:#88a0ff,color:#ffffff
    classDef phase14 fill:#123c32,stroke:#4fd1b5,color:#ffffff
    classDef phase15 fill:#4a2b12,stroke:#ffb366,color:#ffffff
    classDef phase16 fill:#2c244a,stroke:#b8a1ff,color:#ffffff
    classDef phase17 fill:#3b2f18,stroke:#d9b36c,color:#ffffff
    classDef phase18 fill:#1f3a2e,stroke:#73d6a3,color:#ffffff
    classDef phase19 fill:#3a2338,stroke:#d68fd0,color:#ffffff
    classDef phase20 fill:#24303f,stroke:#7ab6f5,color:#ffffff
    classDef phase21 fill:#24361f,stroke:#9dd06b,color:#ffffff
    classDef phase22 fill:#2f233d,stroke:#c59bff,color:#ffffff

    class FAITH-001,FAITH-002,FAITH-003,FAITH-004,FAITH-005,FAITH-006 phase1
    class FAITH-007,FAITH-008,FAITH-009 phase2
    class FAITH-010,FAITH-011,FAITH-012,FAITH-013 phase3
    class FAITH-014,FAITH-015,FAITH-016,FAITH-017,FAITH-018,FAITH-057 phase4
    class FAITH-019,FAITH-020,FAITH-021 phase5
    class FAITH-022,FAITH-023,FAITH-024,FAITH-025,FAITH-026,FAITH-027,FAITH-028,FAITH-029,FAITH-030,FAITH-031,FAITH-032,FAITH-033 phase6
    class FAITH-034,FAITH-035,FAITH-066,FAITH-068,FAITH-069,FAITH-081 phase7
    class FAITH-036,FAITH-038,FAITH-039,FAITH-040,FAITH-041,FAITH-042,FAITH-043,FAITH-044,FAITH-058,FAITH-062,FAITH-064,FAITH-070,FAITH-071,FAITH-072,FAITH-073,FAITH-079,FAITH-080,FAITH-082,FAITH-084,FAITH-085,FAITH-086,FAITH-087,FAITH-096,FAITH-097,FAITH-098,FAITH-099,FAITH-125 phase8
    class FAITH-045,FAITH-046,FAITH-047,FAITH-048 phase9
    class FAITH-049,FAITH-050,FAITH-051,FAITH-053,FAITH-065,FAITH-067,FAITH-083 phase10
    class FAITH-054,FAITH-055,FAITH-056,FAITH-059 phase11
    class FAITH-052 phase12
    class FAITH-063,FAITH-074,FAITH-075,FAITH-076,FAITH-077,FAITH-078 phase13
    class FAITH-088,FAITH-089,FAITH-090,FAITH-091 phase14
    class FAITH-092,FAITH-093,FAITH-094,FAITH-095 phase15
    class FAITH-100,FAITH-101,FAITH-102,FAITH-103,FAITH-104,FAITH-105,FAITH-106,FAITH-107 phase16
    class FAITH-108,FAITH-109,FAITH-110,FAITH-111,FAITH-112 phase17
    class FAITH-113,FAITH-114,FAITH-115,FAITH-116,FAITH-117 phase18
    class FAITH-118,FAITH-119,FAITH-120,FAITH-121,FAITH-122,FAITH-123,FAITH-124 phase19
    class FAITH-126 phase20
    class FAITH-127 phase21
    class FAITH-128 phase22
```

---

## 1. Critical Path Analysis

The critical path is the longest weighted dependency chain before cloud deployment.

### Primary Critical Path

```
FAITH-001 -> FAITH-002 -> FAITH-007 -> FAITH-010 -> FAITH-014 -> FAITH-057 -> FAITH-049 -> FAITH-083 -> FAITH-084 -> FAITH-086 -> FAITH-100 -> FAITH-101 -> FAITH-102 -> FAITH-103 -> FAITH-105 -> FAITH-113 -> FAITH-114 -> FAITH-115 -> FAITH-116
```

**Weighted duration estimate:** ~44 days using the epic complexity weights.

---

## 2. Parallel Execution Schedule (Waves)

Each wave contains tasks whose dependencies are fully satisfied by all prior waves.

### Wave 1
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-001 | Project Directory Structure & Base Scaffolding | 1 (Foundation) | DONE | S |

### Wave 2
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-002 | Redis Container Setup | 1 (Foundation) | DONE | S |
| FAITH-003 | Configuration System: YAML Loading & Pydantic Models | 1 (Foundation) | DONE | M |

### Wave 3
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-004 | Config Hot-Reload Watcher | 1 (Foundation) | DONE | M |
| FAITH-005 | FAITH CLI (`faith-cli` Package) | 1 (Foundation) | DONE | M |
| FAITH-006 | Config Migration System | 1 (Foundation) | DONE | S |
| FAITH-007 | Compact Protocol Data Models & Serialisation | 2 (Protocol & Events) | DONE | M |
| FAITH-008 | Event System Data Models & Publisher | 2 (Protocol & Events) | DONE | M |
| FAITH-019 | Security YAML Schema & Regex Approval Engine | 5 (Security) | DONE | M |
| FAITH-033 | Key-Value Store MCP Server | 6 (Tool Servers) | DONE | S |

### Wave 4
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-009 | Event Subscriber & Dispatcher | 2 (Protocol & Events) | DONE | M |
| FAITH-010 | Base Agent Class | 3 (Agent Runtime) | DONE | L |
| FAITH-020 | Approval Request/Response Flow | 5 (Security) | DONE | M |
| FAITH-021 | Audit Log System | 5 (Security) | DONE | M |
| FAITH-025 | PostgreSQL Database MCP Server | 6 (Tool Servers) | DONE | M |
| FAITH-029 | Git MCP Server | 6 (Tool Servers) | DONE | M |
| FAITH-036 | FastAPI Server Setup & WebSocket Endpoints | 8 (Web UI) | DONE | M |
| FAITH-065 | Docker Daemon Not Running Guidance | 10 (First Run) | TODO | S |

### Wave 5
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-011 | Rolling Context Summary & Compaction | 3 (Agent Runtime) | DONE | M |
| FAITH-012 | MCP Adapter Layer | 3 (Agent Runtime) | DONE | L |
| FAITH-013 | LLM API Client (Ollama + OpenRouter) | 3 (Agent Runtime) | DONE | M |
| FAITH-014 | PA Container Setup & Docker SDK Integration | 4 (PA Core) | DONE | M |
| FAITH-045 | Event Log Writer | 9 (Logging) | DONE | S |
| FAITH-059 | Service Route Discovery & `faith show-urls` | 11 (CLI & Skills) | DONE | S |
| FAITH-078 | Frontend Build Pipeline & Bundled Asset Integration | 13 (Web UI Workspace Migration) | DONE | M |

### Wave 6
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-035 | External MCP Server Registration & Lifecycle | 7 (CAG & External MCP) | DONE | M |
| FAITH-057 | Disposable Sandbox Lifecycle & Scheduling | 4 (PA Core) | DONE | L |
| FAITH-074 | React + Dockview Workspace Shell Migration | 13 (Web UI Workspace Migration) | DONE | L |

### Wave 7
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-015 | PA Session & Task Management | 4 (PA Core) | DONE | L |
| FAITH-022 | Filesystem MCP Server | 6 (Tool Servers) | DONE | L |
| FAITH-024 | Python Execution MCP Server | 6 (Tool Servers) | DONE | M |
| FAITH-026 | Browser Automation MCP Server (Playwright) | 6 (Tool Servers) | DONE | M |
| FAITH-031 | Web Search MCP Server | 6 (Tool Servers) | DONE | M |
| FAITH-038 | Agent Panel Component (Rich Transcript + React) | 8 (Web UI) | DONE | M |
| FAITH-039 | Approval Panel Component | 8 (Web UI) | DONE | M |
| FAITH-040 | System Status Panel & Health Summary | 8 (Web UI) | DONE | S |
| FAITH-041 | Input Panel & File Upload | 8 (Web UI) | DONE | S |
| FAITH-044 | Web UI Log Views | 8 (Web UI) | DONE | M |
| FAITH-049 | First-Run Wizard: Multi-Step UI | 10 (First Run) | IN PROGRESS | L |
| FAITH-058 | Docker Runtime & Image Panel | 8 (Web UI) | DONE | M |
| FAITH-076 | Minimized Panel Tray for Dockview | 13 (Web UI Workspace Migration) | DONE | M |
| FAITH-077 | Radix UI Menubar & Context Menu Integration | 13 (Web UI Workspace Migration) | DONE | M |
| FAITH-081 | Canonical MCP Registry & Agent Tool Manifest Propagation | 7 (CAG & External MCP) | DONE | L |

### Wave 8
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-016 | PA Event Dispatcher & Intervention Logic | 4 (PA Core) | DONE | L |
| FAITH-018 | Living FRS Management | 4 (PA Core) | DONE | M |
| FAITH-023 | Filesystem File History | 6 (Tool Servers) | DONE | M |
| FAITH-027 | Code Index MCP Server (tree-sitter) | 6 (Tool Servers) | DONE | L |
| FAITH-028 | RAG / ChromaDB MCP Server | 6 (Tool Servers) | DONE | L |
| FAITH-030 | Pricing MCP Server | 6 (Tool Servers) | DONE | M |
| FAITH-032 | Full-Text Search MCP Server | 6 (Tool Servers) | DONE | S |
| FAITH-034 | CAG Implementation | 7 (CAG & External MCP) | DONE | M |
| FAITH-042 | Shared Web UI Theme System | 8 (Web UI) | DONE | S |
| FAITH-043 | Project Switcher UI | 8 (Web UI) | DONE | S |
| FAITH-046 | Session & Task Log Writer | 9 (Logging) | DONE | M |
| FAITH-050 | Privacy Profile Enforcement & Provider Knowledge Base | 10 (First Run) | TODO | M |
| FAITH-051 | Ollama Model Download Integration | 10 (First Run) | DONE | S |
| FAITH-053 | First-Run Wizard: Detailed Specification | 10 (First Run) | TODO | M |
| FAITH-054 | `faith run` Command & Task API | 11 (CLI & Skills) | TODO | M |
| FAITH-071 | PA System Prompt Editor Panel | 8 (Web UI) | DONE | M |
| FAITH-079 | Runtime Badge & Container Status Sync | 8 (Web UI) | DONE | S |
| FAITH-080 | Speech-to-Text Dictation Input | 8 (Web UI) | DONE | M |
| FAITH-085 | Input Panel Enter-to-Send & Newline Hint | 8 (Web UI) | DONE | S |
| FAITH-088 | Runtime Specialist-Agent Materialisation & Lifecycle | 14 (Specialist-Agent Delegation from PA Chat) | TODO | L |
| FAITH-127 | Project Agent Fenced Code Block Styling Polish | 21 (Transcript Rendering Polish) | DONE | S |

### Wave 9
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-017 | Loop Detection | 4 (PA Core) | DONE | M |
| FAITH-047 | Token & Cost Log | 9 (Logging) | DONE | S |
| FAITH-055 | Skill Definitions & Unattended Execution | 11 (CLI & Skills) | TODO | M |
| FAITH-066 | Project `cag/` Auto-Loading & Budget Guidance | 7 (CAG & External MCP) | DONE | M |
| FAITH-067 | Ollama Management MCP Server | 10 (First Run) | DONE | M |
| FAITH-068 | PA Chat MCP Tool-Calling Loop | 7 (CAG & External MCP) | DONE | M |
| FAITH-073 | Agent Runtime Date Time Prompt Injection | 8 (Web UI) | DONE | S |
| FAITH-082 | Project Agent Transcript Rehydration on Restart | 8 (Web UI) | DONE | S |
| FAITH-091 | Canonical Specialist-Agent Team Manifest & Delegation Grounding | 14 (Specialist-Agent Delegation from PA Chat) | TODO | M |
| FAITH-118 | Filetype Resolver Framework for Deterministic Excerpt Boundaries | 19 (Scoped File Storage & Deterministic Excerpt Retrieval) | TODO | L |
| FAITH-128 | Project Agent Responsive Bubble Width Behaviour | 22 (Responsive Transcript Bubble Layout) | DONE | S |

### Wave 10
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-048 | Log Retention & Rotation | 9 (Logging) | DONE | S |
| FAITH-056 | Built-in Skill Scheduler | 11 (CLI & Skills) | TODO | M |
| FAITH-069 | PA MCP Inventory Grounding | 7 (CAG & External MCP) | DONE | S |
| FAITH-083 | User Timezone Preference Resolution & Persistence | 10 (First Run) | IN PROGRESS | S |
| FAITH-089 | PA Chat Specialist Delegation Loop | 14 (Specialist-Agent Delegation from PA Chat) | TODO | L |
| FAITH-097 | Project-Workspace Absolute Path Normalisation for Chat Tool Calls | 8 (Web UI) | DONE | S |
| FAITH-098 | PA Chat Tool Call Audit & Session Visibility | 8 (Web UI) | DONE | M |
| FAITH-099 | Session History Live Session Creation & Default Placement | 8 (Web UI) | DONE | M |
| FAITH-119 | Excerpt Discovery Summary MCP Function | 19 (Scoped File Storage & Deterministic Excerpt Retrieval) | TODO | M |

### Wave 11
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-084 | User Settings Window & Profile Preferences | 8 (Web UI) | DONE | M |
| FAITH-090 | Delegated Specialist Result Relay & Persistence | 14 (Specialist-Agent Delegation from PA Chat) | TODO | M |
| FAITH-096 | Deterministic User-Requested Tool Selection in PA Chat | 8 (Web UI) | DONE | M |
| FAITH-120 | Excerpt Retrieval MCP Function for Multi-Format Files | 19 (Scoped File Storage & Deterministic Excerpt Retrieval) | TODO | L |
| FAITH-121 | Scoped Attachment Ingestion and Deduplicated Storage Lifecycle | 19 (Scoped File Storage & Deterministic Excerpt Retrieval) | TODO | L |
| FAITH-126 | User Terminal Panel in Active Agent Runtime Context | 20 (Interactive User Terminal & Runtime Console) | TODO | L |

### Wave 12
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-075 | Dockview Default Layout & Panel Constraints | 13 (Web UI Workspace Migration) | DONE | M |
| FAITH-086 | Host-Backed Web UI Saved State Persistence | 8 (Web UI) | DONE | S |
| FAITH-087 | Locale & Timezone Fixed-Option Selectors | 8 (Web UI) | DONE | S |
| FAITH-095 | Optional Text-to-Speech Runtime & Spoken Reply Integration | 15 (Optional Voice & Avatar Experience) | TODO | M |
| FAITH-104 | Model Catalog, Context Metadata, and Manual Override Management | 16 (Project Instruction Context & Model Intelligence) | DONE | L |
| FAITH-108 | Registry-Driven Tools Menu & Manage Tools Panel | 17 (Managed MCP Tool Acquisition & Governance) | TODO | M |
| FAITH-122 | Storage Inventory, Trash, and Export Panels | 19 (Scoped File Storage & Deterministic Excerpt Retrieval) | TODO | L |
| FAITH-124 | Replay-Friendly MCP Tool Audit Artifacts | 19 (Scoped File Storage & Deterministic Excerpt Retrieval) | TODO | M |

### Wave 13
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-062 | Panel Lifecycle & Deduping | 8 (Web UI) | DONE | S |
| FAITH-092 | Containerised Avatar Runtime & Service Contract | 15 (Optional Voice & Avatar Experience) | TODO | L |
| FAITH-100 | PA Project-Root AGENTS.md Instruction Source | 16 (Project Instruction Context & Model Intelligence) | DONE | M |
| FAITH-109 | GitHub and ZIP MCP Tool Acquisition Review Flow | 17 (Managed MCP Tool Acquisition & Governance) | TODO | L |
| FAITH-123 | Session Naming, Scoped File Access, and Session Export Controls | 19 (Scoped File Storage & Deterministic Excerpt Retrieval) | TODO | M |

### Wave 14
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-063 | Snap-Grid Panel Layout Refinement | 13 (Web UI Workspace Migration) | DONE | M |
| FAITH-064 | Panel Title-Bar Actions | 8 (Web UI) | DONE | S |
| FAITH-093 | Avatar Panel, Speech Playback, and Voice Chat Integration | 15 (Optional Voice & Avatar Experience) | TODO | L |
| FAITH-094 | Avatar Runtime Install, Removal, and Preference Management | 15 (Optional Voice & Avatar Experience) | TODO | M |
| FAITH-101 | AGENTS.md Include Resolution & Reference Normalisation | 16 (Project Instruction Context & Model Intelligence) | DONE | L |
| FAITH-110 | Managed Tools Directory, Trust Badges, Update Notifications, and Rollback Retention | 17 (Managed MCP Tool Acquisition & Governance) | TODO | M |

### Wave 15
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-070 | Theme-Aware Chat Transcript Bubbles | 8 (Web UI) | DONE | M |
| FAITH-102 | Effective PA Context Compiler, Hash Cache, and Persistence | 16 (Project Instruction Context & Model Intelligence) | DONE | L |
| FAITH-111 | Per-Function Tool Permissions, Health States, and Local Failure Classification | 17 (Managed MCP Tool Acquisition & Governance) | TODO | L |

### Wave 16
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-072 | PA Transcript Scroll Containment | 8 (Web UI) | DONE | S |
| FAITH-103 | Effective Context Debug Panel & Redacted Snapshot Inspection | 16 (Project Instruction Context & Model Intelligence) | DONE | M |
| FAITH-112 | Dynamic Tool Lifecycle Activation on Next Inference Turn | 17 (Managed MCP Tool Acquisition & Governance) | TODO | M |
| FAITH-117 | Explicit Durable Rule Promotion from Inference to AGENTS.md | 18 (Runtime Context Compaction & Rule Promotion) | DONE | M |

### Wave 17
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-105 | Token Panel Context Diagnostics & Per-File Attribution | 16 (Project Instruction Context & Model Intelligence) | DONE | M |
| FAITH-125 | Session Selector, Session Details Panel, and Effective Context UX Cleanup | 8 (Web UI) | DONE | M |

### Wave 18
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-106 | Context-Fit Warnings, VRAM Heuristics, and Early Compaction Guidance | 16 (Project Instruction Context & Model Intelligence) | DONE | M |
| FAITH-107 | Automatic OpenRouter Prompt-Caching Optimisation | 16 (Project Instruction Context & Model Intelligence) | DONE | M |
| FAITH-113 | Active Context Usage Tracking & Compaction Thresholds | 18 (Runtime Context Compaction & Rule Promotion) | DONE | M |

### Wave 19
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-114 | Deterministic Retention Rules & Durable History Preservation for Compaction | 18 (Runtime Context Compaction & Rule Promotion) | DONE | M |

### Wave 20
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-115 | Local-Ollama History Compaction Summariser | 18 (Runtime Context Compaction & Rule Promotion) | DONE | L |

### Wave 21
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-116 | Hard Compaction UX Blocking, Buffering Indicator, and Diagnostics | 18 (Runtime Context Compaction & Rule Promotion) | DONE | M |

### Wave 22
| Task | Name | Phase | Status | Complexity |
|------|------|-------|--------|------------|
| FAITH-052 | Cloud Deployment Architecture | 12 (Cloud) | TODO | XL |

---

## 3. Summary Table

| Wave | Tasks | Dependencies Satisfied By | Max Parallelism |
| ------ | ------- | --------------------------- | ----------------- |
| **Wave 1** | FAITH-001 | (none) | 1 |
| **Wave 2** | FAITH-002, FAITH-003 | Wave 1 | 2 |
| **Wave 3** | FAITH-004, FAITH-005, FAITH-006, FAITH-007, FAITH-008, FAITH-019, FAITH-033 | Waves 1-2 | 7 |
| **Wave 4** | FAITH-009, FAITH-010, FAITH-020, FAITH-021, FAITH-025, FAITH-029, FAITH-036, FAITH-065 | Waves 1-3 | 8 |
| **Wave 5** | FAITH-011, FAITH-012, FAITH-013, FAITH-014, FAITH-045, FAITH-059, FAITH-078 | Waves 1-4 | 7 |
| **Wave 6** | FAITH-035, FAITH-057, FAITH-074 | Waves 1-5 | 3 |
| **Wave 7** | FAITH-015, FAITH-022, FAITH-024, FAITH-026, FAITH-031, FAITH-038, FAITH-039, FAITH-040, FAITH-041, FAITH-044, FAITH-049, FAITH-058, FAITH-076, FAITH-077, FAITH-081 | Waves 1-6 | 15 |
| **Wave 8** | FAITH-016, FAITH-018, FAITH-023, FAITH-027, FAITH-028, FAITH-030, FAITH-032, FAITH-034, FAITH-042, FAITH-043, FAITH-046, FAITH-050, FAITH-051, FAITH-053, FAITH-054, FAITH-071, FAITH-079, FAITH-080, FAITH-085, FAITH-088, FAITH-127 | Waves 1-7 | 21 |
| **Wave 9** | FAITH-017, FAITH-047, FAITH-055, FAITH-066, FAITH-067, FAITH-068, FAITH-073, FAITH-082, FAITH-091, FAITH-118, FAITH-128 | Waves 1-8 | 11 |
| **Wave 10** | FAITH-048, FAITH-056, FAITH-069, FAITH-083, FAITH-089, FAITH-097, FAITH-098, FAITH-099, FAITH-119 | Waves 1-9 | 9 |
| **Wave 11** | FAITH-084, FAITH-090, FAITH-096, FAITH-120, FAITH-121, FAITH-126 | Waves 1-10 | 6 |
| **Wave 12** | FAITH-075, FAITH-086, FAITH-087, FAITH-095, FAITH-104, FAITH-108, FAITH-122, FAITH-124 | Waves 1-11 | 8 |
| **Wave 13** | FAITH-062, FAITH-092, FAITH-100, FAITH-109, FAITH-123 | Waves 1-12 | 5 |
| **Wave 14** | FAITH-063, FAITH-064, FAITH-093, FAITH-094, FAITH-101, FAITH-110 | Waves 1-13 | 6 |
| **Wave 15** | FAITH-070, FAITH-102, FAITH-111 | Waves 1-14 | 3 |
| **Wave 16** | FAITH-072, FAITH-103, FAITH-112, FAITH-117 | Waves 1-15 | 4 |
| **Wave 17** | FAITH-105, FAITH-125 | Waves 1-16 | 2 |
| **Wave 18** | FAITH-106, FAITH-107, FAITH-113 | Waves 1-17 | 3 |
| **Wave 19** | FAITH-114 | Waves 1-18 | 1 |
| **Wave 20** | FAITH-115 | Waves 1-19 | 1 |
| **Wave 21** | FAITH-116 | Waves 1-20 | 1 |
| **Wave 22** | FAITH-052 | Waves 1-21 | 1 |

---

## Notes

- **Total tasks:** 125 (FAITH-001 through FAITH-128)
- **Minimum waves to completion (excl. cloud):** 21
- **Maximum parallelism:** Wave 8 with 21 concurrent tasks
- **Source of truth:** `epic.yaml`
- This file is generated. Edit the epic YAML, then regenerate.
