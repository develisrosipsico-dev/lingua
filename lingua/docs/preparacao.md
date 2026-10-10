# Preparação literal e rastreabilidade

O módulo `preparacao.py` usa apenas a biblioteca padrão. Ele recebe o texto,
o identificador do documento e os metadados da etapa anterior. A preparação
é validada antes de receber `validacao.pronto_para_etapa_04 = true`.
Esta etapa é anterior à tokenização e não executa análise linguística.

## Uso direto

```python
import json
from preparacao import preparar_texto, mapear_intervalo, validar_registro

texto = "Eu não queria sair.\r\nMas fiquei."

# documento_id deve ser o identificador real fornecido pela etapa anterior.
def preparar_documento(documento_id, metadados):
    literal = preparar_texto(texto, documento_id, metadados=metadados)
    tecnico = preparar_texto(
        texto, documento_id, metadados=metadados, normalizar_crlf=True,
    )
    assert literal["original"]["texto"] == literal["trabalho"]["texto"]
    assert mapear_intervalo(tecnico, 20, 31) == {"inicio": 21, "fim": 32}
    assert mapear_intervalo(tecnico, 18, 21) == {"inicio": 18, "fim": 22}
    assert mapear_intervalo(tecnico, 19, 20) == {"inicio": 19, "fim": 21}
    serializado = json.dumps(tecnico, ensure_ascii=False, allow_nan=False)
    restaurado = json.loads(serializado)
    validar_registro(restaurado)
    return literal, restaurado
```

`normalizar_crlf=False` é o padrão: texto original e de trabalho são idênticos,
inclusive espaços, tabulações, pontuação, repetições, acentos e caracteres
combinantes. Não há normalização Unicode, correção ou mudança de vocabulário.
Quando a opção está ativa, apenas pares CRLF viram LF no trabalho. CR isolado,
LF isolado e outras quebras continuam intactos; o original não é sobrescrito.

É possível fornecer `preparacao_id` e `registrado_em` (datetime com fuso ou
string ISO 8601 com fuso). Com os mesmos IDs, data e entrada, o resultado é
reproduzível. Sem esses argumentos, o módulo gera UUID4 e data atual em UTC.
IDs de alterações são derivados do ID da preparação e do número da alteração.
Erros de entrada, metadados ou consistência levantam `ErroPreparacao`, uma
subclasse de `ValueError`; nenhum registro inválido é marcado como pronto.

## Metadados e versões

`metadados` é um objeto JSON, copiado para `metadados_origem` sem modificar
a entrada. Quando presentes, os campos `documento_id`, `versao`, `comprimento`
e `sha256` são conferidos contra o texto e o identificador recebidos. Outros
campos são preservados, sem inferir sua interpretação. Objetos não JSON,
NaN, referências circulares e chaves não textuais são recusados.

O identificador do documento aceita string não vazia ou inteiro não negativo;
o identificador de preparação aceita string não vazia. Versões são inteiros
positivos. A versão original é reutilizada dos metadados, ou começa em 1.
Uma preparação literal mantém essa versão no trabalho; a política CRLF ativa
cria a versão seguinte, mesmo se não existir CRLF a converter. Nesse caso,
a lista de alterações fica vazia. Cada execução tem sua própria identidade de
preparação, sem substituir preparações anteriores.

Comprimentos são calculados com `len(texto)`. Os SHA-256 são calculados sobre
`texto.encode("utf-8")` exato; o registro usa hexadecimal em minúsculas.

Para ler arquivos, use o leitor que preserva as quebras de linha:

```python
from preparacao import ler_texto_original
texto_original = ler_texto_original("mensagem.txt")  # UTF-8, newline=""
```

A decodificação é estrita. O leitor padrão preserva BOM UTF-8 e não utiliza
a conversão automática de CRLF/CR para LF. Se a origem usa outra codificação,
forneça a codificação real por `encoding`.

## Coordenadas e mapa

Os intervalos usam **pontos de código Unicode**, base zero e fim exclusivo:
`[inicio, fim)`. Um emoji pode ocupar um ponto de código e duas unidades UTF-16;
um caractere combinante tem posição própria. Um grafema visual pode conter
vários pontos de código.

Cada trecho do mapa informa `tipo`, intervalos `trabalho` e `original` e
`alteracao_id`. Os trechos positivos são ordenados, contíguos e cobrem ambos
os textos por inteiro. Cópias têm conteúdo idêntico; cada CRLF convertido tem
um trecho técnico de dois pontos no original para um ponto no trabalho,
vinculado a uma alteração com operação, versão da regra, motivo, conteúdos
anterior/posterior e os dois intervalos. Texto vazio tem mapa vazio válido.

`mapear_intervalo(registro, inicio, fim)` valida o registro e devolve um
intervalo no original. Toda transformação intersectada por um intervalo não
vazio é incluída integralmente. Por exemplo, o LF em `[19,20)` corresponde ao
CRLF inteiro em `[19,21)`. Intervalos vazios representam uma posição de borda:
`[20,20)` no trabalho equivale a `[21,21)` no original; texto vazio mapeia
`[0,0)` para `[0,0)`. Limites inválidos, índices booleanos e registros
inconsistentes são recusados.

Consumidores que usam bytes ou unidades UTF-16 precisam converter seus índices
antes de chamar o mapeamento. Depois de obter uma posição `p` neste formato,
o deslocamento equivalente em cada texto é:

```python
bytes_utf8 = len(texto[:p].encode("utf-8"))
unidades_utf16 = len(texto[:p].encode("utf-16-le")) // 2
```

Não utilize diretamente `String.length`/índices JavaScript ou deslocamentos de
bytes como posições neste mapa. A conversão precisa usar o texto correspondente
(original ou trabalho), e respeitar os limites dos pontos de código.

## Integração e JSON

Cada novo envio da página grava, numa única transação SQLite, o documento e
uma preparação validada. A tabela `submissions` preserva o original;
`preparations` acrescenta registros JSON identificados, sem atualizar versões
anteriores. A opção técnica está desmarcada por padrão na interface.

Após o envio, use **Baixar registro JSON**. Também é possível gerar uma nova
preparação do documento original. Documentos recebidos antes desta atualização
permanecem intactos e podem receber sua primeira preparação pelo mesmo botão.

- `GET /envios/<id>/preparacao.json`: entrega a preparação mais recente.
- `GET /envios/<id>/preparacao.json?preparacao_id=<id>`: entrega a versão escolhida.
- `POST /envios/<id>/preparacoes`: acrescenta uma preparação; com JSON, recebe
  `normalizar_crlf`, `preparacao_id` e `registrado_em` opcionais e retorna o
  registro com HTTP 201. Com `{}`, cria uma preparação literal.

Na API, opções inválidas retornam HTTP 400; identificador duplicado ou registro
armazenado inconsistente retorna 409; documento/preparação ausente retorna 404.
O JSON exportado é revalidado e conferido com o documento no SQLite. Não basta
confiar no campo de prontidão recebido. Originais e preparações sobrevivem ao
reinício do aplicativo e não são enviados ao GitHub.

Os exemplos completos, com IDs gerados e hashes calculados pelo módulo, estão em
[preservação literal](../examples/preservacao_literal.json) e
[CRLF para LF](../examples/crlf_para_lf.json). Ambos usam o texto do enunciado.
