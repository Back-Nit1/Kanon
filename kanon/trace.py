"""Per-query trace: the traceability chain, written for a human reviewer.

A BaseCallbackHandler sees every tool call (with arguments and output) and every custom event
the graph dispatches (authority resolutions, the gate decision). We record those as they happen,
then render one Markdown file a reviewer can read top to bottom, plus the same data as JSON for
the eval harness.

Why a callback rather than logging from inside each node: the callback sees exactly what
LangGraph executed — a node can't forget to log, and the trace can't drift from the run.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, (list, tuple)):
        return [_jsonable(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj if isinstance(obj, (str, int, float, bool, type(None))) else str(obj)


class TraceRecorder(BaseCallbackHandler):
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._open_tools: dict[str, dict] = {}

    def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kw) -> None:
        self._open_tools[str(run_id)] = {"type": "tool_call", "tool": serialized.get("name"),
                                         "args": _jsonable(inputs) if inputs else input_str}

    def on_tool_end(self, output, *, run_id, **kw) -> None:
        event = self._open_tools.pop(str(run_id), {"type": "tool_call", "tool": "?"})
        out = _jsonable(output)
        if isinstance(out, list) and out and isinstance(out[0], dict) and "id" in out[0]:
            # retrieved chunks: keep id + citation fields, not the full text
            event["chunks"] = [{k: c.get(k) for k in ("id", "doc_id", "revision", "page", "status",
                                                      "section", "score")} for c in out]
        else:
            event["output"] = out
        self.events.append(event)

    def on_tool_error(self, error, *, run_id, **kw) -> None:
        event = self._open_tools.pop(str(run_id), {"type": "tool_call", "tool": "?"})
        event["error"] = repr(error)
        self.events.append(event)

    def on_custom_event(self, name, data, *, run_id, **kw) -> None:
        self.events.append({"type": name, **_jsonable(data)})

    # ---- output ---------------------------------------------------------------------------

    def write(self, out_dir: Path, state: dict) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # run id suffix: two queries in the same second must not overwrite each other's trace
        base = out_dir / f"{stamp}-{uuid.uuid4().hex[:6]}-{state.get('decision', 'unknown')}"
        record = {"question": state["question"], "mode": state["mode"],
                  "query_kind": state.get("query_kind"), "as_of": _jsonable(state.get("as_of")),
                  "decision": state.get("decision"), "events": self.events,
                  "violations": _jsonable(state.get("violations", [])),
                  "citations": _jsonable(state.get("citations", [])),
                  "final": state.get("final")}
        base.with_suffix(".json").write_text(json.dumps(record, indent=2))
        base.with_suffix(".md").write_text(render_markdown(record))
        return base.with_suffix(".md")


def render_markdown(r: dict) -> str:
    lines = [f"# Kanon trace — {r['decision'].upper() if r['decision'] else '?'}", "",
             f"**Question:** {r['question']}", "",
             f"- Mode: `{r['mode']}`", f"- Query kind: `{r['query_kind']}`",
             f"- Authority date (as_of): `{r['as_of']}`", ""]
    lines += ["## 1. Tool calls", ""]
    for e in (e for e in r["events"] if e["type"] == "tool_call"):
        lines.append(f"### `{e['tool']}`  args: `{json.dumps(e.get('args'))}`")
        if "error" in e:
            lines.append(f"- ERROR: {e['error']}")
        for c in e.get("chunks", []):
            lines.append(f"- `{c['id']}` — {c['doc_id']} Rev {c['revision']}, p.{c['page']}, "
                         f"status={c['status']}, §{c['section'] or '—'}")
        if "output" in e:
            lines.append(f"```json\n{json.dumps(e['output'], indent=2)[:2000]}\n```")
        lines.append("")
    lines += ["## 2. Authority resolutions (deterministic lookup)", ""]
    for e in (e for e in r["events"] if e["type"] == "authority_resolution"):
        mark = "GOVERNS" if e["governs"] else "DOES NOT GOVERN"
        lines.append(f"- **{e['doc_id']} Rev {e['revision']}** on {e['as_of']}: {mark} "
                     f"(recorded status: {e['status']})")
        for edge in e.get("chain", []):
            lines.append(f"  - edge: {edge['from_doc']} Rev {edge['from_rev']} —{edge['edge_type']}→ "
                         f"{edge['to_doc']} Rev {edge['to_rev']}, effective {edge['valid_from']} "
                         f"[{edge['confidence']}] — evidence: “{edge['evidence'][:300]}”")
        for edge in e.get("unresolved", []):
            lines.append(f"  - UNRESOLVED: {edge['from_doc']} Rev {edge['from_rev']} → "
                         f"{edge['to_doc']} Rev {edge['to_rev']} — “{edge['evidence'][:300]}”")
    lines += ["", "## 3. Gate decision (Tier B, deterministic)", ""]
    for e in (e for e in r["events"] if e["type"] == "gate_decision"):
        lines.append(f"- Decision: **{e['decision']}** (enforcing: {e['enforcing']})")
    for v in r["violations"]:
        lines.append(f"- violation `{v['rule']}`: {v['doc_id']} Rev {v['revision']} — {v['detail']}")
    if not r["violations"]:
        lines.append("- no violations")
    lines += ["", "## 4. Citations", ""]
    lines += [f"- {c['doc_id']} Rev {c['revision']}, p.{c['page']}"
              + (f", §{c['section']}" if c.get("section") else "") for c in r["citations"]] or ["- none"]
    lines += ["", "## 5. Output", "", r["final"] or ""]
    return "\n".join(lines) + "\n"
