from rag_pipeline import RAGPipeline


def test_lexical_ranks_relevant_example_first(fresh_session):
    rag = RAGPipeline(fresh_session, mode="lexical")
    results = rag.retrieve_examples("Fuktmåling på bad viser 18 % i vegg ved sluk", top_k=2)
    assert results
    assert results[0]["score"] >= results[-1]["score"]


def test_build_context_handles_empty():
    assert "No matching examples" in RAGPipeline.build_context([])


class StubEmbedder:
    """Maps text to a 2-D vector: bathroom-ish on one axis, basement on the other."""

    def embed(self, texts):
        out = []
        for t in texts:
            low = t.lower()
            # Non-zero on both axes so neither example scores exactly 0
            # (a 0 score would be filtered out of the results).
            out.append([1.0, 0.3] if ("bad" in low or "sluk" in low) else [0.3, 1.0])
        return out


def test_semantic_ranks_by_embedding(fresh_session):
    rag = RAGPipeline(fresh_session, embedder=StubEmbedder(), mode="semantic")
    results = rag.retrieve_examples("Vannskade på bad, fukt ved sluk", top_k=2)
    assert results
    # The bathroom/measurement ("clean") example must outrank the basement one.
    assert results[0]["quality_label"] == "clean"
    assert results[0]["score"] > results[-1]["score"]


def test_semantic_falls_back_to_lexical_without_embedder(fresh_session):
    # mode="semantic" but no embedder available → lexical results, no crash.
    rag = RAGPipeline(fresh_session, embedder=None, mode="semantic")
    results = rag.retrieve_examples("fukt i kjeller", top_k=2)
    assert results


def test_embeddings_are_cached(fresh_session):
    from db_setup import RAGExample
    RAGPipeline(fresh_session, embedder=StubEmbedder(), mode="semantic")
    assert all(e.embedding for e in fresh_session.query(RAGExample).all())
