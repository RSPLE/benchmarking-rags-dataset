# Dataset de avaliação

[English](README.md) · [Repositório](../../README.pt-BR.md)

`qa_dataset_90.json` é o dataset padrão dos seis pipelines, com 90 questões e
referências. `qa_dataset_90.en.json` é sua tradução em inglês, mantida separadamente.
Os arquivos foram movidos de `eval-dataset/` sem alterar seus conteúdos.

O carregamento comum fica em `app/benchmark/runner.py`. As referências servem à
avaliação e nunca substituem respostas geradas ou documentos recuperados.
O corpus de sete PDFs é independente deste dataset.

Os detalhes da construção original estão no [README principal](README.md).
A integração atual, a identidade dos experimentos e o contrato do CSV estão
no [protocolo de compatibilidade](../../app/docs/compatibility.pt-BR.md).
