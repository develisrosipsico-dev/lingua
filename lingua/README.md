# Lingua

Projeto independente de análise linguística, em Python com interface HTML.
Recebe um texto, registra o original em SQLite, gera uma preparação validada
e segmenta sua cópia de trabalho em tokens, períodos e parágrafos. Um modelo
português local acrescenta lemas, classes gramaticais, traços morfológicos e
vocabulário do documento, relações sintáticas e entidades nomeadas. Regras
versionadas acrescentam ocorrências de orações, negação, tempo, modalidade
e conectores, com evidências e propostas de alcance. Cada período recebe
uma unidade de contexto, com o foco, os vizinhos selecionados e as referências
às anotações existentes. A etapa 09 permite gerar embeddings do período,
da janela contextual e do documento, localmente ou no Colab, com modelo
e perfil configuráveis, sem substituir os resultados anteriores.
Os registros JSON contêm versões, hashes SHA-256,
metadados e posições que apontam até o original.

## Obter uma cópia local

Requer Git e Python de 3.10 a 3.14, com `pip`. A dependência spaCy 3.8.16
aceita Python anterior a 3.15. No terminal:

```bash
git clone --branch organizacao/projetos-20261010 https://github.com/develisrosipsico-dev/lingua.git lingua-agente
cd lingua-agente/lingua
```

O repositório público pode ser clonado sem login; para enviar alterações, autentique
sua conta no GitHub Desktop ou configure a autenticação Git no terminal.

## Executar

Execute os comandos na pasta `lingua`. A instalação inicial das dependências
precisa de internet; depois, o aplicativo funciona localmente, sem serviços externos.

Essa pasta agora está dentro do repositório, ao lado de `agente_analista/`.
Os comandos abaixo usam o ambiente `.venv/` na pasta acima e conservam o banco
e o cache existentes em `../instance/`. Não mova nem recrie esses dados ao
atualizar uma cópia já preparada.

### macOS ou Linux

Na primeira instalação, crie o ambiente virtual. Se `../.venv/` já existe,
comece pelo comando de instalação das dependências:

```bash
python3 -m venv ../.venv
../.venv/bin/python -m pip install -r requirements.txt
../.venv/bin/python app.py
```

### Windows (PowerShell ou Prompt de Comando)

```powershell
py -3 -m venv ..\.venv
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
..\.venv\Scripts\python.exe app.py
```

Abra <http://127.0.0.1:5001> no navegador do mesmo computador, cole o texto e
clique em **Registrar texto**. A confirmação mostra o identificador do registro
e a data em horário de Brasília. Atualizar a confirmação não repete o envio.
Mantenha o terminal aberto durante o uso e pressione `Ctrl+C` para encerrar.

## Navegar pelas etapas

Depois de registrar um texto, use as abas **Texto**, **Preparação**,
**Segmentação**, **Morfologia**, **Sintaxe**, **Regras**, **Contexto** e
**Vetores**. Uma etapa aparece por vez, com seus formulários, resultados e
histórico. Etapas pendentes indicam o que precisa ser concluído antes.

As setas esquerda/direita, Home e End mudam de aba; Tab entra no conteúdo.
A barra facilita alternar entre as etapas durante a leitura. O endereço
identifica a aba selecionada e permite voltar ou avançar pelo histórico do navegador.
Sem JavaScript, os links continuam disponíveis e as etapas aparecem na página.

Se a vetorização terminar enquanto você consulta outra aba, suas edições
permanecem na página. Abra **Vetores** e use **Ver resultado da vetorização**
para consultar a execução concluída.

## Preparar o registro para análise

Por padrão, o texto de trabalho é idêntico ao original. A opção técnica de
converter CRLF em LF começa desativada e modifica somente a cópia de trabalho.
Depois de registrar o texto, clique em **Baixar registro JSON**. Para outra
preparação do mesmo original, use **Gerar nova preparação**; as versões
anteriores permanecem disponíveis.

O módulo usa apenas a biblioteca padrão e pode ser utilizado sem Flask.
Veja [instruções, metadados e convenções de posições](docs/preparacao.md), e os
exemplos de [preservação literal](examples/preservacao_literal.json) e
[transformação opcional](examples/crlf_para_lf.json).

## Tokenizar e segmentar

Depois de registrar o texto, escolha a preparação em **Preparações salvas**,
quando houver versões anteriores. Abra a aba **Segmentação** e clique em
**Tokenizar e segmentar**. O resultado mostra as contagens e os
períodos; **Baixar segmentação JSON** exporta o registro completo. Cada geração
acrescenta um registro ao histórico, vinculado à preparação escolhida, sem
modificar o texto original ou resultados anteriores.

A etapa 04 usa `spacy.blank("pt")` e regras próprias versionadas para períodos,
com o tokenizador português. Ela prepara a estrutura para a etapa 05;
suas fronteiras podem precisar de revisão em textos ambíguos.
Veja [uso direto, regras e API HTTP](docs/segmentacao.md) e o
[exemplo de segmentação](examples/segmentacao.json).

## Anotar morfologia e vocabulário

Escolha uma segmentação salva e clique em **Anotar morfologia e vocabulário**.
O resultado mostra os tokens com lema, classe gramatical e morfologia, além
do vocabulário agrupado por lema e classe. Cada anotação entra no histórico;
use **Baixar anotações JSON** para exportar o registro completo.

A etapa 05 usa spaCy 3.8.16 com o modelo treinado português `pt_core_news_sm`
3.8.0. A instalação por `requirements.txt` inclui o modelo, com download de
aproximadamente 13 MB na primeira vez. Depois de instalado, o processamento
funciona localmente em CPU e o texto permanece no computador.

Esta etapa admite até **50.000 pontos de código** e **10.000 tokens internos**
do modelo, incluindo espaços. Entradas maiores geram erro explícito, sem
truncamento. Os limites são menores que os das etapas anteriores para conter
o consumo de memória do modelo. Fronteiras e previsões linguísticas podem
precisar de revisão humana.

Veja [uso direto, campos, limites e API HTTP](docs/anotacao.md), o
[exemplo completo](examples/anotacao.json) e a
[avaliação sobre referência manual](docs/avaliacao_morfologia.md).
O [relatório calculado](docs/avaliacao_morfologia_resultado.md) descreve o
desempenho do modelo nesse conjunto de referência.

## Analisar sintaxe e entidades

Escolha uma anotação morfológica salva e clique em **Analisar sintaxe e
entidades**. O resultado apresenta a relação sintática e a cabeça de cada
token, além das ocorrências de entidades nomeadas. Uma lista vazia de entidades
é um resultado válido. Use **Baixar análise JSON** para exportar o registro
completo; cada execução acrescenta uma análise ao histórico, vinculada à
anotação exata utilizada.

A etapa 06 usa o mesmo modelo português instalado pela etapa 05, com os
componentes `tok2vec`, `parser` e `ner`. As anotações morfológicas anteriores
permanecem intactas. Os limites continuam em **50.000 pontos de código** e
**10.000 tokens internos**. Uma divergência de tokens, períodos, árvore ou
entidades gera um erro explícito; o aplicativo preserva a previsão do modelo
sem corrigir relações ou recortar entidades para ajustar o resultado.

Veja [uso direto, convenções e API HTTP](docs/sintaxe_entidades.md) e os
exemplos de análise com [preservação literal](examples/sintaxe_entidades_literal.json)
e [normalização opcional de CRLF](examples/sintaxe_entidades_normalizada.json).
Uma análise validada fornece o material para a etapa 07.

## Aplicar regras linguísticas

Escolha uma análise sintática salva e clique em **Aplicar regras linguísticas**.
O resultado reúne cinco famílias: orações e núcleos verbais, negação, tempo
linguístico, modalidade e conectores. Cada ocorrência mostra a regra, seus
marcadores, núcleos, evidências, fragmentos de alcance, vínculos e ambiguidades.
O catálogo e o estado de cada regra ficam disponíveis no mesmo painel.

A etapa 07 usa somente a biblioteca padrão sobre o JSON validado da etapa 06.
Não executa novamente o modelo nem modifica as previsões anteriores. Ela
conserva as hipóteses linguísticas e suas limitações; a validação estrutural
não certifica que uma previsão do modelo ou uma interpretação esteja correta.

Cada execução entra em **Execuções de regras salvas**, vinculada à análise
exata usada, e pode ser exportada em **Baixar regras JSON**. Um diagnóstico
incompleto também fica no histórico: a interface diferencia regras sem
ocorrências, desabilitadas, impedidas por anotações ausentes e com falha.
Somente uma execução completa e validada recebe `pronto_para_etapa_08: true`.

As novas execuções usam catálogo e mecanismo `1.1.0`. Delimitadores literais
de aspas são excluídos dos núcleos, e os alcances respeitam a região de citação
do marcador, inclusive na modalidade. O formato JSON permanece na versão
`1.0.0`; execuções históricas continuam sendo validadas com suas regras originais.

Veja [catálogo, decisões, limitações e API HTTP](docs/regras_linguisticas.md),
o [exemplo atual de regras](examples/regras_integradas.json) e os exemplos
históricos `1.0.0` com [preservação literal](examples/regras_linguisticas_literal.json)
e [normalização opcional de CRLF](examples/regras_linguisticas_normalizada.json).

## Construir unidades de contexto

Escolha uma execução de regras pronta e clique em **Construir unidades de
contexto**. A configuração inicial seleciona até um período anterior e um
seguinte, dentro do mesmo parágrafo. Você pode alterar os raios e permitir
explicitamente a passagem entre parágrafos. O painel destaca o foco, mostra
os vizinhos e mantém acesso ao parágrafo, ao documento e às anotações de origem.

A etapa 08 usa somente a biblioteca padrão, preserva a saída completa da
etapa 07 e acrescenta uma unidade por período. Ela organiza as evidências,
ambiguidades e necessidades de contexto existentes. Não cria interpretações
semânticas novas nem declara ambiguidades resolvidas por incluir mais texto.

Cada execução possui seu próprio ID e fica no histórico. Os textos exatos do
foco e da janela recebem hashes SHA-256, e a seleção recebe uma identidade
lógica estável, independente do ID e da data da execução. Somente um registro
validado recebe `pronto_para_etapa_09: true`, condição verificada novamente
antes da vetorização.

O módulo contextual permanece na versão `1.0.0` e aceita ambas as versões
das regras. Os quatro exemplos históricos das etapas 07 e 08 permanecem válidos.

Veja [uso direto, identidade, limites e API HTTP](docs/unidades_contexto.md),
a [especificação consolidada](docs/especificacao_etapa08.md), o
[exemplo atual de contexto](examples/contexto_integrado.json) e os exemplos
históricos com [preservação literal](examples/unidades_contexto_literal.json) e
[normalização opcional de CRLF](examples/unidades_contexto_normalizada.json).

## Vetorizar para o Agente Analista e outros consumidores

Escolha uma execução contextual pronta e solicite a vetorização no painel
da etapa 09. O perfil inicial **Agente Analista — parágrafos e relato inteiro**
(`e5_analista`) usa `intfloat/multilingual-e5-large` com `query: ` e produz
vetores dos dois parágrafos originais e do relato inteiro. Exige dois parágrafos
e até 400 palavras. CPU é o dispositivo inicial. Outros perfis continuam
produzindo períodos, janelas contextuais e documento, com modelo e políticas
configuráveis. GPUs são opcionais, e os
experimentos de modelos diferentes conservam seus próprios resultados.

A inferência usa um trabalho acompanhado por progresso e diagnóstico.
Falhas podem ser retomadas; um resultado parcial não é disponibilizado como
pronto. O histórico e os downloads recuperam a origem contextual exata,
os textos preservados, a configuração, os vetores e seus hashes.

As dependências de embeddings e os pesos são opcionais. Para vetorizar no
próprio computador, com o aplicativo encerrado, instale e baixe explicitamente
o modelo antes do primeiro uso:

```bash
../.venv/bin/python -m pip install -r requirements-vetorizacao.txt
../.venv/bin/python modelo_embeddings.py --baixar
../.venv/bin/python app.py
```

No Windows, use `..\.venv\Scripts\python.exe`. Abrir a interface não baixa
pesos automaticamente; as etapas 01–08 continuam usando suas dependências
existentes. Depois de instalar os recursos, a inferência funciona localmente.

A entrada é enviada integralmente quando cabe no limite efetivo do modelo.
No perfil do Analista, o orçamento é de 504 tokens, contando prefixo e tokens
especiais. Entradas maiores geram recortes literais independentes, sem vetor
médio nem truncamento. Nos demais perfis, fragmentos preservam cobertura e
intervalos, e a representação agregada registra componentes e fórmula. Perfis diferentes
não são considerados automaticamente comparáveis. O perfil histórico do
`embed.py` precisa ser solicitado explicitamente e não restringe os demais
modelos. Veja [arquitetura, perfis, configuração, instalação e API HTTP](docs/vetorizacao.md).

O comando `vetorizar.py` também gera uma execução a partir de um JSON da
etapa 08, sempre em arquivo novo. `avaliacao_vetorizacao.py` calcula métricas
de recuperação sobre relevâncias fornecidas e caracteriza armazenamento;
`compatibilidade_embeddings.py` registra comparações amostrais de mecanismos.
Os [exemplos de vetorização](examples/vetorizacao.json) e
[fragmentação](examples/vetorizacao_fragmentada.json) conservam sua origem e
as configurações efetivas. As instruções completas estão no documento da etapa.

### Vetorizar no Colab sem instalar o E5 no Mac

Se o aplicativo ainda estiver aberto, encerre-o com `Ctrl+C`. Na pasta
`lingua`, atualize o código e reinicie:

```bash
git pull --ff-only origin organizacao/projetos-20261010
../.venv/bin/python app.py
```

1. Abra <http://127.0.0.1:5001>, selecione o documento e conclua a etapa
   **Contexto**. Na aba **Vetores**, mantenha o perfil **Agente Analista —
   parágrafos e relato inteiro** para alimentar o agente e clique em
   **Gerar notebook para o Colab**. O navegador baixa um arquivo `.ipynb`.
2. Abra <https://colab.research.google.com> e use **Arquivo → Fazer upload de notebook**
   para enviar o arquivo baixado.
   Depois de atualizar o projeto, gere um novo notebook: notebooks anteriores
   mantêm o código que foi incorporado quando foram baixados.
3. Se desejar GPU, escolha **Ambiente de execução → Alterar tipo de ambiente
   de execução** antes de executar. A GPU é opcional: a configuração `auto`
   utiliza uma GPU disponível ou CPU. O lote pode ser reduzido na célula de
   configuração para diminuir o consumo de memória.
4. Use **Ambiente de execução → Executar tudo**. O notebook instala as
   dependências em um ambiente próprio, baixa a revisão fixa do E5 Large e
   vetoriza o registro contextual validado. A primeira execução requer
   internet e pode levar alguns minutos para instalar e baixar os pesos.
5. Ao concluir, baixe o ZIP oferecido pelo notebook. Ele contém
   `vetorizacao.json`, `contexto_etapa08.json`, `manifesto_colab.json`,
   `original.txt` e `trabalho.txt`. O JSON completo também fica disponível
   na pasta de resultados do notebook.

O notebook leva o texto original, seus metadados e os registros completos
das etapas 03–08, com seus identificadores, intervalos e hashes. Inclui uma
cópia dos módulos necessários do Língua, conferida por SHA-256, e o perfil
selecionado; gerar o arquivo dispensa o E5 instalado localmente e não cria
um trabalho de vetorização no Mac. Ao carregar o notebook no Colab, o texto
e esse histórico também são enviados ao Google.

Cada execução cria uma pasta de resultados com um novo identificador,
preservando os resultados anteriores. O manifesto registra a origem e a
configuração efetiva da execução. O pacote baixado não é importado
automaticamente para o banco do aplicativo; seu JSON pode ser usado nos
outros aplicativos que consumirem esse formato.

Na célula de configuração, `DISPOSITIVO`, `LOTE` e `OPCOES_MODELO` permitem
ajustar a execução. O notebook inicial utiliza o adaptador
`sentence_transformers`; a escolha de outro modelo exige sua revisão e
um perfil compatível. Essa opção de exportação não altera os contratos
multimodelo nem a separação entre espaços vetoriais da etapa 09.

## Dados locais

O banco SQLite é criado automaticamente em `../instance/textos.sqlite3` e não é
versionado nem enviado ao GitHub. Cada computador começa com seu próprio banco
vazio. Para fazer uma cópia de segurança ou transferir os textos, copie esse
arquivo com o aplicativo encerrado.

A tabela `submissions` guarda `id`, `content` e `created_at` (data em UTC no formato
ISO 8601). O conteúdo preserva espaços, acentos e quebras de linha. Textos vazios
são recusados e cada requisição tem limite de 2 MB. Para escolher outro caminho
para o banco, defina a variável `ANALISE_DB` antes de iniciar o aplicativo.
As preparações ficam na tabela `preparations`, no mesmo banco, sem sobrescrever
os textos recebidos. A nova tabela é criada automaticamente ao atualizar o app.
As segmentações ficam na tabela `segmentations`, também local e criada
automaticamente. Cada uma preserva a preparação exata que foi utilizada.
As anotações ficam na tabela `annotations`, acrescentando registros ligados
à segmentação exata utilizada. Atualizar ou gerar outra análise preserva o
original e todos os registros anteriores.
As análises sintáticas e de entidades ficam na tabela `analyses`, vinculadas
à anotação morfológica exata. A tabela é criada automaticamente ao iniciar
o aplicativo atualizado, preservando o banco existente.
As execuções da etapa 07 ficam na tabela `rule_runs`, vinculadas à análise
sintática exata. O registro inclui a origem completa, o catálogo e a
configuração utilizados, preservando também os diagnósticos incompletos.
A tabela é criada automaticamente sem apagar registros anteriores.
As execuções da etapa 08 ficam na tabela `context_runs`, vinculadas à execução
exata de regras. Cada linha conserva sua política e origem; gerar novas
análises, regras ou janelas não altera o histórico contextual.
Os trabalhos e resultados da etapa 09 também ficam no SQLite local. Os
vetores são armazenados em BLOBs com dimensão, formato numérico e hashes;
as representações conservam vínculos com a origem contextual. A atualização
cria as tabelas de forma aditiva, sem substituir textos ou vetores anteriores.

## Sincronizar o código

O repositório principal é `develisrosipsico-dev/lingua`. A continuação de
[spikeSpy13/lingua](https://github.com/spikeSpy13/lingua) foi integrada até a
etapa 08 a partir do commit `886d40f`. A implementação local anterior da etapa
07 foi preservada neste ambiente na branch local `preservacao/etapa07-local`,
commit `110d137`, para comparação durante a integração.

Esta organização está publicada na branch `organizacao/projetos-20261010`.
Antes de editar, execute `git status` e preserve
alterações locais: faça um commit do trabalho concluído ou guarde temporariamente
o trabalho em andamento antes de mudar de branch ou atualizar.

Com a cópia sem alterações pendentes:

```bash
git switch organizacao/projetos-20261010
git pull --ff-only origin organizacao/projetos-20261010
```

Depois de editar, revise com `git diff` e selecione as alterações para o commit
com `git add -p`. Para arquivos novos, use `git add` seguido do caminho de cada
arquivo que deseja incluir. Confira a seleção com `git diff --cached` e execute:

```bash
git commit -m "Descreva sua alteração"
git pull --rebase origin organizacao/projetos-20261010
git push origin organizacao/projetos-20261010
```

Se o rebase apontar conflitos, resolva-os antes de continuar com
`git rebase --continue`; use `git rebase --abort` para voltar ao estado anterior
ao rebase. Evite `push --force` para preservar o histórico compartilhado.

O Git sincroniza o código; o banco e o ambiente virtual permanecem locais.
Depois de atualizar para a etapa 05, instale as dependências e o modelo português.
As etapas 05 e 06 compartilham esse modelo; a etapa 06 não exige um novo download
quando as dependências já estão instaladas. A etapa 07 não acrescenta dependências.
A etapa 08 também não acrescenta dependências nem exige outro modelo.
A etapa 09 usa o conjunto opcional `requirements-vetorizacao.txt` e um
download explícito dos pesos, conforme as instruções de vetorização acima.
Para a opção de notebook Colab, esse conjunto e os pesos são instalados no
Colab; não precisam ser instalados no Mac.
Na pasta `lingua/`, no macOS ou Linux:

```bash
../.venv/bin/python -m pip install -r requirements.txt
```

No Windows:

```powershell
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Repita a instalação quando `requirements.txt` mudar.

## Verificar

Na pasta `lingua/`, execute os testes com o Python do ambiente virtual.
No macOS ou Linux:

```bash
../.venv/bin/python -m unittest discover -s tests -v
```

No Windows:

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Os testes usam bancos temporários e verificam persistência após reiniciar,
preservação literal e Unicode, mapas CRLF, metadados/hashes, ida e volta pelo
JSON, histórico de preparações e segmentações, contexto dos períodos, erros
explícitos e exibição segura de HTML. A etapa 05 também verifica alinhamento
com os tokens existentes, vocabulário, metadados do modelo e histórico de
anotações. Os exemplos curtos anotados manualmente
servem como referência de avaliação da etapa 05. A etapa 06 verifica árvores
sintáticas, entidades, separadores, intervalos literais e normalizados,
histórico e falhas explícitas, além da execução com o modelo real. A etapa 07
verifica regras com anotações controladas independentes do modelo, exceções,
ambiguidade, fragmentos descontínuos, vínculos, estados de execução, adulterações
do JSON, histórico e API HTTP. Os testes de integração com o modelo real
conferem alinhamento e preservação, sem usá-lo como gabarito linguístico.
A etapa 08 verifica seleções contextuais com referências independentes,
fronteiras de parágrafos, raios e tipos estritos, recortes e hashes exatos,
identidade lógica estável, anotações por proprietário, pendências herdadas,
limites de recursos, adulterações, consultas e histórico com bancos temporários.
A etapa 09 separa testes de contrato com adaptadores controlados dos testes
de inferência real. Verifica formatos e dimensões variáveis, hashes,
preservação dos textos, fragmentação, agregação, derivação, compatibilidade,
histórico, retomada e downloads. Testes estruturais não certificam a
qualidade semântica de um modelo nem equivalência entre mecanismos.
O relato
de três parágrafos e dezesseis períodos ainda
não foi fornecido e suas contagens não foram verificadas.
