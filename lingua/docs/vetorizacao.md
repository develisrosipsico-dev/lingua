# Etapa 09: vetorização multimodelo

A etapa 09 recebe uma execução pronta da etapa 08 e produz representações
vetoriais. O perfil inicial da interface, CLI e Colab, `e5_analista`, produz
os dois parágrafos originais e o relato inteiro para upload no Agente Analista.
Os demais perfis produzem período, janela contextual e documento. A origem é
revalidada com `validar_unidades_contexto()`; somente o relatório recalculado
com `pronto_para_etapa_09: true` autoriza continuar. O registro exportado
conserva a execução contextual inteira uma única vez, incluindo sua cadeia
de preparações, segmentação e anotações.

O texto canônico permanece intacto. Prefixos, cabeçalhos ou recortes usados
na entrada do modelo são registrados separadamente. A etapa não corrige
anotações nem resolve as ambiguidades linguísticas herdadas. A integridade
do vetor e de sua origem também não certifica qualidade semântica.

## Instalação local

As dependências das etapas 01–08 permanecem em `requirements.txt`. Para usar
embeddings, instale o conjunto opcional com o aplicativo encerrado:

```bash
git pull --ff-only origin organizacao/projetos-20261010
../.venv/bin/python -m pip install -r requirements-vetorizacao.txt
../.venv/bin/python modelo_embeddings.py --baixar
../.venv/bin/python app.py
```

No Windows, substitua `../.venv/bin/python` por
`..\.venv\Scripts\python.exe`. Abra <http://127.0.0.1:5001> no mesmo computador.

O comando de download é explícito. Abrir a página, consultar um resultado ou
validar um JSON não baixa pesos nem carrega bibliotecas de inferência. Se o
modelo não estiver disponível localmente, o trabalho informa uma falha e
pode ser retomado depois de instalar os recursos necessários. Os textos são
processados localmente; o download traz arquivos do modelo, não envia o
documento para um serviço de embeddings.

O perfil inicial usa `intfloat/multilingual-e5-large` com revisão imutável
`3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3`. Esse modelo tem arquivos grandes e
consumo de memória superior ao do modelo português das etapas anteriores.
CPU é o dispositivo inicial. GPUs CUDA, inclusive dispositivos numerados,
e MPS podem ser selecionados quando suportados pelo ambiente e adaptador.
GPU não é uma dependência obrigatória.

Em Linux, para instalar especificamente o pacote PyTorch de CPU antes das
demais dependências opcionais:

```bash
../.venv/bin/python -m pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu
../.venv/bin/python -m pip install -r requirements-vetorizacao.txt
```

FastEmbed é opcional e tem suas dependências em `requirements-fastembed.txt`.
Não é necessário para o adaptador de referência rodar em CPU. Instalar o
pacote não comprova equivalência de seus pesos ONNX com outro backend.

## Modelo, perfil, adaptador e execução

O núcleo não fixa globalmente dimensão, janela de tokens, prefixo, pooling,
normalização, precisão, formato ou fragmentação. A configuração solicitada
é resolvida contra as capacidades efetivas do adaptador e fica registrada
antes da inferência. Uma opção incompatível gera erro explícito.

| Componente | Responsabilidade |
| --- | --- |
| Modelo | Identificação, revisão dos pesos e tokenizador. |
| Perfil | Finalidade, preparação da entrada e políticas versionadas. |
| Adaptador | Tokenização, capacidades e geração efetiva dos vetores. |
| Execução | Origem, configuração resolvida, dispositivo e histórico. |
| Artefato | Entrada ou derivação, dimensão, formato, valores e integridade. |
| Representação | Associação do artefato a um trecho ou documento da origem. |

`perfis_vetorizacao.py` valida perfis; `modelo_embeddings.py` fornece o
adaptador de referência SentenceTransformers/PyTorch e o caminho opcional
FastEmbed. `vetorizacao.py` planeja entradas, confere resultados e permite
consultar ou derivar representações. `persistencia_vetores.py` guarda os
artefatos, e `trabalhos_vetorizacao.py` acompanha processamento e retomada.

Os cinco perfis pertencem à família E5:

| ID | Finalidade | Prefixo inicial |
| --- | --- | --- |
| `e5_analista` | P1, P2 e relato inteiro para o Agente Analista. | `query: ` |
| `e5_consulta` | Consulta em recuperação assimétrica. | `query: ` |
| `e5_conteudo` | Conteúdo a recuperar. | `passage: ` |
| `e5_simetrico` | Comparação entre textos. | `query: ` |
| `e5_legado` | Compatibilidade histórica explicitamente solicitada. | `passage: ` |

São perfis versionados, não constantes aplicáveis a qualquer modelo.
Modelo, revisão, backend, dispositivo, lote, precisão de inferência,
formato de armazenamento, limite de tokens, pooling, normalização e
políticas podem ser selecionados por configuração nos perfis gerais.
`e5_analista` fixa E5 large e sua revisão, SentenceTransformers, média, L2,
inferência e armazenamento `float32`, texto original e limite de 504 tokens.
Dispositivo, lote e cache continuam configuráveis. A mudança de modelo
mantém os experimentos anteriores. Novos mecanismos podem implementar a
mesma interface de adaptador sem alterar as etapas 01–08.

O adaptador deve informar sua descrição efetiva, tokenizar sem truncamento
e gerar sem prefixos ocultos. Tokens do modelo de embeddings são distintos
dos tokens linguísticos da etapa 04. Revisões dos pesos e do tokenizador,
versões das bibliotecas e precisão são parte da proveniência.

Exemplo de opções avançadas, compatível com o perfil geral `e5_simetrico`:

```json
{
  "dispositivo": "cpu",
  "lote": 8,
  "precisao_inferencia": "float32",
  "formato_armazenamento": "float32",
  "pooling": "mean",
  "normalizacao": "l2",
  "fragmentacao": "sem_sobreposicao",
  "agregacao": "media_ponderada_tokens",
  "texto_documento": "trabalho"
}
```

| Opção | Valores iniciais |
| --- | --- |
| `backend` | `sentence_transformers` ou `fastembed`. |
| `dispositivo` | `cpu`, `cuda`, `cuda:N` ou `mps`, se disponíveis. |
| `precisao_inferencia` | `float32`, `float16` ou `bfloat16`, se suportadas. |
| `formato_armazenamento` | `float16`, `float32` ou `float64`. |
| `pooling` | `mean`, `cls`, `max`, `last_token` ou `modelo`, se suportados. |
| `normalizacao` | `l2` ou `nenhuma`. |
| `fragmentacao` | `sem_sobreposicao` ou `erro`. |
| `agregacao` | `media_ponderada_tokens` ou `media_simples`; `nenhuma` somente em `e5_analista`. |
| `texto_documento` | `trabalho` ou `original`. |

`limite_tokens` omitido é resolvido pelo adaptador. Um valor explícito
precisa respeitar sua capacidade. Outro modelo ou tokenizador exige revisão
imutável fornecida, nunca `main` ou um apelido móvel. Os valores aceitos no
perfil ainda precisam ser compatíveis com o mecanismo real; o adaptador
FastEmbed inicial, por exemplo, exige inferência ONNX `float32`.

Para baixar explicitamente a configuração selecionada e depois consultar
sua descrição local:

```bash
../.venv/bin/python modelo_embeddings.py --baixar --perfil e5_simetrico --opcoes '{"dispositivo":"cpu","lote":8}'
../.venv/bin/python modelo_embeddings.py --descrever --perfil e5_simetrico --opcoes '{"dispositivo":"cpu","lote":8}'
```

`--cache-modelos` permite selecionar uma pasta de pesos. A descrição
efetiva registra o modelo realmente carregado, não somente a solicitação.

`LINGUA_CACHE_MODELOS` seleciona o cache padrão ao iniciar o aplicativo, sem
mover arquivos. Uma opção explícita `cache_modelos` prevalece. Execuções já
criadas conservam o caminho registrado, inclusive após reiniciar o servidor
com outra variável; crie uma nova execução para usar o novo cache. A página
exibe imediatamente o diagnóstico de uma falha recebida pelo acompanhamento,
e o GET de status preserva `erro.tipo` e `erro.mensagem`. Falhas de importação
incluem a dependência original, sem serem confundidas com pesos ausentes.

## Textos e coordenadas

Em `e5_analista`, são criadas três representações a partir de
`preparacao.original.texto`: P1 e P2 com as margens em branco removidas, e
o documento com o texto original inteiro, inclusive separadores e espaços.
Exigem-se exatamente dois parágrafos e até 400 palavras, contadas por espaços
em branco. Os intervalos originais e seu mapeamento para o texto de trabalho
são preservados, inclusive quando a preparação normaliza CRLF.

Nos demais perfis, para cada unidade contextual são criadas duas representações:

| Tipo | Campo selecionado | Referências |
| --- | --- | --- |
| `periodo` | `unidade.foco.texto` | Unidade e período foco. |
| `contextual` | `unidade.janela.texto` | Unidade e identidade lógica da janela. |
| `documento` | Texto integral de trabalho na preparação. | Documento e preparação. |

Há somente uma representação de documento por execução. O documento não
é reconstruído pela concatenação dos períodos: isso poderia perder
separadores, espaços iniciais ou finais. A origem contém o original e o
texto de trabalho; o padrão utiliza o trabalho, alinhado aos focos e janelas.
`texto_documento: "original"` seleciona explicitamente o original para a
representação de documento; focos e janelas continuam usando seus campos
canônicos. A escolha fica na configuração e não modifica a preparação.

Os intervalos são pontos de código Unicode, base zero e fim exclusivo:
`[inicio, fim)`. São conferidos com a fonte, e os intervalos no original
vêm do mapa da preparação. Não são offsets em bytes UTF-8 ou unidades
UTF-16. Consumidores que usem essas unidades precisam converter as posições
sobre o texto exato, sem normalizar Unicode ou quebras de linha.

Uma janela lógica pode coincidir entre preparações literal e normalizada
com textos de trabalho diferentes. Por isso, `janela_logica_id` não é
suficiente para reutilizar um vetor. Texto efetivo, configuração e origem
continuam sendo conferidos separadamente.

## Entradas longas, fragmentação e agregação

Parágrafo, período, janela e documento são enviados integralmente quando couberem no
limite efetivo selecionado. Um modelo com janela maior não herda o limite
do E5. A contagem inclui prefixo e tokens especiais; o núcleo não permite
truncamento silencioso pelo encoder.

Quando a política exige fragmentar, os fragmentos são recortes literais do
texto. Nos perfis gerais, o planejamento prefere fronteiras existentes e divide tecnicamente
um trecho quando necessário. Não reconstrói texto com `tokenizer.decode()`.
Os intervalos ordenados precisam cobrir o texto por inteiro, sem lacunas ou
caracteres inventados. A política de erro também permite recusar entradas
maiores sem produzir um resultado parcial.

`e5_analista` restaura o recorte anterior: busca o maior trecho literal que
cabe em 504 tokens com o prefixo `query: ` e os tokens especiais. Cada recorte
tem seu próprio vetor e alimenta a busca separadamente. Quando há mais de um,
a representação usa `metodo: "fragmentado"`, `artefato_id: null` e conserva
todos os componentes. Não há agregação nem inferência no agente consumidor.

| Método | Proveniência |
| --- | --- |
| `direto` | Uma entrada textual efetivamente enviada ao modelo. |
| `fragmentado` | Recortes independentes, sem vetor agregado, no perfil `e5_analista`. |
| `agregado` | Fragmentos de um texto, intervalos, vetores componentes e fórmula. |
| `derivado` | Representações selecionadas, ordem, pesos e fórmula. |

A agregação inicial pode usar média ponderada pela quantidade de tokens de
conteúdo ou média simples, conforme a política escolhida. Prefixos e tokens
especiais não devem ganhar peso como conteúdo do documento. Os vetores dos
fragmentos são preservados. A normalização final segue o perfil, e precisão
de inferência, cálculo e armazenamento são registradas separadamente.

Um agregado não é apresentado como uma única inferência do documento
integral. A média pode diluir temas localizados e não conserva toda a
informação das partes. A rastreabilidade permite consultar os fragmentos.
Uma entrada sem conteúdo não recebe vetor zero ou artificial; o estado
`sem_conteudo` registra essa condição.

## Perfil de compatibilidade histórica

O `embed.py` fornecido foi analisado como referência externa. O código e o
acervo daquele projeto não são necessários para usar o Língua. As opções
históricas ficam restritas ao perfil explícito `e5_legado`.

O arquivo histórico acrescenta título e seção, junta parágrafos com espaços,
corta o texto preparado em 2.000 pontos de código e acrescenta o prefixo
depois. Seu caminho SentenceTransformers usa CUDA, lote 32, modelo em
`float16` e limite de 512 tokens; FastEmbed usa lote 16 e depende de
configurações externas para alguns desses parâmetros. Ambos normalizam
antes de armazenar em `float16`.

No Língua, cabeçalhos precisam ser fornecidos explicitamente, e qualquer
preparação diferente conserva a origem 08 intacta. O corte histórico
registra cobertura parcial, intervalos utilizados e omissões. Dois mil
caracteres não garantem caber na janela do tokenizer; o limite efetivo
continua sendo conferido. Converter o armazenamento `float16` para
`float32` não recupera a precisão perdida.

A versão inicial do Língua aplica cabeçalho, quebra LF e corte ao recorte
canônico selecionado. Não reproduz automaticamente a junção dos parágrafos
do corpus externo. Essa diferença fica nas limitações do registro; o nome
do perfil histórico não constitui uma declaração de reprodução integral.

As revisões originais de pesos/tokenizador e a equivalência do modelo
ajustado não foram comprovadas pelo arquivo. Um nome ou caminho local não
comprova que SentenceTransformers e FastEmbed carreguem os mesmos pesos.
A compatibilidade histórica permanece não verificada até existirem
artefatos identificados e avaliação real. As configurações históricas não
limitam os demais modelos ou equipamentos.

`juntar_paragrafos: true` é uma opção exclusiva desse perfil. A composição
usa interseções dos parágrafos da etapa 08, insere espaços explicitamente e
registra `recortes_origem` nas entradas: cópias têm intervalos no original e
no trabalho; inserções têm posição na entrada efetiva. Separadores omitidos
e o conteúdo cortado entram em `omissoes`. A opção começa desativada.

## JSON, hashes e validação

O registro contém `schema_version`, `etapa`, `execucao_id`, `registrado_em`,
identificadores da cadeia, `contexto_execucao_id`, `contexto`,
`contexto_sha256`, `perfil`, `processamento`, `configuracao_sha256`,
`espaco_vetorial_id`, `representacoes`, `artefatos` e `validacao`.
Datas têm fuso; IDs e datas podem ser fornecidos pelo chamador. Quando
omitidos, são gerados de fato, sem identificadores ou hashes fictícios.

Cada representação guarda seu tipo, origem, hash textual, método,
artefato, fragmentos e cobertura. Cada artefato direto guarda a entrada
efetiva, seus hashes, tokens, máscara de atenção, tokens especiais e
offsets; agregados e derivados guardam seus componentes e pesos.

| Hash | Conteúdo conferido |
| --- | --- |
| Texto | Texto canônico exato em UTF-8. |
| Entrada | Texto efetivamente enviado, incluindo prefixos. |
| Tokens | Tokenização e máscaras registradas. |
| Configuração | Perfil e propriedades efetivas de geração. |
| Vetor | Bytes no formato numérico e ordem declarados. |
| Contexto | Registro contextual de origem em JSON canônico. |

Os formatos de armazenamento suportados são `float16`, `float32` e
`float64`, em little-endian. O tamanho depende da dimensão e do formato;
não há regra global de quatro bytes por componente. Os valores exportados
já correspondem ao arredondamento do formato armazenado. Números não
finitos, dimensão incorreta, BLOB corrompido, vetor zero e norma incompatível
com a política são recusados.

`validar_vetorizacao()` funciona sem carregar o modelo. Revalida a origem
08, referências, recortes, mapas, hashes, tokens, formatos, dimensão,
cobertura e cálculos de agregação/derivação. Um importado estruturalmente
consistente não prova sozinho que sua inferência veio dos pesos declarados.
A geração pela aplicação utiliza apenas fábricas de adaptadores reais;
adaptadores determinísticos injetáveis são destinados aos testes de
contrato, nunca publicados como inferência neural.

Hashes dos bytes armazenados são exatos. Regerar uma inferência em outra
plataforma, precisão ou versão pode produzir diferenças numéricas. A
reprodução dessas inferências é avaliada com tolerâncias e condições
registradas, sem prometer igualdade bit a bit entre CPU e GPU.

## Uso direto

Depois de instalar o modelo, execute com uma origem contextual válida.
Pelo terminal, gere um arquivo novo para cada experimento:

```bash
../.venv/bin/python vetorizar.py examples/contexto_integrado.json --saida minha_vetorizacao.json --perfil e5_simetrico --opcoes '{"dispositivo":"cpu"}'
```

O comando lê o JSON da etapa 08, mostra progresso no terminal e recusa
sobrescrever uma saída existente. `--execucao-id` e `--registrado-em`
permitem fornecer identificação e data com fuso. O modelo precisa ter sido
baixado explicitamente com as mesmas opções de modelo e cache.

O uso como biblioteca oferece as mesmas validações:

```python
import json
from vetorizacao import (
    consultar_representacao,
    validar_vetorizacao,
    vetorizar_contexto,
)

with open("examples/contexto_integrado.json", encoding="utf-8") as arquivo:
    contexto = json.load(arquivo)

registro = vetorizar_contexto(
    contexto,
    perfil_id="e5_simetrico",
    opcoes={"dispositivo": "cpu"},
)
validar_vetorizacao(registro)
primeira = consultar_representacao(
    registro,
    representacao_id=registro["representacoes"][0]["id"],
)

with open("minha_vetorizacao.json", "w", encoding="utf-8", newline="") as arquivo:
    json.dump(registro, arquivo, ensure_ascii=False, allow_nan=False)
```

`vetorizar_contexto()` aceita perfil, opções, ID, data com fuso, acompanhamento
de progresso e callbacks de cache. O uso direto também aceita um adaptador
injetado para testes controlados; a aplicação usa uma fábrica de produção.
Resultados do cache só são reutilizados após conferência de entrada,
configuração e integridade, mantendo a associação com cada origem.

Uma derivação cria outra execução e conserva os componentes:

```python
from vetorizacao import derivar_vetor, verificar_compatibilidade

ids = [r["id"] for r in registro["representacoes"] if r["tipo"] == "periodo"]
derivado = derivar_vetor(registro, ids, metodo="media_simples")
validar_vetorizacao(derivado)
compatibilidade = verificar_compatibilidade(registro, registro, operacao="simetrica")
```

A seleção precisa ser não vazia e suas representações precisam pertencer à
execução validada. Pesos, quando utilizados, precisam ser explícitos e
compatíveis com a política. Uma derivação não sobrescreve a execução de
origem. `ErroVetorizacao`, derivado de `ValueError`, sinaliza contratos,
configurações ou resultados inválidos; o adaptador e o perfil também têm
erros próprios explícitos.

## Espaços vetoriais e comparação

Nome do modelo ou dimensão iguais não autorizam comparação. Modelo,
revisão, tokenizer, pooling, normalização, precisões, preparação de entrada
e backend permanecem identificados. Experimentos diferentes conservam
espaços e resultados distintos.

O contrato prevê explicitamente o par E5 consulta/conteúdo para recuperação
assimétrica. Isso não o torna um par simétrico genérico. Alterações de
modelo, revisão ou mecanismo não ganham equivalência automática.
SentenceTransformers e FastEmbed precisam de verificação real de pesos,
tokenização, pooling, normalização e resultados. Um cosseno alto numa
amostra é evidência aproximada dessa amostra, não reprodução exata nem
garantia de qualidade de recuperação.

Esta etapa guarda e consulta vetores por origem/identificador. Não cria
automaticamente um índice de busca nem implementa uma interpretação
teórica dos embeddings. Índices futuros precisarão identificar seus
artefatos, versão, métrica e contrato de comparação, preservando índices
anteriores durante novos experimentos.

## Trabalhos, SQLite e retomada

O banco recebe tabelas aditivas de trabalhos, execuções, artefatos binários
e representações. BLOBs são guardados uma vez por artefato; o manifesto
reconstrói a exportação JSON e confere a origem armazenada na etapa 08.
Chaves estrangeiras não substituem a validação das unidades dentro daquele
JSON. A leitura verifica documento, IDs, hashes, dimensão, formato e bytes.

A inferência é executada fora de transações de escrita longas. O trabalhador
mantém checkpoints de artefatos validados para retomar falhas, e uma
execução final só é publicada após conferir novamente a origem e toda a
cobertura. A retomada não substitui silenciosamente a configuração
efetiva resolvida no primeiro processamento.

Os trabalhos têm estados `enfileirada`, `executando`, `concluida`, `falhou`
e `interrompida`. O executor inicial usa um trabalhador; a capacidade é
configurável. Uma interrupção explícita invalida o processamento anterior
antes de permitir retomada. Não se presume que um trabalho ativo esteja
morto somente por ter excedido um tempo, evitando inferências simultâneas
sobre a mesma execução.

Instalar outra revisão ou alterar um perfil cria um experimento novo.
Reprocessar o mesmo documento conserva as execuções anteriores. Trabalhos
pendentes ou com falha não podem ser baixados como execuções prontas.
O banco continua local em `../instance/textos.sqlite3`; encerre o aplicativo
antes de copiar o arquivo para backup. Modelos baixados e ambiente virtual
não são enviados pelo Git.

## Interface e API HTTP

No painel de uma execução contextual pronta, escolha o perfil e as opções
de processamento e solicite a vetorização. A interface mostra progresso,
falhas, retomada e histórico; consultar o histórico não inicia inferência.
Selecionar uma execução antiga recupera sua origem contextual e a cadeia
exata anterior. O JSON de configuração permite opções avançadas, sempre
sujeitas à validação de perfil/adaptador.

- `POST /envios/<id>/vetorizacoes`: recebe JSON com
  `contexto_execucao_id`, `perfil_id`, `opcoes`, `execucao_id` e
  `registrado_em`. Retorna HTTP 202 e o trabalho criado; o formulário
  redireciona para o detalhe com HTTP 303.
- `GET /envios/<id>/vetorizacoes/<execucao_id>`: consulta o estado e progresso.
- `POST /envios/<id>/vetorizacoes/<execucao_id>/retomar`: solicita retomada
  mantendo origem, configuração e checkpoints.
- `GET /envios/<id>/vetorizacao.json?vetorizacao_execucao_id=<id>`:
  exporta uma execução final, com sua fonte 08 integral uma única vez.
- `GET /envios/<id>/representacao-vetorial.json?vetorizacao_execucao_id=<id>&representacao_id=<id>`:
  consulta uma representação da execução selecionada.
- `GET /envios/<id>?vetorizacao_execucao_id=<id>`: seleciona a execução e
  sua origem exata. Seletores conflitantes de outras etapas são recusados.

Entrada/configuração inválida recebe HTTP 400; recurso inexistente, 404;
fonte inconsistente, ID duplicado ou download de execução ainda não pronta,
409. Uma falha de inferência aparece no estado do trabalho e não cria
resultado parcialmente pronto. O processamento automático pode ser
desativado em testes com `VECTORS_AUTO_START=False`.

Quando `contexto_execucao_id` é omitido no POST JSON, a aplicação seleciona
a execução contextual mais recente do documento. O formulário fornece a
origem escolhida explicitamente. O download completo sem seletor usa a
execução vetorial concluída mais recente; a consulta de uma representação
exige os dois identificadores. `VECTORS_MAX_WORKERS` configura a capacidade
do executor, inicialmente um trabalhador.

### Recuperar um trabalho após encerrar o processo

Se o aplicativo foi encerrado durante a inferência, o trabalho pode
permanecer como `executando` no banco. Primeiro confirme que o processo
anterior terminou. Depois marque a interrupção explicitamente, substituindo
o ID abaixo pelo identificador exibido no histórico e usando o caminho do
banco configurado em `ANALISE_DB`, se diferente do padrão:

```bash
../.venv/bin/python - <<'PY'
import sqlite3
from trabalhos_vetorizacao import interromper_trabalho

conexao = sqlite3.connect("../instance/textos.sqlite3")
conexao.execute("PRAGMA foreign_keys = ON")
with conexao:
    interromper_trabalho(conexao, "ID_REAL_DO_TRABALHO")
conexao.close()
PY
../.venv/bin/python app.py
```

Abra o trabalho no navegador e clique em **Retomar**. Apenas reiniciar o
aplicativo não agenda os trabalhos antigos automaticamente. A retomada
conserva os checkpoints, invalida a publicação por um trabalhador anterior
e exige a mesma configuração efetiva. Para trabalhos com estado `falhou`,
use diretamente a retomada após resolver a causa indicada.

## Verificação e avaliação de modelos

Os testes de contrato usam entradas controladas e adaptadores
determinísticos injetados somente pelos testes. Exercitam modelos com
dimensões e limites diferentes, formatos, prefixos, Unicode, CRLF,
fragmentação, cobertura, agregação, derivação, adulterações, cache,
histórico, retomada e exportação seguida de leitura. O resultado desses
testes não é uma avaliação semântica do E5.

As verificações com modelo real são separadas e identificam revisão,
dispositivo, bibliotecas, precisão, lote e entradas. Para comparar novos
modelos, use o mesmo conjunto de consultas/documentos e relevâncias
controladas, mantendo cada experimento separado:

| Aspecto | Medidas |
| --- | --- |
| Recuperação | Recall@k e qualidade da ordenação frente à referência. |
| Desempenho | Carregamento, latência e vazão, incluindo condições do lote. |
| Recursos | Pico de RAM e de memória GPU, quando disponível. |
| Armazenamento | Dimensão, formato e bytes dos vetores/índice. |
| Compatibilidade | Diferenças numéricas e alterações no ranking recuperado. |

Tempos de equipamentos diferentes não devem ser atribuídos somente ao
modelo. Registre hardware e condições de medição. A quantidade de testes
aprovados comprova os contratos exercitados; não determina qualidade de
busca nem adequação clínica ou teórica.

`avaliacao_vetorizacao.py` usa somente registros já gerados. A recuperação
exige o contrato explícito consulta/conteúdo e referências de relevância
fornecidas pelo avaliador. Por padrão, o corpus contém representações de
período; `--conteudo-id` permite uma seleção explícita. Os relatórios têm
Recall@k, MRR@k e nDCG@k, com média por consulta e desempate pela ordem do
corpus. Ambas as pontas são renormalizadas para calcular cosseno.

```bash
../.venv/bin/python avaliacao_vetorizacao.py recuperar minhas_consultas.json meus_conteudos.json minhas_referencias.json --ks 1 3 10 --saida minha_avaliacao.json
../.venv/bin/python avaliacao_vetorizacao.py caracterizar minha_vetorizacao.json --saida meus_recursos.json
```

O JSON de referências mapeia cada ID real de representação de consulta
para IDs reais relevantes do corpus. Os valores podem ser listas de IDs ou
objetos que atribuem graus positivos de relevância. Esse gabarito precisa
ser definido independentemente do ranking calculado.

A caracterização conta bytes dos vetores únicos por dimensão/formato;
exclui modelo, metadados e índices. Tempo e memória são medições externas
opcionais, fornecidas por `--tempo-total-segundos`,
`--tempo-carregamento-segundos`, `--pico-memoria-bytes` e
`--memoria-gpu-bytes`. Valores ausentes permanecem ausentes; o comando não
estima medições nem transforma falta de dados em zero.

Para verificar dois mecanismos reais, prepare uma lista JSON de strings
efetivas, já incluindo prefixos, e use:

```bash
../.venv/bin/python compatibilidade_embeddings.py --entradas minhas_entradas.json --perfil-a e5_simetrico --perfil-b e5_simetrico --opcoes-a '{"backend":"sentence_transformers","dispositivo":"cpu"}' --opcoes-b '{"backend":"fastembed","dispositivo":"cpu","arquivo_onnx":"CAMINHO_ONNX_REAL","modelo_onnx_sha256":"SHA256_REAL_DO_ARQUIVO"}' --saida minha_compatibilidade.json
```

O adaptador FastEmbed exige arquivo ONNX e SHA-256 reais, além do
tokenizador de revisão fixada. Pesos externos ao grafo também precisam ser
identificados; a opção `arquivos_onnx_sha256` permite fornecer seus hashes.
O comando não acrescenta prefixos nem permite truncamento. Compara
metadados, tokenização, normas nas duas pontas, diferenças e cossenos. O
limiar histórico de 0,99 pode ser configurado, e o resultado sempre se
limita à amostra registrada: ele não concede compatibilidade global ou
altera automaticamente contratos de similaridade.

## Exemplos

- [Vetorização](../examples/vetorizacao.json).
- [Fragmentação com cobertura e agregação](../examples/vetorizacao_fragmentada.json).
- [Perfil de consulta](../examples/vetorizacao_consulta.json).
- [Perfil de conteúdo](../examples/vetorizacao_conteudo.json).
- [Avaliação de recuperação controlada](../examples/avaliacao_recuperacao.json).
- [Verificação amostral dos mecanismos](../examples/compatibilidade_backends.json).

Os registros identificam as condições efetivas do experimento. A avaliação
de recuperação e a comparação entre mecanismos descrevem somente seus
conjuntos de referência e amostras; não comprovam o funcionamento de uma
GPU não utilizada nem a reprodução do sistema histórico externo.

Os seis exemplos foram produzidos com o E5 Large da revisão fixada, em CPU,
com inferência `float32`. O fluxo HTTP 03–09 foi verificado em banco
temporário, incluindo fila, página, downloads, dois experimentos e leitura
após reiniciar o aplicativo. O original com CRLF permaneceu intacto.
O experimento padrão gerou nove representações e nove artefatos; o perfil
com orçamento de 16 tokens gerou nove representações e treze artefatos,
armazenados em `float16`, com cobertura integral e componentes preservados.
Em ambos, a capacidade do modelo registrada é 512 tokens.

A comparação PyTorch/ONNX da mesma revisão usou oito entradas com prefixos,
CRLF, acentos, caractere combinante, emoji e espaços. Os IDs, máscaras,
tokens especiais e offsets conferiram. O menor cosseno foi
`0.9999999999993604`, acima do limiar de `0.99`; os resultados foram
classificados como compatibilidade aproximada nessa amostra. Isso não
estabelece equivalência global, compatibilidade com pesos históricos ou
com uma GPU.

A avaliação controlada utilizou quatro períodos literais como consultas e
os mesmos quatro períodos como conteúdos, com correspondências definidas
antes do ranking. Recall, MRR e nDCG em k=1, 3 e 4 foram 1,0 nesse teste de
sanidade. A primeira execução levou aproximadamente 8,05 segundos e a
seguinte 0,96 segundo no processo de avaliação; o RSS máximo foi cerca de
2,29 GB. O RSS inclui bibliotecas e recursos carregados, e esses tempos
descrevem apenas essa execução. Os relatórios preservam os valores medidos
e seu escopo. É necessário um conjunto independente e representativo para
avaliar recuperação em produção.

Agregações e derivações usam `math.fsum` e `math.hypot` sobre os componentes
armazenados, com números Python em precisão dupla, antes de codificar o
resultado no formato escolhido. Os componentes, pesos e métodos versionados
permitem conferir esse cálculo sem executar novamente o modelo.

Execute a suíte na pasta `lingua/` do repositório:

```bash
../.venv/bin/python -m unittest discover -s tests -v
```

A validação desta entrega executou **561 testes, todos aprovados**, incluindo
os testes existentes das etapas anteriores. A verificação das dependências
com `pip check` também passou.
