Estes três arquivos JSON são exportações fictícias congeladas do contrato da
etapa 09, produzidas em 10/10/2026 com anotações manuais e o adaptador de teste
determinístico. Eles contêm cinco períodos, suas janelas e o documento de dois
parágrafos. Não contêm dados de pessoas reais nem inferência de um modelo neural.

`exportacao_atual.json` preserva CRLF; `exportacao_atual_crlf.json` declara sua
normalização; `exportacao_atual_fragmentada.json` usa orçamento de 23 tokens.
Os caminhos de cache são metadados fictícios, sem acesso a arquivos externos.

Os testes do agente consomem esses arquivos e adaptam seus parâmetros com
`fixtures_importacao.py`. A suíte não importa nem executa o aplicativo Língua.
