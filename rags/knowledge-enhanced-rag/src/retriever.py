"""
Módulo do retriever KE-RAG.

Combina a busca semântica no Chroma com consultas ao Knowledge Graph
para enriquecer o contexto das respostas.
"""

from src.ingestion import load_or_create_index
from src.knowledge_graph import KnowledgeGraph


class KERagRetriever:
    """
    Retriever que combina Chroma (busca semântica) com Knowledge Graph.
    """

    def __init__(self, knowledge_graph: KnowledgeGraph | None = None):
        """
        Inicializa o retriever carregando o índice Chroma e o Knowledge Graph.

        Args:
            knowledge_graph: Instância do KnowledgeGraph. Se None, tenta criar uma.
        """
        self.indice = load_or_create_index()
        self.kg = knowledge_graph

        if self.kg is None:
            try:
                self.kg = KnowledgeGraph()
            except Exception as e:
                print(f"Aviso: Não foi possível conectar ao Knowledge Graph: {e}")
                self.kg = None

    def retrieve(self, pergunta: str) -> dict:
        """
        Realiza a recuperação KE-RAG combinando Chroma e Knowledge Graph.

        1. Busca os top-5 chunks relevantes no Chroma
        2. Tenta identificar o conceito principal da pergunta
        3. Consulta o Knowledge Graph para obter fatos relacionados
        4. Retorna um dicionário com docs, kg_facts, prerequisites e next_concepts

        Args:
            pergunta: Pergunta do usuário.

        Returns:
            Dicionário com:
                - docs: Lista de Documents recuperados do Chroma
                - kg_facts: Fatos do Knowledge Graph sobre o conceito
                - prerequisites: Pré-requisitos do conceito identificado
                - next_concepts: Próximos conceitos sugeridos
        """
        # 1. Busca semântica no Chroma
        docs = self.indice.similarity_search(pergunta, k=5)

        # 2. Identifica o conceito principal da pergunta
        conceito = None
        kg_facts = ""
        prerequisites = []
        next_concepts = []

        if self.kg is not None:
            try:
                conceito = self.kg.find_concept(pergunta)

                if conceito:
                    # 3. Consulta o Knowledge Graph
                    kg_facts = self.kg.get_related_facts(conceito)
                    prerequisites = self.kg.get_prerequisites(conceito)
                    next_concepts = self.kg.get_next_concepts(conceito)
            except Exception as e:
                print(f"Aviso: Erro ao consultar Knowledge Graph: {e}")

        return {
            "docs": docs,
            "kg_facts": kg_facts,
            "prerequisites": prerequisites,
            "next_concepts": next_concepts,
        }
