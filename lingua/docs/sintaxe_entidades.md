# Etapa 06: sintaxe e entidades

O módulo `sintaxe_entidades.py` recebe uma anotação válida da etapa 05 e
acrescenta relações de dependência sintática e entidades nomeadas. Ele preserva
a entrada completa como registro de origem, sem modificar textos, hashes,
metadados, lemas, classes, morfologia, separadores ou posições. Cada execução
produz um registro próprio com `schema_version = "1.0.0"` e
`etapa = "06_sintaxe_entidades"`.

## Instalação e execução local

As etapas 05 e 06 usam spaCy **3.8.16** e o modelo português
**`pt_core_news_sm` 3.8.0**, compatível com spaCy `>=3.8.0,<3.9.0`.
As versões estão fixadas em `requirements.txt`, incluindo a distribuição
oficial do modelo. Na pasta `lingua/`, no macOS ou Linux:

```bash
git pull --ff-only origin organizacao/projetos-20261010
../.venv/bin/python -m pip install -r requirements.txt
../.venv/bin/python app.py
```

No Windows:

```powershell
git pull --ff-only origin organizacao/projetos-20261010
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
..\.venv\Scripts\python.exe app.py
```

A instalação inicial requer internet. O modelo já instalado na etapa 05 é
reutilizado; a etapa 06 não baixa outro modelo. Durante a análise, o
processamento executa localmente em CPU e dispensa serviços externos.
O modelo é carregado uma vez por instância de processamento e reutilizado
nas execuções seguintes. Ausência ou incompatibilidade do modelo produz
um erro explícito.

## Uso direto

```python
import json
from preparacao import preparar_texto
from segmentacao import segmentar_preparacao
from anotacao import anotar_segmentacao
from sintaxe_entidades import (
    analisar_sintaxe_entidades,
    validar_analise_sintaxe_entidades,
)

preparacao = preparar_texto(
    "Eu entreguei uma nota.",
    documento_id="documento-exemplo",
)
segmentacao = segmentar_preparacao(preparacao)
anotacao = anotar_segmentacao(segmentacao)
analise = analisar_sintaxe_entidades(anotacao)
relatorio = validar_analise_sintaxe_entidades(analise)

serializado = json.dumps(analise, ensure_ascii=False, allow_nan=False)
restaurado = json.loads(serializado)
validar_analise_sintaxe_entidades(restaurado)
```

- `analisar_sintaxe_entidades(anotacao, *, analise_id=None, registrado_em=None)`:
  valida a fonte, executa o modelo e devolve a análise completa. O chamador
  pode fornecer a identidade e a data com fuso; quando omitidas, a função
  gera uma identidade nova e usa a data atual.
- `validar_analise_sintaxe_entidades(registro)`: verifica a fonte preservada,
  a cobertura dos tokens, as árvores, as entidades e os vínculos. Devolve
  um novo relatório sem modificar o registro. A leitura de arquivos existentes
  não precisa carregar o modelo nem reexecutar a previsão linguística.

`ErroAnalise` deriva de `ValueError`. `ErroEntrada` identifica uma fonte
inválida; `ErroModelo`, modelo indisponível, incompatível ou falha de execução;
`ErroAlinhamento`, divergências entre tokens ou períodos; `ErroLimite`, excesso
dos limites; e `ErroPrevisao`, resultado do modelo incompatível com o contrato.
Uma falha impede a entrega de um registro marcado como pronto.

## Texto, documento interno e períodos

A etapa 05 conserva seu registro JSON, mas não armazena o `Doc` interno do
spaCy. A etapa 06 reconstrói esse documento mantendo o texto literal e os
tokens internos de espaços. A forma, a ordem e o intervalo de cada token
de conteúdo precisam coincidir exatamente com os tokens da etapa 04.
Espaços repetidos, tabulações, CRLF e LF permanecem representados; o texto
não é refeito juntando palavras com um único espaço.

Os atributos morfológicos da etapa 05 são repostos no documento interno.
A análise executa `tok2vec`, `parser` e `ner`; `morphologizer`, `lemmatizer`,
`senter` e `attribute_ruler` ficam desativados. Nenhuma anotação morfológica
anterior é sobrescrita. Os metadados registram as versões reais, os
componentes disponíveis, executados e desativados e a configuração utilizada.
Representações neurais internas não são exportadas como embeddings.

Os limites dos períodos da etapa 04 são informados antes do parser e
conferidos depois dele nos tokens de conteúdo. O spaCy pode representar
espaços no início do texto como uma sentença interna; esse marcador espacial
não cria um período de conteúdo na saída. Uma mudança de fronteira em tokens
de conteúdo produz erro. Uma cabeça sintática que aponte para um separador
também produz erro, pois não corresponde a um `token_id` de conteúdo.

Entradas válidas sem tokens de conteúdo produzem listas vazias de sintaxe
e entidades, sem executar a inferência. Isso é permitido no módulo; o
formulário de registro de textos mantém a regra própria de recusar envios
vazios.

## Relações sintáticas

`sintaxe` contém uma anotação por token da segmentação, na mesma ordem.
Cada anotação inclui os vínculos e intervalos preservados, além da previsão:

| Campo | Conteúdo |
| --- | --- |
| `token_id` | Identificador original do token. |
| `periodo_id`, `paragrafo_id` | Unidades de origem. |
| `texto`, `trabalho`, `original` | Texto e intervalos exatos do token. |
| `dependencia` | Rótulo produzido por `token.dep_`. |
| `cabeca_token_id` | ID correspondente à cabeça indicada por `token.head`. |
| `origem` | Ferramenta, versão, modelo e componentes efetivos. |

A convenção da raiz é **`dependencia = "ROOT"` e cabeça no próprio token**.
Cada período de conteúdo precisa ter uma raiz. Todas as cabeças devem
existir no mesmo período e parágrafo, e cada caminho deve alcançar a raiz
sem ciclos, exceto a autorreferência da raiz.

Os rótulos e as ligações vêm da execução do modelo. O programa não cria
relações artificiais para completar uma árvore inválida. A validação dessas
regras confirma a estrutura; ela não comprova que todas as relações sejam
linguisticamente corretas.

## Entidades nomeadas

`entidades` contém as ocorrências extraídas de `doc.ents`, ordenadas pela
posição no documento. Nomes repetidos geram ocorrências distintas, cada uma
com identidade e ordem próprias. Os rótulos são os efetivamente suportados
e produzidos pelo modelo instalado.

Cada ocorrência registra `id`, `ordem`, `texto`, `rotulo`, `token_ids`,
`periodo_id`, `paragrafo_id`, `trabalho`, `original` e `origem`. O texto é
obtido pelo recorte exato do trabalho. Seus limites precisam coincidir com
o início do primeiro e o fim do último token de conteúdo da entidade.
Os `token_ids` listam tokens de conteúdo consecutivos, na ordem da fonte,
todos no mesmo período e parágrafo.

Uma entidade pode conter espaços internos, inclusive repetidos: em
`"João   Silva"`, os separadores ficam no texto e nos intervalos, enquanto
`token_ids` contém somente os tokens de conteúdo. Isso não autoriza aparar
ou expandir a previsão. Uma entidade sem tokens, com limites incompatíveis
ou atravessando unidades gera erro explícito.

Uma lista vazia é válida. O aplicativo não acrescenta rótulos de pessoa
ou organização a substantivos genéricos por regras próprias. Categorias
adicionais e regras linguísticas pertencem a etapas posteriores.

## Posições e mapeamento ao original

As coordenadas continuam em **pontos de código Unicode**, base zero e fim
exclusivo: `[inicio, fim)`. Consumidores que usam bytes UTF-8 ou unidades
UTF-16 devem converter suas posições com o texto correspondente; um emoji
pode ter tamanhos diferentes nessas convenções. Veja as
[instruções da preparação](preparacao.md).

Os intervalos das entidades são convertidos pelo mapa da preparação.
`mapear_intervalos(preparacao, [(inicio, fim), ...])` valida a preparação
uma vez e devolve os intervalos originais na mesma ordem. Sua convenção é
a mesma de `mapear_intervalo(preparacao, inicio, fim)`: quando um intervalo
atravessa uma transformação técnica, inclui o intervalo original completo
dessa transformação. Não há cálculo manual de deslocamentos de CRLF.

O mapeamento em lote mantém o comportamento da função individual e evita
revalidar a preparação completa para cada entidade. As fontes literais e
normalizadas conservam o mesmo original; posições no trabalho podem diferir
quando CRLF é convertido em LF.

## Registro e prontidão

O registro inclui `documento_id`, `preparacao_id`, `segmentacao_id`,
`anotacao_id`, `analise_id` e `registrado_em` com fuso. O bloco `anotacao`
preserva integralmente a etapa 05, incluindo as etapas 04 e 03. Os blocos
`coordenadas`, `processamento`, `sintaxe`, `entidades` e `validacao`
completam o pacote.

O validador revalida a entrada, em vez de confiar somente em seu indicador
de prontidão. Confere IDs, cobertura, ordem, textos, intervalos, árvores,
associações das entidades e a origem das previsões. Somente uma análise
que passa em todas as verificações recebe `pronto_para_etapa_07: true`.
A etapa 06 termina nesse registro; não aplica regras linguísticas,
classificações ou interpretação teórica.

## Limites

Os limites são os mesmos da etapa 05:

- **50.000 pontos de código Unicode** no texto de trabalho.
- **10.000 tokens internos**, incluindo os tokens espaciais.

Os limites são conferidos antes da inferência. Entradas maiores produzem
erro, sem truncamento ou análise parcial. Uma preparação válida nas etapas
anteriores pode exceder esses limites. A contagem de tokens exibida na etapa
04 exclui espaços e pode diferir da contagem interna usada aqui.

## Interface, persistência e API HTTP

Na página do documento, selecione a anotação morfológica desejada e clique
em **Analisar sintaxe e entidades**. O resultado mostra as dependências
junto aos tokens e as entidades junto ao texto de origem. A ausência de
entidades é apresentada normalmente. Use **Baixar análise JSON** para
exportar o registro completo.

O SQLite acrescenta registros na tabela `analyses`, vinculados à anotação
morfológica exata. Novas execuções preservam o histórico e o original.
Ao escolher uma análise anterior, a interface usa sua anotação, segmentação
e preparação de origem, mesmo quando existem versões mais recentes.

- `POST /envios/<id>/analises`: com JSON, aceita `anotacao_id`, `analise_id`
  e `registrado_em` opcionais e retorna HTTP 201 com o registro completo.
  Sem `anotacao_id`, resolve a anotação mais recente do documento naquele
  momento e preserva sua identidade exata.
- No mesmo POST, o formulário envia explicitamente a anotação escolhida.
  A resposta HTTP 303 seleciona os registros de origem e a nova análise.
- `GET /envios/<id>/analise.json?analise_id=<id>`: exporta a análise
  escolhida como anexo. Sem o parâmetro, entrega a mais recente do documento.
- `GET /envios/<id>?analise_id=<id>`: seleciona uma análise salva e sua
  cadeia exata de origem. Seleções conflitantes entre etapas são recusadas.

Argumentos inválidos retornam HTTP 400; fonte ou registro armazenado
inconsistente e identidade duplicada, 409; divergência de alinhamento ou
previsão incompatível com o contrato, 422; excesso de limites, 413;
modelo indisponível ou incompatível, 503; registros inexistentes, 404.

## Exemplos e verificações

Os exemplos foram gerados com o modelo real e preservam a cadeia de origem:

- [Preparação literal](../examples/sintaxe_entidades_literal.json).
- [Conversão opcional de CRLF em LF](../examples/sintaxe_entidades_normalizada.json).

Nos dois exemplos, o modelo classificou `São\tPaulo` como `ORG`. O registro
conserva esse rótulo real, que precisa de revisão linguística; passar pela
validação estrutural não transforma essa previsão em uma referência correta.

Na pasta `lingua/` do repositório, execute os testes com o Python do ambiente virtual:

```bash
../.venv/bin/python -m unittest discover -s tests -v
```

No Windows, use `..\.venv\Scripts\python.exe` no lugar de `../.venv/bin/python`.
Os testes de contrato verificam preservação, Unicode, separadores, árvores,
entidades, intervalos, erros, persistência e histórico; os testes com o modelo
real conferem o alinhamento e a execução dos componentes instalados.
A validação estrutural e a execução do modelo não substituem uma avaliação
linguística com referência anotada independentemente.

Na entrega desta etapa, **239 testes passaram**, incluindo **65 novos testes**
de contrato, modelo real, integração e mapeamento em lote. O fluxo também foi
verificado por um servidor HTTP real com SQLite temporário: origem intacta,
exportação JSON e histórico preservado após encerrar e reiniciar o servidor.
Os dois exemplos JSON foram gerados pelo modelo instalado e revalidados após
a serialização e a leitura.

O relato de três parágrafos e dezesseis períodos continua pendente na
conferência da etapa 04. Essas contagens não são impostas à etapa 06
nem a outros documentos.
