# Compatibilidade com os experimentos originais

[English](compatibility.md) · [Configuração](configuration.pt-BR.md) · [Aceite](acceptance.pt-BR.md)

## Referências auditadas

A comparação de 7 de outubro de 2026 usa os scripts Python dos repositórios abaixo.
Os notebooks permanecem históricos. Os hashes dos prompts e o cabeçalho original
estão em `app/tests/fixtures/upstream_contracts.json`, com regressão automatizada.

| RAG | Revisão original | Recuperação preservada |
| --- | --- | --- |
| Context | [a8fdf885](https://github.com/RSPLE/context-rag/tree/a8fdf8859f19946cc3d777176a45e0b744ea9e72) | Chunks 800/100; top-5 vetorial |
| Hybrid | [b4855bd0](https://github.com/RSPLE/hybrid-rag/tree/b4855bd001a7844ebd4014bacc11f0359e5d2011) | Chunks 800/100; BM25 0,4 + vetorial 0,6; `RETRIEVER_K=3` por padrão |
| Self | [3ad11ff8](https://github.com/RSPLE/self-rag/tree/3ad11ff823d169141c75ebfa018c3290ef395cd0) | Chunks 800/100; top-5; autocrítica e até um refinamento |
| Memory | [8b43b663](https://github.com/RSPLE/memory-augmented-rag/tree/8b43b663db76ecb9c90f1a214061b3670c81f953) | Chunks 800/100; ferramenta top-5; thread nova por pergunta |
| Graph | [cc80d331](https://github.com/RSPLE/graph-rag/tree/cc80d331c122dc091e6fcba7a361b0959bac5dcc) | Chunks 1000/200; vetor top-3; extração dos 20 primeiros chunks; profundidade 2 e até 40 nós relevantes |
| Knowledge | [62898387](https://github.com/RSPLE/knowledge-enhanced-rag/tree/62898387c0c72fbb6e1a7de75c049987ad8a58e8) | Chunks 800/100; top-5; Neo4j curado; últimos cinco pares de conversa compartilhados |

Os valores `800/100` e `1000/200` indicam tamanho e sobreposição dos chunks no
splitter original. Top-k e parâmetros intrínsecos do grafo foram preservados.
Não há limite adicional de bytes, corte de documentos ou deduplicação global
introduzidos pelo runner. O retriever híbrido mantém sua própria fusão original.

## CSV principal e arquivos auxiliares

Todos os seis pipelines geram `resultados/<rag>/<experiment_id>/results.csv`,
com delimitador `;`, UTF-8 BOM, sem índice de dataframe, nesta ordem exata:

```text
question;faithfulness;answer_relevancy;context_precision;context_recall;answer_response_time_seconds;answer_input_tokens;answer_output_tokens;answer_total_tokens
```

`<rag>-run-<repetição>_1.csv` contém os mesmos bytes, para consumidores que procuram
os nomes dos scripts originais. Não somar os dois arquivos como repetições distintas.
Cada linha corresponde a um caso com as quatro métricas válidas, na ordem do dataset.
Falhas, pendências e métricas ausentes não viram zeros. O CSV não é uma prova de
cobertura completa: conferir `summary.json` e seus denominadores antes de comparar.

| Arquivo | Uso |
| --- | --- |
| `results.csv` e cópia com nome original | Resultados principais para tabelas, gráficos e envio pelo Telegram |
| `results_detailed.csv` | IDs, referências, respostas, contagem de contextos e campos principais; privado |
| `checkpoint.json` | Respostas, contextos documentais, evidências adicionais e métricas por etapa; privado |
| `public_results.json` | IDs e campos públicos do CSV, sem respostas ou evidências |
| `summary.json` | Cobertura, médias, falhas, etapa e consumo conhecido/desconhecido |
| `usage.jsonl` e `budget.jsonl` | Consumo de preparação, geração, embeddings e juiz, separado dos tokens da resposta |

`answer_*` mede a etapa de resposta, preservando a finalidade original das colunas;
não é o custo total do experimento. Reuso de índices/recuperação evita trabalho e
altera tempos observados. O modo `evaluate` preserva tempo/tokens originais quando
presentes na exportação congelada; dados ausentes permanecem ausentes.

O gráfico local aceita a pasta de um experimento de qualquer um dos seis RAGs:

```bash
uv run --locked --project app/rags/context-rag python app/rags/context-rag/plot_graph.py --results-dir resultados/context-rag/EXP --output mean_metrics.png
```

O script lê somente arquivos com o nome original, rejeita métricas inválidas ou
ausentes e não converte NaN em zero. Agregar arquivos de várias repetições calcula
a média das médias; conferir mesma cobertura, protocolo e modelos antes de fazê-lo.

## Evidências avaliadas e evidências de geração

Os prompts originais de resposta, crítica/refinamento e extração auditados foram
preservados. No CSV principal, `contexts` contém os chunks documentais separados,
sem concatená-los em um único item. Isso importa para a
[precisão de contexto do RAGAS](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/),
que considera a relevância e a posição dos itens recuperados.

No Graph e Memory, a avaliação conserva a busca vetorial da pergunta original.
Quando a ferramenta já executou exatamente essa busca, seus documentos são
reutilizados. Se o agente reformulou a consulta, a recuperação documental original
continua sendo feita, como nos scripts auditados. O conteúdo das ferramentas é
registrado à parte em `generation_contexts` e `generation_evidence_metadata`.

No Knowledge, RAGAS recebe os trechos dos PDFs. Fatos, pré-requisitos e próximos
conceitos continuam no prompt do gerador e ficam salvos nos campos adicionais.
Assim, a fidelidade principal mede sustentação nos documentos; ela não mede
automaticamente toda a evidência do grafo ou do histórico. Misturar essas evidências
no CSV principal mudaria o protocolo original. Uma avaliação alternativa exige
outro experimento explicitamente identificado.

## Sessão do Knowledge e diferenças declaradas

A escolha para as 90 perguntas é uma sessão compartilhada, como no chatbot original.
O gerador recebe os últimos cinco pares pergunta/resposta e o novo prompt completo.
O checkpoint salva `conversation_history` e `generation_order` após cada geração.
Uma retomada restaura a última conversa salva; retentar apenas o juiz não acrescenta
um turno nem gera outra resposta. Falhas de geração podem alterar a ordem efetiva;
ela fica registrada. Para reprodução, usar a mesma sequência e declarar falhas.

O script upstream mantém o chatbot inclusive entre suas cinco rodadas internas.
Aqui `--repetition` cria experimentos independentes, com memória nova no começo de
cada experimento; não transporta histórico entre IDs. A sessão compartilhada vale
dentro do experimento de 90 perguntas e de suas retomadas. Portanto, isso não
reproduz a sequência inteira de cinco rodadas do script upstream em uma só sessão.

As mudanças de confiabilidade são explícitas: recuperação duplicada do Knowledge
foi removida; índices e extração do Graph podem ser reaproveitados; respostas e
métricas são salvas antes de novas tentativas. O Self usa o texto original da crítica,
sem contexto adicionado, mas normaliza `SIM`, `NAO` e `NÃO`; resposta de crítica fora
desse conjunto falha em vez de ser aceita silenciosamente. Knowledge exige Neo4j
quando configurado como `required`, sem continuar silenciosamente com grafo ausente.

Compatibilidade de CSV e prompts não garante notas idênticas aos artigos. Dataset
de 90 perguntas, corpus, modelos GLM/embeddings, versões, memória, repetições e
cobertura precisam constar no método publicado. O manifesto identifica essas
condições; não misturar resultados legados com novos experimentos sob o mesmo ID.

## Flags compartilhadas

As mesmas opções são reconhecidas por `main.py run`, `run-all`, `resume` e pelos
comandos `/executar` e `/retomar` do bot:

| Flag | Efeito |
| --- | --- |
| `--questions N`, `--limit N` | Até N tentativas por RAG; o bot aceita de 1 a 90 |
| `--provider openrouter\|openai` | Provedor de geração/juiz; embeddings seguem a configuração própria |
| `--mode full\|evaluate` | Geração + avaliação ou somente avaliação congelada |
| `--frozen CAMINHO` | Respostas salvas de um único RAG no modo `evaluate` |
| `--selection unresolved\|pending\|failed` | Casos elegíveis para a rodada |
| `--max-calls N` | Chamadas máximas por lote |
| `--max-seconds N` | Prazo do lote |
| `--question-timeout N` | Prazo da pergunta |
| `--repetition N` | Identidade independente para uma repetição científica |

Opções omitidas usam a `.env`. No bot, a ausência de quantidade admite até as 90
perguntas. `/executar all` enfileira os seis RAGs sequencialmente e cancela os demais
ao primeiro erro; comandos repetidos com o mesmo ID de evento não duplicam lotes.
O formato antigo `/executar RAG N [USD]` continua aceito. Não há teto obrigatório
em USD. `--frozen` no bot é restrito às pastas de importação documentadas.

```bash
uv run --locked python -m app run all --questions 1 --max-seconds 3600
uv run --locked python -m app resume knowledge-enhanced-rag EXP --questions 3
```

```text
/executar all --questions 1 --max-seconds 3600
/retomar knowledge-enhanced-rag EXP --questions 3
/resultado knowledge-enhanced-rag EXP
```

`EXP` é o hash completo do experimento. Alterar modelo, corpus, código ou repetição
na retomada é recusado; alterar quantidade, seleção ou prazo não muda o método.
O bot aceita comandos do usuário autorizado no privado; o canal recebe os resultados.
