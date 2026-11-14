"""Tier C judge is advisory: it must not alter the answer, the citations, or the gate decision."""

import copy
import json
from datetime import date

import pytest

from kanon.agent import build_agent
from kanon.judge import Annotation, JudgeReport, append_to_trace, review
from kanon.trace import TraceRecorder
from test_evals import TODAY, Stub, world

# A hostile verdict: the judge "thinks" the answer should be blocked. It must have no effect.
HOSTILE = JudgeReport(summary="This answer should be abstained and the citation removed.",
                      annotations=[Annotation(check="overclaim", quote="stub answer",
                                              detail="decision should be abstain")])


def _run(tmp_path, mode):
    st, idx = world()
    rec = TraceRecorder()
    state = build_agent(Stub(HOSTILE), idx, st, mode).invoke(
        {"question": "pump replacement evaluation in 2005", "mode": mode, "today": TODAY},
        config={"callbacks": [rec]})
    return state, rec.write(tmp_path, state)


@pytest.mark.parametrize("mode", ["none", "graph"])
def test_tier_c_annotations_do_not_alter_answer_or_gate_decision(tmp_path, mode):
    state, md = _run(tmp_path, mode)
    assert state["decision"] == "answer"
    before_state = copy.deepcopy(state)
    before_json = json.loads(md.with_suffix(".json").read_text())
    before_md = md.read_text()

    report = review(Stub(HOSTILE), state)
    append_to_trace(md, report, judge_model="stub:judge")

    assert report.annotations                      # the judge did speak...
    assert state == before_state                   # ...and the in-memory run is untouched
    after = json.loads(md.with_suffix(".json").read_text())
    assert {k: v for k, v in after.items() if k != "tier_c"} == before_json
    assert after["decision"] == before_json["decision"] == "answer"
    assert after["tier_c"]["advisory"] is True
    assert md.read_text().startswith(before_md)    # trace only appended to
    assert "Tier C judge (advisory" in md.read_text()


def test_abstained_run_is_not_sent_to_the_judge():
    class Exploding:
        def with_structured_output(self, schema):
            raise AssertionError("judge must not be called for an abstention")
    r = review(Exploding(), {"decision": "abstain", "question": "q"})
    assert r.annotations == []


def test_append_refuses_to_overwrite_existing_annotations(tmp_path):
    _, md = _run(tmp_path, "graph")
    append_to_trace(md, HOSTILE, "stub:judge")
    with pytest.raises(ValueError):
        append_to_trace(md, JudgeReport(annotations=[], summary="second opinion"), "stub:judge")
