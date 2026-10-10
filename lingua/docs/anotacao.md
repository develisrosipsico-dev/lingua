# Etapa 05: morfologia e vocabulário

O módulo `anotacao.py` recebe uma segmentação válida da etapa 04 e usa um modelo
treinado de português para acrescentar lemas, classes gramaticais e traços
morfológicos. A anotação preserva a segmentação recebida, seus identificadores,
textos e posições até o original. Cada execução gera um registro próprio com
`schema_version = "1.0.0"` e `etapa = "05_morfologia_vocabulario"`.

## Instalação e execução local

O ambiente usa spaCy **3.8.16** e o modelo **`pt_core_news_sm` 3.8.0**. O modelo
declara compatibilidade com spaCy `>=3.8.0,<3.9.0`. A dependência está fixada
em `requirements.txt` por uma URL HTTPS para a distribuição oficial no GitHub
de `explosion/spacy-models`.

Após atualizar sua cópia com Git, execute na pasta `lingua/`, no macOS ou Linux:

```bash
../.venv/bin/python -m pip install -r requirements.txt
```

No Windows:

```powershell
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Esse comando instala tanto as dependências quanto o modelo. O download inicial
do modelo tem aproximadamente 13 MB; a instalação requer internet. Durante
a análise, o modelo instalado executa localmente em CPU. O texto permanece
na máquina, e o processamento não faz solicitações de rede nem downloads
automáticos de modelos.

## Uso direto

```python
import json
from preparacao import preparar_texto
from segmentacao import segmentar_preparacao
from anotacao import anotar_segmentacao, validar_anotacao

preparacao = preparar_texto(
    "As crianças chegaram cedo. Eu fiquei.",
    documento_id="documento-exemplo",
)
segmentacao = segmentar_preparacao(preparacao)
registro = anotar_segmentacao(segmentacao)
relatorio = validar_anotacao(registro)

serializado = json.dumps(registro, ensure_ascii=False, allow_nan=False)
restaurado = json.loads(serializado)
validar_anotacao(restaurado)
```

- `anotar_segmentacao(segmentacao, *, anotacao_id=None, registrado_em=None)`:
  valida a fonte, executa o modelo e devolve o registro completo. É possível
  fornecer a identidade e uma data com fuso; sem esses argumentos, gera uma
  nova identidade e a data atual.
- `validar_anotacao(registro)`: confere a fonte preservada, vínculos, campos
  anotados e agrupamentos de vocabulário. Devolve um novo relatório, sem
  modificar a entrada. A leitura de registros existentes dispensa o modelo
  e o spaCy; a validação não reexecuta a previsão linguística.

`ErroAnotacao` é uma subclasse de `ValueError`. As falhas específicas incluem
`ErroModelo`, `ErroAlinhamento` e `ErroLimite`, permitindo que consumidores
diferenciem indisponibilidade do modelo, incompatibilidade de posições e
excesso dos limites de processamento.

## Alinhamento e preservação

O modelo trabalha sobre o texto completo da preparação preservada. Seus tokens
são conferidos contra os tokens da etapa 04 pela forma e pelas posições.
A anotação conserva os mesmos IDs, ordem, texto, vínculos com período/parágrafo
e intervalos `trabalho` e `original`. Uma divergência de alinhamento impede
a geração do resultado e produz um erro explícito.

As coordenadas continuam em pontos de código Unicode, base zero e fim exclusivo
`[inicio, fim)`. Comprimentos em bytes UTF-8 e unidades UTF-16 precisam ser
convertidos para esse formato, usando o texto correspondente. Consulte as
[convenções da preparação](preparacao.md) e a
[estrutura da segmentação](segmentacao.md).

Original, preparação e segmentação permanecem disponíveis na fonte preservada.
Se uma nova segmentação ou preparação for criada, anotações anteriores continuam
vinculadas ao resultado exato utilizado. Os hashes SHA-256 dos textos continuam
correspondendo aos conteúdos UTF-8 exatos registrados na etapa 03.

## Campos linguísticos e modelo

A lista `anotacoes_tokens` conserva os campos dos tokens e acrescenta:

| Campo | Conteúdo |
| --- | --- |
| `lema` | Lema previsto, ou `null` quando ausente. |
| `pos` | Classe gramatical universal, ou `null` quando ausente. |
| `tag` | Classe detalhada quando disponível, ou `null`. |
| `morfologia` | Objeto de traços morfológicos, ou `null`. |
| `campos_nao_resolvidos` | Lista dos campos sem valor produzido. |
| `origem` | Ferramenta, versão, modelo e componentes efetivos. |

Um lema igual à forma original é um resultado válido, comum em palavras que já
estão na forma de dicionário.

A morfologia é `null` quando o modelo não informa análise morfológica
(`has_morph` falso). Quando há análise, é um objeto produzido por `to_dict()`;
ele pode ser vazio (`{}`), o que é diferente de informação ausente. TAG também
pode ficar `null`, conforme os componentes efetivamente executados.

O processamento executa `tok2vec`, `morphologizer` e `lemmatizer`, cuja fábrica
é `trainable_lemmatizer`. Os componentes de parser, entidades e segmentação
estatística de frases ficam desativados, assim como o `attribute_ruler` vazio.
O contexto usado pela anotação é o texto de trabalho completo; os períodos
armazenados permanecem os definidos na etapa 04.

Os metadados registram a origem real no modelo, as versões efetivas e os
componentes executados. `origem` informa `ferramenta`, `versao`, `modelo` e
`componentes`. O processamento registra o modelo com nome, versão e
compatibilidade com spaCy, além dos componentes disponíveis, executados e
desativados. Este fluxo não fornece uma confiança numérica por
token; os campos não resolvidos permanecem explícitos. Representações neurais
internas usadas pelo modelo não são exportadas como embeddings no JSON.

As previsões são resultados de um modelo treinado. Abreviações, nomes próprios,
palavras raras, construções informais e contextos ambíguos podem exigir revisão.
A validação estrutural confirma a consistência do pacote; a avaliação manual
mede os acertos linguísticos em uma referência definida.

## Vocabulário do documento

O vocabulário reúne as ocorrências por lema e POS. Quando o lema está ausente,
usa a forma original e POS como alternativa. Cada entrada tem identidade e
ordem próprias, critério de agrupamento, formas encontradas, quantidade e
identificadores dos tokens correspondentes: `id`, `ordem`, `lema`, `pos`,
`criterio_agrupamento`, `forma_agrupamento`, `formas`, `quantidade` e `token_ids`.

- `criterio_agrupamento = "lema_pos"`: agrupa pelo lema e pela classe; a
  `forma_agrupamento` fica `null`.
- `criterio_agrupamento = "forma_pos"`: agrupa pela forma original e pela
  classe; a `forma_agrupamento` registra essa forma.

As formas distintas aparecem na ordem da primeira ocorrência, sem repetições.
O agrupamento exclui somente tokens cuja POS seja `PUNCT` ou `SYM`.
Stopwords continuam incluídas, assim como tokens com POS ausente ou `X`.
A aplicação preserva maiúsculas/minúsculas e a composição Unicode das formas,
sem normalização adicional; o lema previsto pelo modelo pode diferir da forma.

## Limites de processamento

Para conter o uso de memória do modelo local, a etapa 05 tem limites próprios:

- Até **50.000 pontos de código Unicode** no texto de trabalho.
- Até **10.000 tokens internos** do modelo, incluindo tokens espaciais.

Os limites são conferidos antes da inferência treinada. Entradas maiores
produzem `ErroLimite`; nenhum texto é truncado ou parcialmente anotado.
As etapas 03/04 aceitam textos maiores, portanto uma preparação ou segmentação
válida pode exceder a capacidade desta etapa. O número de tokens exibido na
etapa 04 exclui tokens espaciais e pode diferir da contagem interna usada
neste limite.

Esses limites controlam o tamanho da entrada; a disponibilidade de memória
e a instalação correta do modelo também são requisitos de execução.

## Interface, histórico e API HTTP

Na página do documento, selecione a preparação e a segmentação desejadas e
clique em **Anotar morfologia e vocabulário**. O resultado mostra tabelas
dos tokens e do vocabulário, oferece **Baixar anotações JSON** e mantém
um histórico das anotações anteriores.

O SQLite acrescenta registros na tabela `annotations`, vinculados à segmentação
exata. Originais e resultados anteriores permanecem intactos. O banco é local
e não é versionado nem enviado ao GitHub.

- `POST /envios/<id>/anotacoes`: com JSON, aceita `segmentacao_id`,
  `anotacao_id` e `registrado_em` opcionais e retorna HTTP 201 com o registro
  completo. Sem `segmentacao_id`, resolve a segmentação mais recente do
  documento naquele momento e preserva sua identidade exata.
- No mesmo POST, o formulário envia explicitamente a segmentação escolhida.
  A resposta HTTP 303 redireciona para o documento, selecionando a preparação,
  a segmentação e a anotação utilizadas.
- `GET /envios/<id>/anotacao.json?anotacao_id=<id>`: exporta a anotação
  escolhida como anexo JSON. Sem o parâmetro, entrega a anotação mais recente
  do documento.

Erros de argumentos retornam HTTP 400; fonte ou registro armazenado
inconsistente e identidade duplicada retornam 409. Falhas de alinhamento
retornam 422, excesso dos limites retorna 413 e falhas do modelo retornam 503.
Identificadores de documentos ou registros inexistentes retornam 404.

## Exemplos e avaliação

O [exemplo completo](../examples/anotacao.json) contém uma anotação gerada pelo
modelo e sua fonte rastreável. A
[referência manual](../examples/referencia_morfologia.json) e o
[guia de avaliação](avaliacao_morfologia.md) documentam as decisões da referência
e como executar o script [`avaliacao_morfologia.py`](../avaliacao_morfologia.py).
Os resultados calculados estão no
[relatório de avaliação](avaliacao_morfologia_resultado.md) e no
[pacote JSON da avaliação](../examples/avaliacao_morfologia.json).

Os testes técnicos verificam preservação, alinhamento, metadados, vocabulário,
histórico e tratamento de falhas. A avaliação linguística compara o modelo
com a referência manual; seus resultados pertencem ao conjunto avaliado e
não representam uma garantia de acerto para qualquer texto.
