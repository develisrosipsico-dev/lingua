# Etapa 07: aplicar regras linguísticas

O módulo `regras_linguisticas.py` recebe o JSON validado da etapa 06 e acrescenta
ocorrências explicáveis de cinco famílias: orações e núcleos verbais, negação,
tempo linguístico, modalidade e conectores. O registro conserva a análise
completa em `analise`, incluindo todas as etapas anteriores. Textos, tokens,
separadores, períodos, parágrafos, morfologia, dependências, entidades e
metadados da origem permanecem intactos.

O mecanismo usa somente a biblioteca padrão. A aplicação e a leitura do JSON
não carregam spaCy nem executam novamente o modelo. As dependências já
instaladas continuam necessárias para criar uma nova origem nas etapas 04–06.
O resultado fornece evidências para a etapa 08; não constrói ainda unidades
de contexto nem atribui conceitos teóricos ao narrador.
O registro usa `schema_version = "1.0.0"`,
`etapa = "07_regras_linguisticas"` e catálogo e mecanismo na versão `1.1.0`.
O formato mantém `execucao_id` e os vínculos tipados. O leitor também valida
registros históricos do mecanismo `1.0.0`, reproduzindo as regras dessa versão
e preservando o catálogo e as ocorrências originalmente registrados.

## Instalação e execução local

Use a subpasta `lingua/` do repositório, conforme as [instruções do README](../README.md).
Com o aplicativo encerrado e suas alterações locais preservadas:

```bash
git pull --ff-only origin organizacao/projetos-20261010
../.venv/bin/python app.py
```

No Windows, inicie com `..\.venv\Scripts\python.exe app.py`.
A etapa 07 não muda `requirements.txt` nem baixa outro modelo. Execute a
instalação descrita no README se este for o primeiro uso ou se esse arquivo
mudar em uma atualização. Abra <http://127.0.0.1:5001>.

## Uso direto e reprodução

```python
import json
from regras_linguisticas import (
    aplicar_regras_linguisticas,
    validar_regras_linguisticas,
)

with open("examples/sintaxe_entidades_literal.json", encoding="utf-8") as arquivo:
    analise = json.load(arquivo)

registro = aplicar_regras_linguisticas(
    analise,
    execucao_id="minha-execucao-etapa07",
    registrado_em="2026-10-07T20:00:00-03:00",
)
relatorio = validar_regras_linguisticas(registro)

serializado = json.dumps(registro, ensure_ascii=False, allow_nan=False)
restaurado = json.loads(serializado)
validar_regras_linguisticas(restaurado)
```

- `aplicar_regras_linguisticas(analise, *, execucao_id=None, registrado_em=None,
  regras_habilitadas=None)` valida efetivamente a fonte e sua cadeia, trabalha
  sobre uma cópia e devolve a execução com seu catálogo e diagnósticos.
- `validar_regras_linguisticas(registro)` revalida a origem, o contrato, as
  evidências, os alcances, os vínculos, o catálogo, a configuração e o relatório.
  Não confia apenas nos indicadores de prontidão recebidos.
- `regras_habilitadas=None` habilita o catálogo inteiro. Uma lista explícita
  habilita somente as regras identificadas nela; `[]` desabilita todas. IDs
  desconhecidos ou repetidos são erros de configuração.
- ID e data com fuso são opcionais. Quando omitidos, são gerados automaticamente.
  A mesma origem e configuração produzem as mesmas detecções, ordenação,
  evidências e alcances. Para reproduzir o JSON inteiro, forneça também ID e data.

A configuração conserva a seleção e a ordem efetiva de execução, definida
por prioridade e identificador de regra, independentemente da ordem dos IDs
na lista fornecida pelo chamador. Alterar uma configuração atual não modifica
registros históricos. O leitor reconhece explicitamente as versões que suporta;
uma versão desconhecida gera erro em vez de reinterpretar silenciosamente dados.

`ErroRegras` deriva de `ValueError`; `ErroEntrada` identifica uma análise ou
cadeia inválida, e `ErroConfiguracao`, uma seleção de regras inválida. Erros
operacionais isolados de regras produzem diagnósticos no registro, em vez de
substituir a execução por uma lista vazia.

## Catálogo inicial e cobertura

O catálogo separado em `catalogo_regras.py` declara identificador, versão,
família, descrição, condições, atributos utilizados, padrão, resultado,
critério de alcance, exceções, prioridade, política de sobreposição e exemplos
anotados. O registro guarda seu snapshot completo, além da versão do mecanismo.
Esta é uma cobertura inicial documentada, não uma gramática completa do português.

O catálogo `lingua-etapa07` e suas 12 regras atuais estão na versão `1.1.0`:

| Regra | Família | Cobertura inicial |
| --- | --- | --- |
| `negacao_correlativa` | Negação | Pares completos e adjacentes “não só/somente/apenas… mas também”. |
| `oracao_predicado` | Orações | Predicados verbais/copulares, auxiliares e vínculos de coordenação/subordinação. |
| `negacao_nao` | Negação | “Não” adverbial e menção citada; não inclui nominalização sem citação. |
| `negacao_nunca` | Negação | “Nunca” adverbial e menção citada. |
| `negacao_nem` | Negação | “Nem” adverbial/coordenativo, com alcance verbal ou nominal sustentado. |
| `negacao_multiplos` | Negação | Ao menos dois negadores reconhecidos com núcleo e contexto de citação comuns. |
| `tempo_flexao` | Tempo | Traços recebidos `Tense`, `Mood` e `VerbForm` de verbos/auxiliares. |
| `tempo_relativo` | Tempo | “Ontem”, “hoje”, “amanhã” e “quando” em subordinada temporal. |
| `modalidade_verbal` | Modalidade | Poder, dever, precisar, necessitar, conseguir, saber, querer, desejar, duvidar e ter que/de; capacidade de saber/conseguir exige `xcomp` infinitivo. |
| `modalidade_lexical` | Modalidade | Talvez, possivelmente, provavelmente; predicados copulares com lema necessário, obrigatório ou capaz. |
| `conector_simples` | Conectores | “Mas” entre predicados coordenados; “porque”, “se” e “quando” com condição sintática apropriada. |
| `conector_por_isso` | Conectores | Par adjacente “por isso”, anteposto ao núcleo predicativo, com ligação sintática explícita. |

A prioridade da correlativa é 10, a de orações é 20 e as demais são 100.
Empates seguem o identificador da regra. As exceções e os exemplos
completos são preservados por regra no JSON e estão disponíveis na interface.
Não há regras implícitas para “jamais”, “tampouco”, “portanto”, “logo”,
calendários, datas absolutas ou elipses verbais recuperadas.

### Orações e núcleos verbais

Os agrupamentos partem das dependências recebidas. Predicados verbais,
coordenação e subordinação mantêm vínculos com seus períodos e com outros
agrupamentos. Construções copulares também são consideradas: em “Ela está
feliz”, o núcleo sintático pode ser “feliz”, com “está” como elemento verbal.
Auxiliares ligados por `aux` ou `aux:pass` integram o predicado. Uma ligação
`xcomp` ou `ccomp` propõe uma oração complementar; não transforma automaticamente
“quer sair” em locução verbal.

Alcances ficam nas unidades de origem. Orações principais e subordinadas podem
ter relações e sobreposições justificadas. A regra usa a árvore, sem decidir
fronteiras apenas por vírgulas ou alterar a segmentação. Um token excluído
entre dois tokens selecionados obriga a separar os fragmentos.

### Negação

Marcadores “não”, “nunca” e “nem” são registrados separadamente do núcleo e do
alcance proposto. “Não pensei em sair” vincula o marcador a “pensei”; “Pensei
em não sair” vincula-o a “sair”, conforme a cabeça recebida. A determinação
do alcance não autoriza preencher um caso irresolvido com o período inteiro.

A construção completa “não só… mas também” tem prioridade e registra adição
correlativa. Suprime somente as interpretações incompatíveis dos marcadores
que fazem parte dessa construção. Um segundo “não”, como em “Não só não
estudou, mas também faltou”, continua sujeito à regra de negação.
Múltiplos marcadores são relacionados sem calcular polaridade pela sua quantidade.

### Tempo linguístico

Flexões temporais recebidas e expressões relativas como “ontem”, “hoje” e
“amanhã” são evidências diferentes. O registro conserva os traços morfológicos
e os vínculos verbais encontrados. Uma expressão relativa mantém seu valor
linguístico; o mecanismo não converte “amanhã” em data usando a data técnica
de processamento. A ausência de um traço em um verbo é registrada como
pendência quando esse atributo for necessário à regra.

### Modalidade

Padrões documentados propõem possibilidade, necessidade, obrigação, capacidade,
desejo expresso ou dúvida. “Pode” conserva alternativas de possibilidade,
permissão e capacidade quando as dependências disponíveis não as distinguem.
O marcador e o conteúdo possível são explicitados. Uma ocorrência de “quero”
registra a construção de desejo expresso; não afirma uma interpretação teórica
nem um estado do narrador.

A leitura de capacidade de “saber” e “conseguir” exige um complemento `xcomp`
com `VerbForm=Inf`. “Sei que chove”, com complemento `ccomp`, não constitui
evidência dessa habilidade. Se `VerbForm` estiver ausente no candidato a
complemento, a regra registra a pendência em vez de assumir um infinitivo.
Adjetivos modais flexionados, como “necessárias”, são reconhecidos pelo lema
recebido. Um adjetivo predicativo sem lema gera pendência; o mecanismo não
preenche esse lema a partir de uma forma superficial.

Ocorrências de modalidade e negação mantêm seus vínculos. “Não quero sair” não
autoriza afirmar um desejo positivo de sair só porque a regra encontrou “quero”.

### Conectores

“Mas”, “porque”, “se”, “quando” e “por isso” dependem do padrão e das relações
sintáticas previstas; encontrar a palavra não basta. O “se” reflexivo e o
“se” de interrogação indireta não são classificados automaticamente como
condição. “Quando” pode sustentar uma ocorrência de conexão e outra temporal;
as famílias diferentes não constituem duplicação indevida.

“Por isso” propõe consequência. Quando o antecedente depende de um período
anterior ou de contexto ainda indisponível, a ocorrência registra essa
necessidade sem inventar uma unidade de contexto.

Na cobertura inicial, “mas” precisa ligar predicados em coordenação `conj`;
contraste nominal, como “João, mas não Pedro”, não é classificado por essa
regra. “Por isso” precisa ocorrer antes do núcleo predicativo. Usos
pós-verbais ou argumentais como “passei por isso” ficam fora da versão inicial.

## Citações, ambiguidades e limites da entrada

Na versão `1.1.0`, delimitadores literais de aspas nunca se tornam núcleos
oracionais, mesmo quando o modelo lhes atribui uma classe gramatical incorreta.
O alcance proposto permanece na mesma região de citação do marcador. Esse
limite também vale para marcadores fora das aspas e para alcances modais;
nenhum deles incorpora automaticamente o conteúdo de outra região citada.
Esses ajustes não corrigem nem sobrescrevem as anotações recebidas.

As ocorrências conservam informações de citações pareadas simples e propostas
de discurso relatado apoiadas em complementos/parataxe de verbos de elocução.
Aspas aninhadas, aspas sem fechamento, fala com travessões, ironia e usos
metalinguísticos não têm interpretação semântica completa nesta versão.
Uma expressão citada não é automaticamente uma afirmação do narrador.

Ambiguidade sustentada por evidências disponíveis é diferente de anotação
ausente. Na primeira, o registro conserva alternativas ou necessidade de
contexto. Na segunda, identifica campos e tokens que impedem a regra; essa
pendência reduz a cobertura efetiva da execução e impede a prontidão completa.
Não há porcentagens inventadas de confiança.

Se faltar `pos` em um token do período de um candidato, a ausência pode ocultar
um núcleo e alterar as fronteiras oracionais. O mecanismo preserva o marcador
e registra essa pendência, retirando o alcance que dependeria dessa informação.
`null` não é tratado como classe não verbal, mesmo se a regra de orações estiver
desabilitada na configuração. A execução correspondente fica incompleta e
não recebe prontidão para a etapa 08.

O mecanismo conserva erros e limites do modelo. Nas sondagens da etapa 06,
“Se chover, ficarei” recebeu `Tense=Past` em “ficarei”, e “Quando chegar,
avise” recebeu `POS=NOUN` em “avise”. Uma entrada estruturalmente válida
pode conter essas previsões. A etapa 07 não as corrige nem as trata como
referência linguística independente.

## Ocorrências, fragmentos e coordenadas

Cada ocorrência identifica sua ordem, família, categoria, regra e versão,
período e parágrafo. Separa tokens do marcador/estrutura, núcleos, evidências,
alcance, vínculos, explicação, ambiguidades e alternativas. A explicação
corresponde à condição realmente satisfeita; o validador confere também os
predicados das evidências, em vez de aceitar apenas IDs existentes.

`marcador_token_ids` e `nucleo_token_ids` separam essas funções; `evidencias`
reúne os fragmentos dos tokens usados para sustentá-las. `alcance` é `null`
quando ausente, ou contém `token_ids` e `trechos`. `anotacoes_utilizadas`
preserva lema, classe, morfologia, dependência e cabeça dos tokens de evidência.
Esse campo resume as evidências diretas do marcador e do núcleo; não é um
registro completo de todos os acessos internos do detector. As demais
anotações consultáveis permanecem disponíveis na fonte integral `analise`.
Os `vinculos` podem referenciar tokens, períodos, entidades e ocorrências
oracionais existentes, com `tipo`, `alvo_tipo` e `alvo_id`.

Os fragmentos canônicos contêm tokens em ordem, sem repetição, agrupados
somente quando consecutivos na sequência de tokens da fonte. Cada fragmento
preserva o recorte exato do texto, inclusive separadores internos, e registra
`trabalho` e `original`. Um alcance descontínuo usa vários fragmentos e
não inclui artificialmente tokens intermediários excluídos.

As coordenadas são pontos de código Unicode, base zero e fim exclusivo:
`[inicio, fim)`. Bytes UTF-8 e unidades UTF-16 têm convenções diferentes.
`mapear_intervalos` faz o mapeamento em lote com o mapa validado da preparação,
incluindo as transformações CRLF→LF; não há cálculo manual de deslocamentos.

A ordenação é total e estável pela posição do primeiro token do marcador,
identificador da regra, categoria e representação JSON canônica da proposta.
A chave de duplicação usa a proposta canônica completa: regra e versão,
família e categoria, marcador, núcleo, alcance, unidades e vínculos, explicação,
ambiguidades, alternativas e necessidade de contexto. Repetições em posições
diferentes e sobreposições permitidas pelo catálogo permanecem distintas.

## Estados por regra e prontidão

`execucoes_regras` tem uma entrada para cada regra do snapshot do catálogo.
A ausência de ocorrências não equivale à ausência de execução.

| Estado | Significado |
| --- | --- |
| `executada` | A regra foi aplicada completamente; pode ter encontrado zero ocorrências. |
| `desabilitada` | A configuração não selecionou a regra. |
| `impedida` | Anotações necessárias ausentes impediram a execução útil. |
| `executada_com_pendencias` | A regra examinou parte dos candidatos e registrou impedimentos no restante. |
| `falhou` | Houve falha operacional identificada, com diagnóstico explícito. |

Falhas não são convertidas em listas vazias. Um diagnóstico incompleto pode
ser um registro válido, com `pronto_para_etapa_08: false`, e ser preservado
no histórico. Uma fonte inválida ou configuração inválida produz erro explícito
antes da execução; não é um diagnóstico de ausência de correspondências.

`pronto_para_etapa_08: true` exige contrato validado, pelo menos uma regra
habilitada e execução completa de todas as habilitadas, sem impedimentos
ou falhas. Ambiguidades corretamente representadas podem seguir à etapa 08.
A prontidão confirma o contrato e a cobertura declarada da configuração;
não certifica a correção linguística de cada previsão ou hipótese.

O validador reproduz as condições mecânicas das regras sobre a origem
preservada e compara o registro canônico, sem inferência neural. Ele verifica
também `catalogo_sha256` e recusa catálogo alterado ou versão não suportada.
Diagnósticos de falha armazenados são conferidos como diagnósticos; a leitura
não exige que uma falha operacional transitória volte a ocorrer.

## Interface, persistência e API HTTP

Selecione uma análise na página do documento e clique em **Aplicar regras
linguísticas**. O painel **Regras linguísticas** apresenta as cinco famílias,
ocorrências e seus trechos, catálogo, configuração e estados por regra.
**Execuções de regras salvas** permite selecionar o histórico; **Baixar regras
JSON** exporta o registro completo, inclusive o catálogo e a cadeia de origem.
O aplicativo escapa o texto apresentado em HTML.

A tabela SQLite `rule_runs` referencia a análise exata na tabela `analyses`.
A criação é aditiva: bancos existentes conservam textos e todas as etapas
anteriores. Uma execução antiga recupera sua análise, anotação, segmentação e
preparação de origem, mesmo se houver registros posteriores.

- `POST /envios/<id>/regras`: com JSON, aceita `analise_id`, `execucao_id`,
  `registrado_em` e `regras_habilitadas` opcionais. Sem `analise_id`, usa a
  análise mais recente do documento naquele momento. Retorna HTTP 201 para
  uma execução pronta, ou HTTP 422 com o diagnóstico incompleto preservado.
- No mesmo POST, o formulário envia a análise selecionada e responde HTTP
  303 para consultar o registro, incluindo os diagnósticos incompletos.
- `GET /envios/<id>/regras.json?execucao_id=<id>`: exporta a execução como
  anexo; sem o parâmetro, usa a mais recente do documento. Um diagnóstico
  incompleto válido continua disponível com HTTP 200.
- `GET /envios/<id>?execucao_id=<id>`: seleciona a execução e sua cadeia
  exata. Seleções conflitantes entre etapas são recusadas.

Payload ou configuração inválidos recebem HTTP 400; origem armazenada
inconsistente ou identidade duplicada, 409; recurso inexistente, 404.
Diagnóstico incompleto com HTTP 422 é distinto de erro estrutural do registro.

## Exemplos e verificações

Na integração atual, **424 testes passaram**. Os testes adicionais cobrem
citações, versões históricas e sua convivência com novas execuções no SQLite.
O fluxo 03–08 também foi verificado por HTTP real com banco temporário,
incluindo downloads após reinício, integridade do SQLite, original com CRLF
preservado e cópia de trabalho com a transformação opcional. Os dois exemplos
atuais foram gerados por esse fluxo e revalidados após a leitura do JSON.

Na entrega histórica da etapa 07 com mecanismo `1.0.0`, **318 testes passaram**,
incluindo 58 testes novos de regras/contrato e 21 de integração do aplicativo.
O fluxo também foi
verificado por um servidor HTTP real com banco temporário: cadeia 03–07,
cinco famílias, 38 ocorrências no texto dos exemplos, download JSON,
diagnóstico incompleto com HTTP 422, original intacto e histórico preservado
após encerrar e reiniciar o servidor.

- [Preservação literal](../examples/regras_linguisticas_literal.json).
- [Conversão opcional de CRLF em LF](../examples/regras_linguisticas_normalizada.json).

Os dois arquivos acima são exemplos históricos do catálogo e mecanismo
`1.0.0`. Continuam válidos e são lidos com a semântica dessa versão; suas
contagens abaixo descrevem a execução histórica.

O [exemplo atual de regras](../examples/regras_integradas.json) usa a versão
`1.1.0` e fornece a origem exata do
[exemplo integrado de contexto](../examples/contexto_integrado.json).
O contexto mantém o módulo da etapa 08 na versão `1.0.0`.

Os exemplos históricos usam o mesmo texto original, espaços repetidos, CRLF,
Unicode, cópula, auxiliar, subordinadas, negação, tempo relativo, modalidade, conectores
e uma expressão citada. Suas origens foram geradas com o modelo real, com IDs
e datas fornecidos, e revalidadas depois da serialização. Eles mostram saídas
reais e limites das previsões, não um gabarito linguístico independente.

Nos dois exemplos históricos, todas as 12 regras foram executadas e o mecanismo
produziu 38 ocorrências. As 89 evidências/fragmentos de alcance correspondentes
conservaram intervalos originais iguais entre a preparação literal e a
normalizada. A modalidade de “posso” conserva alternativas, e o “Não” dentro
de “Ele disse: ‘Não sairei amanhã’” conserva a indicação de citação/discurso
relatado, sem atribuir a fala ao narrador.

O modelo recebido anotou “escrevi” com `VerbForm=Part`, `Gender=Masc` e
`Number=Plur`. Os exemplos preservam essa previsão e o lema recebido
`escrevi`; a validade estrutural e a detecção da correlativa não os corrigem.

Para executar os testes, na pasta local `lingua/`:

```bash
../.venv/bin/python -m unittest discover -s tests -v
```

No Windows, substitua o executável por `..\.venv\Scripts\python.exe`.
Os testes de regras usam anotações controladas independentes do mecanismo
e do modelo; incluem positivos, negativos e ambiguidades. Os testes de
contrato verificam preservação, estados, textos, tokens, alcances descontínuos,
regras, vínculos, adulterações, serialização e mapeamento literal/normalizado.
Os testes SQLite/HTTP usam bancos temporários e verificam histórico, cadeia
exata, download e persistência de diagnósticos. A integração com o modelo real
confere alinhamento e preservação, sem usar suas previsões como referência
de correção linguística.

O relato de três parágrafos e dezesseis períodos continua pendente na conferência
da etapa 04. Suas contagens não são impostas à etapa 07 nem a outros documentos.
