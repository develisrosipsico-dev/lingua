# Referência gramatical e comparação de modelos

`examples/referencia_gramatical.json` amplia a referência anterior de 15 palavras e duas dimensões. Contém 33 alvos em 11 casos: o documento 8 enviado por Rodrigo, com seus cinco períodos, e dez controles curtos. Inclui classes, lemas, traços flexionais selecionados, relação de dependência e cabeça. As anotações foram propostas na revisão deste atendimento; ainda precisam da conferência de um especialista independente. Não constituem um corpus representativo nem uma estimativa de acurácia global.

Os controles distinguem a construção aspectual “passa a reconhecer” da passagem física “passou a ponte”, além de “continua orientando”, usos de subjuntivo, advérbios, “premissa”, “redefinem” e a ambiguidade de “guarda”. São controles de regressão criados após observar os problemas; não equivalem a um conjunto de teste independente coletado antes da revisão.

## Executar

Na raiz do repositório, com os modelos já instalados no ambiente Python:

```bash
PYTHONPATH=lingua python lingua/avaliacao_gramatical.py \
  --modelo pt_core_news_sm \
  --modelo pt_core_news_md \
  --saida-json /tmp/avaliacao-gramatical.json \
  --saida-markdown /tmp/avaliacao-gramatical.md
```

`--modelo` pode ser repetido. A avaliação recusa modelos ausentes e não baixa arquivos implicitamente. Usa o pipeline spaCy completo de cada pacote instalado, com texto original integral de cada caso; não chama o E5, não modifica o banco do Língua e não corrige as previsões. Os nomes, versões, componentes e tempos ficam registrados. O relatório salvo inclui a própria referência e seu SHA-256 canônico, permitindo identificar alterações posteriores na anotação.

A comparação de modelos mede as previsões brutas. Ela não equivale à avaliação integral dos agrupamentos oracionais do aplicativo, que ainda precisa dos testes específicos do detector e de novos textos revisados.

## Como ler os resultados

- POS e lema têm denominadores próprios por alvo explicitamente anotado.
- O conjunto de traços só acerta quando todas as chaves esperadas acertam. A saída também conta cada traço individualmente. Traços extras não anotados não são avaliados; um dicionário vazio não afirma ausência de flexão.
- Relação e cabeça são medidas separadamente. A medida conjunta exige que ambas acertem no mesmo alvo.
- A cabeça usa o intervalo original do token, permitindo comparar modelos sem depender dos índices internos de tokenização.
- Um alvo sem token com os limites exatos é contado como divergência de alinhamento e falha nas dimensões anotadas. Não se escolhe um subtoken nem se desloca o intervalo para aumentar o resultado.
- As locuções esperadas ficam em `grupos_verbais`, para testes do detector. Não recebem uma pontuação de dependência nem exigem transformar verbos aspectuais em `AUX`.

Os offsets são pontos de código Unicode, base zero e fim exclusivo, sempre no texto original de cada caso. Os arquivos de entrada, previsões e referência permanecem intactos durante a comparação.

## Resultado observado em 16/10/2026

O resultado real está em `examples/avaliacao_gramatical.json`, com versão legível em `docs/avaliacao_gramatical_resultado.md`. Foram usados spaCy 3.8.16 e os pacotes `pt_core_news_sm` e `pt_core_news_md`, ambos 3.8.0, em CPU no ambiente de nuvem.

| Dimensão nos alvos | sm | md |
| --- | --- | --- |
| Classes | 28/33 | 33/33 |
| Lemas | 26/33 | 26/33 |
| Conjunto de traços | 25/29 | 27/29 |
| Relação e cabeça | 23/32 | 30/32 |

O `md` melhorou classes e boa parte da árvore neste conjunto, mas ainda chamou “continue” de raiz no último período e ligou “convertida” a “auditorias”. Continuou errando “afirmem” e “examine” como lemas e o modo de “afirmem”. Os lemas das duas ocorrências de “guarda” pioraram no controle curto; portanto, não houve melhora total na contagem de lemas.

Não foi medido consumo de memória nem tempo no Mac. As durações registradas servem apenas para esta execução curta na nuvem. Também não foi avaliado o `lg`, pois ele não estava instalado. A escolha de perfil deve continuar explícita e versionada, preservando o histórico já produzido.

Antes de concluir que um modelo melhora a análise em geral, ampliar o conjunto com textos de outros gêneros, extensões e construções, revisar os alvos com outra pessoa e avaliar separadamente a extração e a classificação de orações.
