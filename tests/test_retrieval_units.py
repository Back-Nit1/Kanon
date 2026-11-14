from kanon.retrieval import rrf, tokenize


def test_tokenizer_keeps_regulatory_identifiers_whole():
    toks = tokenize("Per 10 CFR 50.59 and RG 1.187, see MP-033.05 Rev 28.")
    assert {"50.59", "1.187", "mp-033.05"} <= set(toks)


def test_rrf_rewards_agreement_between_retrievers():
    # 'b' is 2nd in both lists; 'a' is 1st in one and absent from the other.
    scores = rrf([["a", "b"], ["c", "b"]])
    assert scores["b"] > scores["a"] and scores["b"] > scores["c"]
    assert abs(scores["a"] - 1 / 61) < 1e-12
