"""Seed deterministic RAG examples into SQLite for local testing."""

from db_setup import seed_rag_examples


def main() -> None:
    inserted = seed_rag_examples()
    print(f"RAG example seed completed. Inserted: {inserted}")


if __name__ == "__main__":
    main()
