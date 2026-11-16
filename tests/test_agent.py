

def test_precedent_only_question_does_not_need_requirement_citation():
    from kanon.agent import _needs_requirements
    assert not _needs_requirements("Has a licensee been cited for an inadequate 10 CFR 50.59 evaluation?", True)
    # requirement wording keeps the check on, even with 'violation' in the question
    assert _needs_requirements("Which requirement governs a violation of 10 CFR 50.59?", False)
    assert _needs_requirements("In 2001, how did 10 CFR 50.59 define a change?", False)
