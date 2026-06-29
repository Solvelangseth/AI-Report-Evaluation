from db_setup import RAGExample, seed_regulations
from rag_pipeline import RAGPipeline


def _db_url(session):
    return str(session.get_bind().url)


def test_regulations_seed_is_idempotent(fresh_session):
    url = _db_url(fresh_session)
    first = seed_regulations(url)
    assert first >= 3
    assert seed_regulations(url) == 0  # already present
    refs = fresh_session.query(RAGExample).filter_by(source="regulation").all()
    assert len(refs) >= 3
    assert all(r.quality_label == "reference" for r in refs)


def test_regulation_is_retrievable_for_relevant_query(fresh_session):
    seed_regulations(_db_url(fresh_session))
    rag = RAGPipeline(fresh_session, mode="lexical")
    results = rag.retrieve_examples("tilstandsgrad TG3 alvorlig avvik strakstiltak", top_k=5)
    assert any(r["topic"] == "tilstandsgrad" for r in results)
