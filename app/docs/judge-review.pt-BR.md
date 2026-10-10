# Justificativas do juiz e reavaliação de métricas

Cada avaliação nova salva `judge_responses.jsonl` no diretório do experimento.
O registro inclui questão, métrica, identificador da tentativa, data, configuração
do juiz, versão do RAGAS, entrada avaliada, hash das evidências, saída textual do
modelo e resultados estruturados dos prompts. Quando o RAGAS retorna `reason`,
essa justificativa fica associada à afirmação e ao veredito. Métricas que não
produzem justificativa textual conservam a saída efetivamente retornada; nenhuma
justificativa é inventada.

A resposta textual é persistida antes da interpretação do JSON. Assim, uma falha
de interpretação não elimina a resposta recebida. Cada evento é gravado com
`fsync`, com permissão `0600`; mensagens de erro e conteúdo são filtrados para
remover credenciais. Raciocínio privado do modelo, cabeçalhos e objetos de cliente
não são registrados. O arquivo entra no backup privado e na importação autenticada
do painel, mas não no ZIP público enviado ao Telegram.

Avaliações antigas sem registro do juiz não permitem recuperar retroativamente
sua justificativa. Uma reavaliação gera outra resposta, com outra identidade.
Questões sem resposta, gabarito ou contextos salvos são recusadas antes de chamar
a API. Isso inclui resultados legados que preservaram somente nota e resposta.

## No painel

Abra **Juiz e reavaliação**, selecione arquitetura, experimento e métrica. A tabela
permite filtrar notas zero, falhas na métrica, diferenças de contexto e ausência
de registro do juiz. O indicador de contextos diferentes detecta trechos avaliados
ausentes do texto de geração salvo; sozinho, ele não confirma erro do juiz.

Selecione uma questão para inspecionar resposta, gabarito, contextos e tentativas
do juiz. As justificativas estruturadas, a saída textual e a configuração estão
disponíveis para leitura e download. Selecione uma ou várias questões e clique
**Reavaliar métrica selecionada**. O botão de seleção em lote inclui apenas as
questões elegíveis do filtro atual. O executor ocupado bloqueia novos pedidos.

Há duas políticas explícitas:

- **Contextos da avaliação original**: usa a mesma resposta, gabarito e contextos
  salvos, para conferir a nota daquela evidência.
- **Evidências usadas na resposta**: usa `generation_contexts` já salvos, incluindo
  ferramentas/grafo quando presentes. É outra política de avaliação, identificada
  separadamente; não altera a recuperação original nem reconstrói o grafo.

As reavaliações executam apenas a métrica selecionada. Não geram outra resposta,
não repetem as demais métricas e não reconstroem índices. As notas novas ficam em
`judge_reviews.jsonl`, ao lado da nota original, política, hashes, erros e referência
à saída do juiz. Checkpoint, CSV e médias originais permanecem intactos: comparar
políticas não deve sobrescrever o experimento original. A retomada normal de uma
pipeline com falha continua usando seu checkpoint original.

Cada pedido pode consumir a API do juiz e, dependendo da métrica, embeddings de
avaliação. O consumo usa o mesmo diário e os limites existentes. Reenviar o mesmo
identificador não repete chamadas. Depois de uma interrupção, um novo pedido
explícito é necessário; as tentativas parciais permanecem consultáveis.
Falhas de credencial, crédito ou orçamento interrompem o lote; três erros
consecutivos da mesma categoria também interrompem novas chamadas.

## Pela CLI

Use o ID completo de um experimento local com checkpoint v2:

```bash
uv run python -m app rejudge graph-rag ID_COMPLETO_DO_EXPERIMENTO \
  --metric context_recall --question-ids Q005,Q010 \
  --evidence original --reason 'Revisar notas e justificativas'
```

Para avaliar a evidência da geração, use `--evidence generation`. O processo roda
no ambiente travado do RAG e permite apenas as credenciais de avaliação.

O controle reconhece o comando equivalente:

```text
/reavaliar graph-rag ID_COMPLETO_DO_EXPERIMENTO --metric context_recall --question-ids Q005,Q010 --evidence original
```

O diário detalhado permanece privado; os eventos públicos da fila não incluem
respostas, contextos ou justificativas. Instalar este código não recupera registros
de processos já iniciados com uma versão anterior.
