import os

from neo4j import GraphDatabase


class KnowledgeGraph:
    def __init__(self):
        uri = os.environ.get("NEO4J_URI", "")
        usuario = os.environ.get("NEO4J_USERNAME", "neo4j")
        senha = os.environ.get("NEO4J_PASSWORD", "")

        if not uri or not senha:
            raise ValueError("Variáveis de ambiente NEO4J_URI e NEO4J_PASSWORD são obrigatórias.")

        self.driver = GraphDatabase.driver(uri, auth=(usuario, senha))

    def fechar(self):
        self.driver.close()

    def build_graph(self):
        print("Construindo o Knowledge Graph de Lógica de Programação...")

        with self.driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as sessao:
            sessao.run("MATCH (n:Conceito) DETACH DELETE n")

            conceitos = [
                ("Variáveis", 1),
                ("Tipos de Dados", 1),
                ("Operadores", 1),
                ("Entrada e Saída", 1),
                ("Condicionais IF/ELSE", 2),
                ("Switch/Case", 2),
                ("Loop FOR", 2),
                ("Loop WHILE", 2),
                ("Loop DO-WHILE", 2),
                ("Funções", 3),
                ("Vetores/Arrays", 3),
                ("Matrizes", 3),
                ("Recursão", 4),
                ("Ordenação", 4),
            ]

            for nome, dificuldade in conceitos:
                sessao.run(
                    "CREATE (:Conceito {nome: $nome, dificuldade: $dificuldade})",
                    nome=nome,
                    dificuldade=dificuldade,
                )

            relacionamentos = [
                ("Condicionais IF/ELSE", "REQUER", "Variáveis"),
                ("Condicionais IF/ELSE", "REQUER", "Operadores"),
                ("Switch/Case", "REQUER", "Variáveis"),
                ("Loop FOR", "REQUER", "Variáveis"),
                ("Loop FOR", "REQUER", "Operadores"),
                ("Loop WHILE", "REQUER", "Variáveis"),
                ("Loop WHILE", "REQUER", "Operadores"),
                ("Loop DO-WHILE", "REQUER", "Variáveis"),
                ("Loop DO-WHILE", "REQUER", "Operadores"),
                ("Funções", "REQUER", "Variáveis"),
                ("Funções", "REQUER", "Tipos de Dados"),
                ("Vetores/Arrays", "REQUER", "Variáveis"),
                ("Vetores/Arrays", "REQUER", "Loop FOR"),
                ("Matrizes", "REQUER", "Vetores/Arrays"),
                ("Recursão", "REQUER", "Funções"),
                ("Ordenação", "REQUER", "Vetores/Arrays"),
                ("Ordenação", "REQUER", "Loop FOR"),
                ("Entrada e Saída", "REQUER", "Variáveis"),
                ("Entrada e Saída", "REQUER", "Tipos de Dados"),
                ("Condicionais IF/ELSE", "É_UM_TIPO_DE", "Condicionais IF/ELSE"),
                ("Switch/Case", "É_UM_TIPO_DE", "Condicionais IF/ELSE"),
                ("Loop FOR", "É_UM_TIPO_DE", "Loop FOR"),
                ("Loop WHILE", "É_UM_TIPO_DE", "Loop WHILE"),
                ("Loop DO-WHILE", "É_UM_TIPO_DE", "Loop DO-WHILE"),
                ("Loop FOR", "SIMILAR_A", "Loop WHILE"),
                ("Loop WHILE", "SIMILAR_A", "Loop DO-WHILE"),
                ("Loop FOR", "SIMILAR_A", "Loop DO-WHILE"),
                ("Condicionais IF/ELSE", "SIMILAR_A", "Switch/Case"),
                ("Vetores/Arrays", "SIMILAR_A", "Matrizes"),
                ("Variáveis", "LEVA_A", "Tipos de Dados"),
                ("Tipos de Dados", "LEVA_A", "Operadores"),
                ("Operadores", "LEVA_A", "Entrada e Saída"),
                ("Entrada e Saída", "LEVA_A", "Condicionais IF/ELSE"),
                ("Condicionais IF/ELSE", "LEVA_A", "Loop FOR"),
                ("Loop FOR", "LEVA_A", "Loop WHILE"),
                ("Loop WHILE", "LEVA_A", "Loop DO-WHILE"),
                ("Loop DO-WHILE", "LEVA_A", "Funções"),
                ("Funções", "LEVA_A", "Vetores/Arrays"),
                ("Vetores/Arrays", "LEVA_A", "Matrizes"),
                ("Matrizes", "LEVA_A", "Recursão"),
                ("Recursão", "LEVA_A", "Ordenação"),
                ("Switch/Case", "LEVA_A", "Loop FOR"),
            ]

            for origem, tipo, destino in relacionamentos:
                if origem == destino:
                    continue
                query = (
                    f"MATCH (a:Conceito {{nome: $origem}}), (b:Conceito {{nome: $destino}}) "
                    f"CREATE (a)-[:{tipo}]->(b)"
                )
                sessao.run(query, origem=origem, destino=destino)

        print("Knowledge Graph construído com sucesso!")

    def get_prerequisites(self, conceito: str) -> list[str]:
        with self.driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as sessao:
            resultado = sessao.run(
                "MATCH (a:Conceito {nome: $nome})-[:REQUER]->(b:Conceito) "
                "RETURN b.nome AS prerequisito",
                nome=conceito,
            )
            return [registro["prerequisito"] for registro in resultado]

    def get_related_facts(self, conceito: str) -> str:
        with self.driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as sessao:
            resultado = sessao.run(
                "MATCH (a:Conceito {nome: $nome})-[r]->(b:Conceito) "
                "RETURN type(r) AS tipo, b.nome AS relacionado, b.dificuldade AS dificuldade",
                nome=conceito,
            )
            registros = resultado.data()

        if not registros:
            return f"Não foram encontrados relacionamentos para o conceito '{conceito}'."

        linhas = [f"Fatos sobre '{conceito}':"]
        for reg in registros:
            tipo = reg["tipo"].replace("_", " ")
            linhas.append(
                f"- {conceito} {tipo} {reg['relacionado']} (dificuldade: {reg['dificuldade']})"
            )
        return "\n".join(linhas)

    def get_next_concepts(self, conceito: str) -> list[str]:
        with self.driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as sessao:
            resultado = sessao.run(
                "MATCH (a:Conceito {nome: $nome})-[:LEVA_A]->(b:Conceito) "
                "RETURN b.nome AS proximo ORDER BY b.dificuldade",
                nome=conceito,
            )
            return [registro["proximo"] for registro in resultado]

    def find_concept(self, consulta: str) -> str | None:
        consulta_lower = consulta.lower()

        mapeamento = {
            "variável": "Variáveis",
            "variave": "Variáveis",
            "tipo de dado": "Tipos de Dados",
            "tipos de dados": "Tipos de Dados",
            "tipo": "Tipos de Dados",
            "operador": "Operadores",
            "entrada": "Entrada e Saída",
            "saída": "Entrada e Saída",
            "leitura": "Entrada e Saída",
            "impressão": "Entrada e Saída",
            "if": "Condicionais IF/ELSE",
            "else": "Condicionais IF/ELSE",
            "condicional": "Condicionais IF/ELSE",
            "switch": "Switch/Case",
            "case": "Switch/Case",
            "for": "Loop FOR",
            "loop for": "Loop FOR",
            "while": "Loop WHILE",
            "loop while": "Loop WHILE",
            "do-while": "Loop DO-WHILE",
            "do while": "Loop DO-WHILE",
            "função": "Funções",
            "funções": "Funções",
            "funcao": "Funções",
            "vetor": "Vetores/Arrays",
            "array": "Vetores/Arrays",
            "lista": "Vetores/Arrays",
            "matriz": "Matrizes",
            "matrizes": "Matrizes",
            "recursão": "Recursão",
            "recursao": "Recursão",
            "recursiv": "Recursão",
            "ordenação": "Ordenação",
            "ordenacao": "Ordenação",
            "sort": "Ordenação",
        }

        for chave, conceito in mapeamento.items():
            if chave in consulta_lower:
                return conceito

        return None
