"""Generate dependency-graph.md from epic.yaml.

This script treats ``epic.yaml`` as the single source of truth for task IDs,
titles, phases, dependencies, complexity, and model guidance. It regenerates
``dependency-graph.md`` and optionally renders ``dependency-graph.svg`` if the
``graphviz`` Python package and Graphviz binaries are available.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
EPIC_PATH = ROOT / "epic.yaml"
OUTPUT_MD = ROOT / "dependency-graph.md"
OUTPUT_SVG = ROOT / "dependency-graph.svg"
GRAPHVIZ_CANDIDATES = [
    Path(r"C:\Program Files\Graphviz\bin"),
    Path(r"C:\Program Files (x86)\Graphviz\bin"),
]

for candidate in GRAPHVIZ_CANDIDATES:
    if (candidate / "dot.exe").exists():
        os.environ["PATH"] = str(candidate) + os.pathsep + os.environ.get("PATH", "")
        break

PHASE_COLOURS = {
    1: ("#1e3a5f", "#4a90d9"),
    2: ("#2d4a22", "#6abf4b"),
    3: ("#5c3d1e", "#d4943a"),
    4: ("#4a1942", "#b84daf"),
    5: ("#5a1a1a", "#e04040"),
    6: ("#1a4a4a", "#40c0c0"),
    7: ("#4a4a1a", "#c0c040"),
    8: ("#2a1a4a", "#8040c0"),
    9: ("#3a2a1a", "#c09040"),
    10: ("#1a3a3a", "#40a0a0"),
    11: ("#3a1a3a", "#a040a0"),
    12: ("#4a0a0a", "#ff4040"),
}

PHASE_NAMES = {
    1: "Foundation",
    2: "Protocol & Events",
    3: "Agent Runtime",
    4: "PA Core",
    5: "Security",
    6: "Tool Servers",
    7: "CAG & External MCP",
    8: "Web UI",
    9: "Logging",
    10: "First Run",
    11: "CLI & Skills",
    12: "Cloud",
}

COMPLEXITY_WEIGHTS = {"S": 0.5, "M": 2.0, "L": 4.0, "XL": 6.0}


@dataclass(frozen=True)
class Task:
    task_id: str
    name: str
    phase: int
    dependencies: tuple[str, ...]
    complexity: str
    model: str
    status: str

    @property
    def numeric_id(self) -> int:
        return int(self.task_id.split("-")[1])


def parse_scalar(value: str) -> str:
    value = value.strip()
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def parse_dependencies(value: str) -> list[str]:
    value = value.strip()
    if value == "[]":
        return []
    if not (value.startswith("[") and value.endswith("]")):
        raise ValueError(f"Unsupported dependency list format: {value}")
    inner = value[1:-1].strip()
    if not inner:
        return []
    parts = [part.strip() for part in inner.split(",") if part.strip()]
    return [parse_scalar(part) for part in parts]


def parse_epic(epic_text: str) -> list[Task]:
    lines = epic_text.splitlines()
    tasks: list[Task] = []
    current_phase_id: int | None = None
    current_task: dict[str, object] | None = None
    in_phases = False
    in_tasks = False

    def flush_current_task() -> None:
        nonlocal current_task
        if current_task is None or current_phase_id is None:
            return
        tasks.append(
            Task(
                task_id=str(current_task["id"]),
                name=str(current_task["name"]),
                phase=current_phase_id,
                dependencies=tuple(current_task.get("dependencies", [])),
                complexity=str(current_task["complexity"]),
                model=str(current_task["model"]),
                status=str(current_task["status"]),
            )
        )
        current_task = None

    for raw_line in lines:
        line = raw_line.rstrip()
        if not line:
            continue

        if line == "phases:":
            in_phases = True
            continue
        if not in_phases:
            continue

        if line.startswith("      - id: "):
            flush_current_task()
            current_task = {"id": parse_scalar(line.split(": ", 1)[1])}
            continue

        if line.startswith("  - id: ") and not line.startswith("      - id: "):
            flush_current_task()
            current_phase_id = int(line.split(": ", 1)[1])
            in_tasks = False
            continue

        if line.strip() == "tasks:":
            flush_current_task()
            in_tasks = True
            continue

        if not in_tasks or current_phase_id is None:
            continue

        if current_task is None:
            continue

        if line.startswith("        "):
            key, raw_value = line.strip().split(": ", 1)
            if key == "dependencies":
                current_task[key] = parse_dependencies(raw_value)
            else:
                current_task[key] = parse_scalar(raw_value)

    flush_current_task()

    task_ids = {task.task_id for task in tasks}
    resolved_tasks: list[Task] = []
    ordered_ids = [task.task_id for task in sorted(tasks, key=lambda t: t.numeric_id)]

    for task in sorted(tasks, key=lambda t: t.numeric_id):
        deps = list(task.dependencies)
        if len(deps) == 1 and deps[0] == "All previous phases":
            deps = [tid for tid in ordered_ids if tid != task.task_id]
        for dep in deps:
            if dep not in task_ids:
                raise ValueError(f"{task.task_id} depends on unknown task {dep}")
        resolved_tasks.append(
            Task(
                task_id=task.task_id,
                name=task.name,
                phase=task.phase,
                dependencies=tuple(deps),
                complexity=task.complexity,
                model=task.model,
                status=task.status,
            )
        )

    return resolved_tasks


def compute_waves(tasks: list[Task]) -> dict[int, list[Task]]:
    task_map = {task.task_id: task for task in tasks}
    wave_by_task: dict[str, int] = {}
    visiting: set[str] = set()

    def resolve_wave(task_id: str) -> int:
        if task_id in wave_by_task:
            return wave_by_task[task_id]
        if task_id in visiting:
            raise ValueError(f"Cyclic dependency detected while resolving {task_id}")

        visiting.add(task_id)
        task = task_map[task_id]
        if not task.dependencies:
            wave = 1
        else:
            wave = max(resolve_wave(dep) for dep in task.dependencies) + 1
        visiting.remove(task_id)
        wave_by_task[task_id] = wave
        return wave

    ordered = sorted(tasks, key=lambda t: t.numeric_id)
    for task in ordered:
        resolve_wave(task.task_id)

    waves: dict[int, list[Task]] = {}
    for task in ordered:
        waves.setdefault(wave_by_task[task.task_id], []).append(task)
    return waves


def compute_longest_paths(tasks: list[Task]) -> tuple[list[str], float]:
    ordered = sorted(tasks, key=lambda t: t.numeric_id)
    task_map = {task.task_id: task for task in tasks}
    best_score: dict[str, float] = {}
    best_path: dict[str, list[str]] = {}
    visiting: set[str] = set()

    def resolve_score(task_id: str) -> float:
        if task_id in best_score:
            return best_score[task_id]
        if task_id in visiting:
            raise ValueError(f"Cyclic dependency detected while resolving {task_id}")

        visiting.add(task_id)
        task = task_map[task_id]
        own_weight = COMPLEXITY_WEIGHTS.get(task.complexity, 1.0)
        if not task.dependencies:
            score = own_weight
            path = [task_id]
        else:
            winning_dep = max(task.dependencies, key=resolve_score)
            score = resolve_score(winning_dep) + own_weight
            path = [*best_path[winning_dep], task_id]
        visiting.remove(task_id)
        best_score[task_id] = score
        best_path[task_id] = path
        return score

    for task in ordered:
        resolve_score(task.task_id)

    non_cloud = [task for task in ordered if task.task_id != "FAITH-052"]
    winner = max(non_cloud, key=lambda task: best_score[task.task_id])
    return best_path[winner.task_id], best_score[winner.task_id]


def markdown_table(rows: Iterable[Iterable[str]]) -> str:
    rendered = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(rendered)


def build_registry_table(tasks: list[Task]) -> str:
    rows = [
        ("Task ID", "Task Name", "Phase", "Status", "Dependencies", "Complexity", "Model"),
        ("---------", "-----------", "-------", "--------", "--------------", "------------", "-------"),
    ]
    for task in sorted(tasks, key=lambda t: t.numeric_id):
        deps = ", ".join(task.dependencies) if task.dependencies else "None"
        rows.append(
            (
                task.task_id,
                task.name,
                f"{task.phase} ({PHASE_NAMES[task.phase]})",
                task.status,
                deps,
                task.complexity,
                task.model,
            )
        )
    return markdown_table(rows)


def build_mermaid(tasks: list[Task], waves: dict[int, list[Task]]) -> str:
    lines: list[str] = ["```mermaid", "flowchart TD"]
    phase_to_tasks: dict[int, list[Task]] = {}
    for task in sorted(tasks, key=lambda t: t.numeric_id):
        phase_to_tasks.setdefault(task.phase, []).append(task)

    for phase in sorted(phase_to_tasks):
        lines.append(f"    %% ── Phase {phase}: {PHASE_NAMES[phase]} ──")
        for task in phase_to_tasks[phase]:
            safe_name = task.name.replace('"', "'")
            lines.append(
                f'    {task.task_id}["{task.task_id}<br/>{safe_name}<br/>'
                f'Phase {task.phase} | {task.status} | {task.complexity}"]'
            )
        lines.append("")

    lines.append("    %% ════════════════════════════════════")
    lines.append("    %% DEPENDENCY ARROWS")
    lines.append("    %% ════════════════════════════════════")
    lines.append("")

    for task in sorted(tasks, key=lambda t: t.numeric_id):
        for dep in task.dependencies:
            lines.append(f"    {dep} --> {task.task_id}")
    lines.append("")
    lines.append("    %% ════════════════════════════════════")
    lines.append("    %% PHASE COLOUR CODING")
    lines.append("    %% ════════════════════════════════════")
    lines.append("")
    for phase, (fill, stroke) in PHASE_COLOURS.items():
        lines.append(
            f"    classDef phase{phase} fill:{fill},stroke:{stroke},color:#ffffff"
        )
    lines.append("")
    for phase in sorted(phase_to_tasks):
        ids = ",".join(task.task_id for task in phase_to_tasks[phase])
        lines.append(f"    class {ids} phase{phase}")
    lines.append("```")
    return "\n".join(lines)


def build_wave_sections(waves: dict[int, list[Task]]) -> tuple[str, str]:
    section_lines: list[str] = []
    summary_rows = [
        ("Wave", "Tasks", "Dependencies Satisfied By", "Max Parallelism"),
        ("------", "-------", "---------------------------", "-----------------"),
    ]

    for wave_num in sorted(waves):
        section_lines.append(f"### Wave {wave_num}")
        section_lines.append("| Task | Name | Phase | Status | Complexity |")
        section_lines.append("|------|------|-------|--------|------------|")
        for task in waves[wave_num]:
            section_lines.append(
                f"| {task.task_id} | {task.name} | {task.phase} ({PHASE_NAMES[task.phase]}) | "
                f"{task.status} | {task.complexity} |"
            )
        section_lines.append("")

        if wave_num == 1:
            deps_text = "(none)"
        elif wave_num == 2:
            deps_text = "Wave 1"
        else:
            deps_text = f"Waves 1-{wave_num - 1}"
        summary_rows.append(
            (
                f"**Wave {wave_num}**",
                ", ".join(task.task_id for task in waves[wave_num]),
                deps_text,
                str(len(waves[wave_num])),
            )
        )

    return "\n".join(section_lines).rstrip(), markdown_table(summary_rows)


def build_markdown(tasks: list[Task]) -> str:
    waves = compute_waves(tasks)
    critical_path, critical_score = compute_longest_paths(tasks)
    non_cloud_waves = [wave for wave in waves if all(task.task_id != "FAITH-052" for task in waves[wave])]
    max_parallelism_wave = max(waves, key=lambda wave: len(waves[wave]))
    wave_sections, summary_table = build_wave_sections(waves)

    lines: list[str] = [
        "# FAITH Epic — Dependency Graph & Implementation Schedule",
        "",
        "**Generated from:** `epic.yaml`",
        f"**Date:** {date.today().isoformat()}",
        "",
        "---",
        "",
        "## Task Registry",
        "",
        build_registry_table(tasks),
        "",
        "---",
        "",
        "## Mermaid Dependency Diagram",
        "",
        build_mermaid(tasks, waves),
        "",
        "---",
        "",
        "## 1. Critical Path Analysis",
        "",
        "The critical path is the longest weighted dependency chain before cloud deployment.",
        "",
        "### Primary Critical Path",
        "",
        "```",
        " -> ".join(critical_path),
        "```",
        "",
        f"**Weighted duration estimate:** ~{critical_score:g} days using the epic complexity weights.",
        "",
        "---",
        "",
        "## 2. Parallel Execution Schedule (Waves)",
        "",
        "Each wave contains tasks whose dependencies are fully satisfied by all prior waves.",
        "",
        wave_sections,
        "",
        "---",
        "",
        "## 3. Summary Table",
        "",
        summary_table,
        "",
        "---",
        "",
        "## Notes",
        "",
        f"- **Total tasks:** {len(tasks)} ({tasks[0].task_id} through {tasks[-1].task_id})",
        f"- **Minimum waves to completion (excl. cloud):** {max(non_cloud_waves)}",
        f"- **Maximum parallelism:** Wave {max_parallelism_wave} with {len(waves[max_parallelism_wave])} concurrent tasks",
        "- **Source of truth:** `epic.yaml`",
        "- This file is generated. Edit the epic YAML, then regenerate.",
    ]
    return "\n".join(lines) + "\n"


def try_render_svg(tasks: list[Task], waves: dict[int, list[Task]]) -> str | None:
    try:
        import graphviz  # type: ignore
    except Exception:
        return None

    dot = graphviz.Digraph(
        "FAITH_Dependencies",
        format="svg",
        engine="dot",
        graph_attr={
            "rankdir": "TB",
            "bgcolor": "#0d1117",
            "fontcolor": "#c9d1d9",
            "fontname": "Helvetica",
            "fontsize": "14",
            "pad": "0.5",
            "nodesep": "0.4",
            "ranksep": "0.8",
            "label": "FAITH — Task Dependency Graph\\n\\n",
            "labelloc": "t",
            "labeljust": "c",
        },
        edge_attr={"color": "#484f58", "arrowsize": "0.6"},
    )

    for wave_num in sorted(waves):
        with dot.subgraph() as subgraph:
            subgraph.attr(rank="same")
            for task in waves[wave_num]:
                fill, font_col = PHASE_COLOURS[task.phase]
                subgraph.node(
                    task.task_id,
                    label=(
                        f"{task.task_id}\\n{task.name}\\n"
                        f"Phase {task.phase} | {task.status}\\n[{task.complexity}]"
                    ),
                    shape="box",
                    style="filled,rounded",
                    fillcolor=fill,
                    fontcolor="#ffffff",
                    fontname="Helvetica",
                    fontsize="9",
                    width="1.8",
                    height="0.6",
                )

    for task in tasks:
        for dep in task.dependencies:
            dot.edge(dep, task.task_id)

    output_base = str(OUTPUT_SVG.with_suffix(""))
    try:
        dot.render(output_base, cleanup=True)
    except Exception:
        return None
    return str(OUTPUT_SVG)


def main() -> int:
    tasks = parse_epic(EPIC_PATH.read_text(encoding="utf-8"))
    markdown = build_markdown(tasks)
    OUTPUT_MD.write_text(markdown, encoding="utf-8")

    waves = compute_waves(tasks)
    svg_path = try_render_svg(tasks, waves)

    print(f"Generated markdown: {OUTPUT_MD}")
    if svg_path:
        print(f"Generated SVG: {svg_path}")
    else:
        print("Skipped SVG generation: graphviz Python package or Graphviz binary not available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
