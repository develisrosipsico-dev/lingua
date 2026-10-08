# Etapa 08: construir unidades de contexto

O módulo `unidades_contexto.py` recebe uma execução validada e pronta da etapa
07 e produz uma unidade para cada período da segmentação original. Cada
unidade separa o período central, chamado foco, da janela de períodos
selecionados. Conserva as referências às anotações do foco e dos vizinhos,
às evidências descontínuas, às ambiguidades e às necessidades de contexto.

O registro preserva a execução completa de regras em `regras`, uma única vez
por execução contextual. Essa origem inclui a cadeia das etapas anteriores,
o documento, seus hashes, textos, separadores, tokens, períodos, parágrafos,
anotações e os estados de cada regra. A construção trabalha sobre uma cópia;
não modifica nem resegmenta a origem.

O mecanismo utiliza somente a biblioteca padrão. Não carrega spaCy, não
executa novamente o modelo e não acrescenta interpretações semânticas.
Ambiguidades existentes continuam sendo ambiguidades mesmo quando mais texto
está disponível. A etapa 09 poderá escolher explicitamente um dos campos
textuais e verificar seu hash antes de vetorizar; a etapa 08 não cria vetores.
O registro usa `schema_version = "1.0.0"`,
`etapa = "08_unidades_contexto"` e módulo, política e identidade lógica
na versão `1.0.0`.

## Instalação e execução local

Use a pasta `linguaSpike` criada pelas [instruções do README](../README.md).
Com o aplicativo encerrado e suas alterações locais preservadas:

```bash
git pull --ff-only origin main
.venv/bin/python app.py
```

No Windows, inicie com `.\.venv\Scripts\python.exe app.py`.
A etapa 08 não altera `requirements.txt` nem baixa outro modelo. Execute a
instalação do README se este for o primeiro uso ou se as dependências mudarem
em outra atualização. Abra <http://127.0.0.1:5001>.

Escolha uma execução de regras pronta e use **Construir unidades de contexto**.
Os raios e a opção de passagem entre parágrafos ficam no formulário. O painel
apresenta o foco, os vizinhos, a política aplicada e as pendências herdadas.
As execuções salvas e os downloads permitem recuperar a origem exata usada.

## Uso direto e reprodução

```python
import json
from unidades_contexto import (
    construir_unidades_contexto,
    consultar_unidade_contexto,
    validar_unidades_contexto,
)

with open("examples/regras_linguisticas_literal.json", encoding="utf-8") as arquivo:
    regras = json.load(arquivo)

registro = construir_unidades_contexto(
    regras,
    execucao_id="minha-execucao-etapa08",
    registrado_em="2026-10-08T09:00:00-03:00",
    raio_anterior=1,
    raio_seguinte=1,
    atravessar_paragrafos=False,
)
relatorio = validar_unidades_contexto(registro)
primeira = consultar_unidade_contexto(
    registro, unidade_id=registro["unidades"][0]["id"]
)
mesma_unidade = consultar_unidade_contexto(
    registro, periodo_id=primeira["periodo_foco_id"]
)

serializado = json.dumps(registro, ensure_ascii=False, allow_nan=False)
restaurado = json.loads(serializado)
validar_unidades_contexto(restaurado)
```

- `construir_unidades_contexto(regras, *, execucao_id=None, registrado_em=None,
  raio_anterior=1, raio_seguinte=1, atravessar_paragrafos=False)` revalida a
  fonte e sua cadeia. Exige que o relatório recalculado confirme
  `pronto_para_etapa_08: true`; o indicador recebido sozinho não autoriza
  continuar. Uma execução incompleta de regras não se transforma em contexto.
- `validar_unidades_contexto(registro)` confere a fonte, a configuração,
  os textos e intervalos, a seleção, as referências, os hashes, a identidade
  lógica e o relatório de prontidão.
- `consultar_unidade_contexto(registro, *, unidade_id=None, periodo_id=None)`
  consulta dentro da execução informada. Forneça exatamente um seletor. O
  mesmo período pode receber janelas diferentes em execuções históricas.
- ID e data com fuso são opcionais e gerados automaticamente quando omitidos.
  Para reproduzir o JSON inteiro, forneça ambos. Reexecuções com outra
  identidade ou data preservam a seleção e sua identidade lógica quando
  documento, limites e política permanecem iguais.

`ErroContexto` deriva de `ValueError`. `ErroEntrada` indica origem inválida ou
não pronta; `ErroConfiguracao`, parâmetros inválidos; `ErroLimite`, expansão
excessiva. IDs vazios, datas sem fuso, seletores incorretos e registros
adulterados também geram erros explícitos. O ID contextual deve ser diferente
do ID da execução de regras usado como origem. Versões não suportadas são
recusadas em vez de reinterpretadas silenciosamente.

## Política e seleção

A política inicial usa `raio_anterior=1`, `raio_seguinte=1` e
`atravessar_paragrafos=False`. Os raios aceitam somente inteiros não negativos;
booleanos não são números de raio. A passagem aceita somente `True` ou
`False`, sem conversão de `0`, `1`, strings ou `None`.
A configuração também precisa ser serializável em JSON UTF-8. Inteiros cujo
tamanho impede essa representação no ambiente Python são recusados com
`ErroConfiguracao` antes da construção.

Cada janela inclui o foco exatamente uma vez. As listas anteriores, seguintes
e a lista completa seguem a ordem de leitura. Nas bordas, usa somente os
vizinhos disponíveis e registra a quantidade solicitada, a selecionada e o
limite encontrado. Um raio zero é uma escolha da política, não uma falta de
vizinhos. Com ambos os raios zero, a janela contém apenas o foco.

Por padrão, os vizinhos pertencem ao parágrafo do foco. Com
`atravessar_paragrafos=True`, a seleção usa a ordem global e registra todos
os parágrafos envolvidos, mantendo o foco em seu parágrafo de origem. O
texto da janela é um recorte contínuo, do início do primeiro período ao fim
do último; conserva os separadores efetivos entre eles, sem juntar textos
com espaços inventados.

A consulta `segmentacao.contexto_periodo` da etapa 04 conserva sua política
global anterior, que pode atravessar parágrafos. A etapa 08 usa sua própria
política versionada. Períodos sem entidades ou ocorrências de regras também
recebem unidades. Uma fonte pronta sem períodos produz zero unidades válidas.

## Campos e origem das anotações

O registro distingue `execucao_id`, da etapa 08, e `regras_execucao_id`, da
origem na etapa 07. Guarda a política completa e a configuração aplicada.
Cada unidade tem seu ID próprio, ordem, `periodo_foco_id`,
`paragrafo_foco_id`, listas de vizinhos, períodos e parágrafos selecionados,
além dos blocos `foco` e `janela`.

Os blocos textuais separam `texto`, `trabalho`, `texto_original` e `original`.
As coordenadas são pontos de código Unicode, base zero e fim exclusivo:
`[inicio, fim)`. Os intervalos originais são obtidos pelo mapa validado da
preparação, em lote. Não há cálculo manual de deslocamentos CRLF/LF.

As referências morfológicas usam o par `anotacao_id`/`token_id`; as sintáticas,
`analise_id`/`token_id`. Entidades usam seus IDs na análise, e ocorrências de
regras usam seus IDs na execução de origem. A propriedade pelo período separa
as anotações do foco das pertencentes aos vizinhos. Vínculos com outro período
não transferem a propriedade de uma ocorrência. Os alvos tipados continuam
disponíveis na origem, inclusive tokens e ocorrências.

`pendencias` preserva cada ambiguidade ou necessidade herdada, com descrição,
ID da ocorrência, campo de origem, índice da mensagem, papel `foco` ou
`vizinho` e contexto disponível. Como as mensagens da etapa 07 são strings
sem alvo estruturado, sua localização é sempre
`localizacao_indeterminada`. A etapa 08 não infere um alvo a partir do texto
da mensagem ou dos vínculos da ocorrência. Não declara a pendência resolvida;
uma referência temporal como “amanhã” pode exigir informação externa.

Separadamente, `vinculos_disponiveis` conserva os vínculos tipados de cada
ocorrência, o período de origem e o período proprietário do alvo. Sua
localização `na_janela` ou `fora_da_janela` descreve a disponibilidade desse
alvo explícito, sem associá-lo automaticamente a uma mensagem de pendência.
As evidências descontínuas da etapa 07 continuam acessíveis na origem, sem
preencher suas lacunas.

## Integridade e identidade lógica

`foco.sha256_texto` e `janela.sha256_texto` são hashes SHA-256 da codificação
UTF-8 de seus campos `texto` exatos. `sha256_texto_original` identifica o
campo `texto_original` correspondente. Nenhum desses cálculos normaliza
Unicode, espaços, tabulações ou quebras de linha nem inclui rótulos da
interface. `processamento.integridade` registra
`{"algoritmo": "sha256", "codificacao": "utf-8"}`.

O validador verifica os recortes contra a fonte e recalcula os hashes.
Adulterar texto e atualizar seu hash não contorna a validação: o recorte
ainda precisa corresponder à origem. Esses hashes complementam os do
documento inteiro e servem para conferir o texto escolhido na etapa 09.

`janela_logica_id` identifica uma seleção contextual estável, separada do ID
próprio da unidade e da execução. A descrição versionada `selecao_logica`
preserva o documento e seu hash original, os limites originais do foco e de
seu parágrafo, a sequência dos limites dos períodos e parágrafos selecionados,
as regras de segmentação e a política completa.

Os campos exatos de `selecao_logica` são:

| Campo | Tipo e conteúdo |
| --- | --- |
| `versao` | String `"1.0.0"`, versão do contrato da identidade. |
| `documento.documento_id` | String não vazia ou inteiro não negativo, sem booleanos; preserva a identidade e o tipo do documento original. |
| `documento.sha256_original` | String hexadecimal SHA-256 do documento original inteiro. |
| `foco.original` | Objeto `{inicio: int, fim: int}`, intervalo original do período foco. |
| `foco.paragrafo_original` | Objeto de intervalo original do parágrafo do foco. |
| `periodos` | Lista ordenada de objetos com `original` e `paragrafo_original`, ambos intervalos `{inicio, fim}`. |
| `regras_segmentacao` | Objeto com strings `identificacao` e `versao`, preservado da segmentação. |
| `politica_contexto.identificacao` | String `"janela_periodos_consecutivos"`. |
| `politica_contexto.versao` | String `"1.0.0"`. |
| `politica_contexto.raio_anterior` | Inteiro não negativo, sem booleanos. |
| `politica_contexto.raio_seguinte` | Inteiro não negativo, sem booleanos. |
| `politica_contexto.atravessar_paragrafos` | Booleano. |

A descrição é serializada como JSON canônico: chaves ordenadas,
`separators=(",", ":")`, `ensure_ascii=False`, UTF-8, seguida de SHA-256.
Não entram nesse cálculo datas ou IDs gerados para as execuções ou períodos.
Os IDs reais continuam presentes nas referências para indicar a origem exata.
O tipo de `documento_id` faz parte da descrição: o inteiro `1` e a string
`"1"` identificam documentos distintos no cálculo canônico.

Focos diferentes têm identidades diferentes mesmo quando suas janelas têm
texto igual. Políticas diferentes também permanecem distintas quando as
bordas produzem a mesma seleção disponível. Preparações literal e normalizada
podem compartilhar a identidade lógica quando os limites originais e a
política coincidem; seus hashes de texto de trabalho podem ser diferentes.
Por isso, a identidade lógica sozinha não serve como chave para reutilizar
um vetor. Duas execuções históricas podem ter a mesma identidade lógica sem
serem deduplicadas ou sobrescritas.

## Limites de recursos e validação

Antes de materializar a expansão, o construtor estima o custo de todas as
janelas. A configuração admite raios grandes, desde que a expansão respeite
os limites registrados em `processamento.limites_recursos`:

| Campo | Limite |
| --- | --- |
| `max_unidades` | 10.000 unidades, uma por período. |
| `max_referencias_periodos` | 100.000 períodos somados entre todas as janelas. |
| `max_caracteres_textuais` | 16.777.216 pontos de código Unicode somados nos quatro campos textuais por unidade: foco e janela, trabalho e original. |
| `max_referencias` | 500.000 referências agregadas, incluindo anotações, vínculos, tokens, seleção e contexto disponível das mensagens. |

Esses valores limitam a expansão contextual; não representam o tamanho em
bytes do JSON. O custo calculado fica em `validacao.cobertura.custo`.
Exceder um limite produz `ErroLimite` antes da criação de um resultado
parcial, sem omitir unidades nem truncar texto. Os limites das etapas de
origem continuam aplicáveis às respectivas etapas.

O validador revalida a origem e reproduz a organização determinística para
comparar o registro completo. Não confia em contagens ou hashes isolados.
Recusa vizinhos incorretos com contagens corretas, referências estrangeiras,
reordenação, campos extras, coerções de tipos, identidade lógica adulterada
e alteração conjunta de texto e hash incompatível com a fonte. Só um
registro consistente conserva `validacao.pronto_para_etapa_09: true`.

## Interface, persistência e API HTTP

A tabela SQLite `context_runs` guarda uma linha por execução, ligada à
execução exata da tabela `rule_runs`. É criada de forma aditiva ao iniciar o
aplicativo atualizado. O JSON conserva a origem integral e a configuração;
selecionar uma execução antiga recupera sua cadeia original mesmo após
novas preparações, análises ou regras.

- `POST /envios/<id>/contextos`: com JSON, aceita `regras_execucao_id`,
  `execucao_id`, `registrado_em`, `raio_anterior`, `raio_seguinte` e
  `atravessar_paragrafos`. Sem `regras_execucao_id`, escolhe as regras mais
  recentes do documento. Os demais parâmetros têm os padrões do uso direto.
  Retorna HTTP 201 com o registro. O formulário responde HTTP 303 para a
  página da execução criada.
- `GET /envios/<id>/contexto.json?contexto_execucao_id=<id>`: baixa o registro
  completo como anexo; sem o parâmetro, usa a execução contextual mais recente.
- `GET /envios/<id>/unidade-contexto.json?contexto_execucao_id=<id>&unidade_id=<id>`:
  consulta uma unidade. A execução é obrigatória; use alternativamente
  `periodo_id=<id>`, fornecendo exatamente um dos dois seletores.
- `GET /envios/<id>?contexto_execucao_id=<id>`: apresenta a execução escolhida
  e sua cadeia exata. O parâmetro `execucao_id` continua identificando a etapa
  07. Seleções conflitantes entre etapas são recusadas.

Payload, configuração ou consulta inválidos recebem HTTP 400; fonte
armazenada inconsistente, regras não prontas ou identidade duplicada, 409;
recurso inexistente, 404; limite de recursos excedido, 413. Uma origem não
pronta não é persistida como execução contextual parcial.

## Exemplos e verificações

- [Preservação literal](../examples/unidades_contexto_literal.json).
- [Conversão opcional de CRLF em LF](../examples/unidades_contexto_normalizada.json).

Os exemplos partem das execuções de regras já disponibilizadas, preservando
integralmente suas previsões e limitações. Usam IDs e datas fornecidos, a
política inicial sem passagem entre parágrafos e o mesmo documento original.
Eles ilustram a rastreabilidade; as previsões do modelo não são um gabarito
linguístico independente.

Cada exemplo contém cinco unidades, distribuídas pelos dois parágrafos da
origem, e 33 registros de pendências herdadas entre as janelas. Uma mesma
pendência pode aparecer em mais de uma janela, com sua origem explícita.
Os arquivos foram revalidados após serialização. A seleção e os intervalos
originais são equivalentes entre as duas preparações, assim como as cinco
identidades lógicas. Quatro hashes de janela diferem porque seus recortes de
trabalho incluem quebras CRLF/LF; a janela formada somente pelos períodos
três e quatro não contém essa transformação e mantém seu hash.

Para executar os testes na raiz local `linguaSpike`:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

No Windows, substitua o executável por `.\.venv\Scripts\python.exe`.
As seleções e os intervalos esperados dos testes devem ser definidos
independentemente do construtor. A conferência linguística e suas pendências
continuam registradas nas etapas de origem; prontidão para a etapa 09 confirma
o contrato contextual, não a correção semântica de cada previsão.
