import os

from src.ingestion import load_or_create_index
from src.knowledge_graph import KnowledgeGraph


class KERagRetriever:
    def __init__(self, knowledge_graph: KnowledgeGraph | None = None):
        self.indice = load_or_create_index()
        self.kg = knowledge_graph

        self.kg_mode = os.getenv("BENCHMARK_KG_MODE", "required")
        if self.kg_mode not in {"required", "disabled"}:
            raise ValueError("BENCHMARK_KG_MODE must be required or disabled")
        if self.kg_mode == "disabled":
            self.kg = None
        else:
            self.kg = self.kg or KnowledgeGraph()
            self.kg.driver.verify_connectivity()
            with self.kg.driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
                record = session.run("MATCH (n:Conceito) RETURN count(n) AS count").single()
                if not record or not record["count"]:
                    raise RuntimeError("Knowledge graph is empty")

    def retrieve(self, pergunta: str) -> dict:

        docs = self.indice.similarity_search(pergunta, k=5)

        conceito = None
        kg_facts = ""
        prerequisites = []
        next_concepts = []

        if self.kg is not None:
            try:
                conceito = self.kg.find_concept(pergunta)

                if conceito:
                    kg_facts = self.kg.get_related_facts(conceito)
                    prerequisites = self.kg.get_prerequisites(conceito)
                    next_concepts = self.kg.get_next_concepts(conceito)
            except Exception as e:
                raise RuntimeError("Knowledge graph query failed") from e

        return {
            "docs": docs,
            "kg_mode": self.kg_mode,
            "kg_facts": kg_facts,
            "prerequisites": prerequisites,
            "next_concepts": next_concepts,
        }
