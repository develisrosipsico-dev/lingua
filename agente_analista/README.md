# Agente analista: relato e Freud

Página local para examinar ligações entre um relato em português e o acervo
fornecido. Importa o arquivo completo `vetorizacao.json` produzido pela análise
linguística e reaproveita seus vetores de períodos, janelas contextuais e
documento. O relato deve ter exatamente dois parágrafos e até 400 palavras.
A busca consulta todos os 8.087 fragmentos e termina em uma tabela de propostas
interpretativas. O agente não executa uma nova vetorização do relato.

O Agente Analista e a análise linguística são aplicativos independentes. A
integração ocorre pelo arquivo exportado: o agente executa sem instalar,
importar código ou manter a análise linguística aberta. Para cada relato,
importe somente **vetorizacao.json** pelo controle **Importar vetorização**.
O texto e os metadados incluídos nesse arquivo permitem conferir os vetores e
as posições das passagens; não é necessário carregar outros arquivos da
análise linguística.

## Executar no Mac Intel

Na pasta do repositório:

```bash
cd ~/lingua-agente
git fetch origin
git switch agente-analista/preparacao-20261010
git pull --ff-only origin agente-analista/preparacao-20261010
instance/venv-agente-e5-intel/bin/python -m pip install -r agente_analista/requirements.txt
instance/venv-agente-e5-intel/bin/python -m agente_analista.app
```

Os comandos usam o ambiente Python já preparado no Mac. As dependências da
página e da busca são Flask, Certifi e NumPy. A chave das justificativas é
informada na página.

Abra **http://127.0.0.1:5002** no navegador. Deixe o terminal aberto enquanto usa
a página; pressione `Ctrl+C` para encerrar. O agente precisa do índice vetorial
do acervo, mas não carrega o E5 nem baixa seus pesos para realizar consultas.

Na análise linguística, gere a vetorização do relato e exporte o arquivo
completo **vetorizacao.json**. Na seção **Importar vetorização**,
selecione esse arquivo. O agente confere os dados, guarda a importação no banco
local, preenche o texto e mostra a quantidade de vetores por tipo. O limite do
arquivo é 32 MiB. O arquivo exportado reúne os vetores e as informações do
relato necessárias para a busca.

Na seção **Justificativas**, escolha o provedor, cole a chave de API no campo
oculto e use **+** para incluir o identificador de um modelo. O seletor começa
sem modelo selecionado, sem modelos predefinidos. Ao incluir um modelo, ele
é selecionado para a próxima busca. Depois de importar a vetorização, clique
em **Buscar ligações**. Editar o texto invalida a associação aos vetores;
nesse caso, faça a vetorização do texto atualizado na análise linguística e
importe o novo arquivo antes de buscar. O relato original, inclusive suas
quebras de linha, é preservado no servidor.

Os vetores precisam usar o mesmo espaço do índice do acervo. A importação
confere os metadados do modelo, revisão, dimensão, normalização e perfil de
consulta, além dos valores, textos e posições. O E5 da análise linguística
continua pertencendo à etapa de origem e pode alimentar diferentes agentes.

### Chave e modelo das justificativas

A configuração é feita na página. OpenRouter aparece como provedor inicial;
OpenAI também pode ser escolhido. Cada modelo adicionado pertence ao provedor
selecionado, e a lista mostra somente os modelos desse provedor. Exemplos de
identificadores: `openai/gpt-4.1-mini` no OpenRouter e `gpt-4.1-mini` na OpenAI.
Antes de buscar, o servidor consulta o catálogo público do OpenRouter para
conferir o identificador e as capacidades do modelo selecionado. Se houver
suporte a respostas estruturadas, usa **JSON Schema**; se houver somente modo
JSON, usa um objeto JSON; nos demais modelos, solicita JSON pelas instruções.
Todas as respostas passam pelas mesmas conferências locais de estrutura,
citações e fontes. Modelos com o sufixo `:free` continuam com esse identificador;
não há troca automática de modelo nem repetição da chamada de avaliação.

A consulta ao catálogo não envia chave ou relato e não gera texto. As capacidades
ficam em memória por cinco minutos. Se o catálogo estiver indisponível ou não
informar as capacidades, a busca para com uma orientação específica. Para
OpenAI, a avaliação continua usando JSON Schema. Quando um formato é exigido
no OpenRouter, a requisição seleciona uma rota que respeite esse formato.

Somente a lista de identificadores dos modelos é guardada no navegador. A
chave fica no campo oculto enquanto a página está aberta e é enviada ao servidor
local para cada busca. Não é gravada em arquivos, no armazenamento do navegador
ou nos resultados exportados; ao recarregar a página, o campo começa vazio.
Não há necessidade de configurar variáveis no terminal. A configuração enviada
na página é usada somente naquela busca, sem alterar as credenciais do projeto
Lingua ou as variáveis de ambiente do servidor.

O índice e os vetores importados são consultados localmente. Para construir as justificativas, o
provedor recebe o relato e os blocos recuperados por HTTPS; essa chamada usa
a conta do provedor e pode consumir créditos. A busca é liberada após informar
a chave, selecionar o modelo e importar a vetorização de um relato válido. **Salvar relato**
guarda o texto e, quando houver, o vínculo com a vetorização importada, sem enviar dados ao provedor ou consumir
créditos. O relato precisa seguir os mesmos limites de dois parágrafos e até
400 palavras. Uma busca também salva automaticamente o relato e seu resultado
no banco local. As chaves de API não fazem parte dos registros salvos.

Os relatos e resultados permanecem disponíveis depois de encerrar e reiniciar
o servidor. O acompanhamento das buscas em andamento continua em memória,
limitado a oito buscas por uma hora; essa limpeza não apaga o histórico salvo.
**Exportar JSON** continua disponível para guardar uma cópia de cada resultado.

### Executar diretamente no ambiente Python

Depois de instalar `agente_analista/requirements.txt` em um ambiente Python:

```bash
instance/venv-agente-e5-intel/bin/python -m agente_analista.app
```

O servidor escuta somente em `127.0.0.1:5002`, separado do Lingua na porta 5001.
Usa uma execução por vez e mostra o progresso na página. O nome histórico do
ambiente Python não indica necessidade de carregar o E5; também é possível
usar outro ambiente com as dependências do agente instaladas.

### Diagnosticar a conexão com o provedor

Se precisar conferir a conexão, execute opcionalmente o diagnóstico abaixo.
Ele usa OpenRouter por padrão; a escolha do provedor das buscas é feita na
página. O teste apenas consulta o endereço do provedor por GET, sem
autenticação, envio do relato ou geração de texto; não consome créditos:

```bash
instance/venv-agente-e5-intel/bin/python -m agente_analista.diagnosticar_conexao
```

As mensagens distinguem certificados HTTPS, DNS, tempo de conexão e bloqueios
de proxy. Receber HTTP 401 no teste da OpenAI é esperado, pois a consulta não
envia chave; confirma conexão HTTPS, sem verificar autenticação ou saldo.

O transporte da API soma os certificados do Certifi aos certificados nativos
do Python, preservando a verificação de certificado e de hostname. Isso atende
ao Python instalado pelo site oficial no Mac, mesmo quando os certificados
globais ainda não foram preparados. Caminhos explícitos `SSL_CERT_FILE`,
`SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE` e `CURL_CA_BUNDLE` são respeitados.

Se o diagnóstico ainda indicar falha de certificado e o instalador oficial
estiver disponível, execute:

```bash
open "/Applications/Python 3.12/Install Certificates.command"
```

Depois, repita o diagnóstico. Se houver proxy ou VPN na rede, siga a indicação
do diagnóstico. Chave inválida e saldo insuficiente têm mensagens próprias
quando a chamada de avaliação consegue alcançar o provedor.

## Como a busca funciona

- `entrada.py` conta palavras por espaços em branco e localiza os parágrafos,
  aceitando separadores com linhas vazias contendo espaços/tabulações. O texto
  recebido permanece intacto. Todas as posições usam pontos de código Unicode,
  base zero, intervalo `[inicio, fim)`; emojis contam como um ponto de código.
- `corpus.py` carrega o manifesto e a matriz por mmap, valida IDs, dimensões,
  valores finitos, normalização L2 e hashes dos textos. Reconstrói blocos pelas
  posições, reúne sobreposições idênticas e rejeita lacunas/conflitos.
- A importação recebe o registro completo da vetorização da análise
  linguística. Cada vetor mantém seu tipo, texto e posição de origem. As
  consultas de períodos, janelas contextuais e documento saem desse registro;
  não há nova inferência E5, divisão por tokens ou processamento linguístico
  no agente. Um texto diferente exige uma nova exportação correspondente.
- A busca lexical usa BM25 (`k1=1.2`, `b=0.75`), palavras em minúsculas sem
  acentos, remoção de palavras funcionais e preservação de negações. O código
  anterior no ZIP serviu de consulta para a fórmula; radicalização não é usada.
- A busca semântica compara os vetores importados com todas as linhas da matriz
  por similaridade cosseno. A fusão usa RRF: soma
  `peso_tipo/(consultas_do_tipo * (60 + rank_bloco))` por método e consulta,
  sobre os 50 melhores fragmentos. Períodos, janelas contextuais e documento
  têm peso de `1/3` cada; as contribuições são divididas pela quantidade de
  consultas do respectivo tipo para equilibrar o total. Cada bloco contribui
  uma vez por ranking. Os 12 melhores blocos
  distintos seguem com seu contexto inteiro para avaliação; nada é filtrado
  previamente por obra ou conceito. Os parâmetros saem no JSON.
- `modelos.py` consulta o catálogo do OpenRouter e ajusta o formato aceito pelo
  modelo escolhido antes da busca, sem mudar seu identificador.
- `ligacoes.py` usa um prompt próprio e o formato aceito pelo modelo para propor relações,
  considerando o relato completo, negações, contexto, limites e alternativas.
  Intervalos de citações são oferecidos com posições já calculadas. O servidor
  rejeita passagens, IDs ou intervalos sem correspondência literal e associa
  somente as referências e pontuações provenientes do índice.
  Se os contextos excederem o limite de envio, candidatos inteiros são retirados
  do fim do ranking até o pedido caber. Os textos continuam intactos; o JSON e
  a inspeção registram quais blocos foram efetivamente avaliados.
  O limite inclui o esquema JSON e os parâmetros de roteamento no pedido real.
  Uma resposta inteiramente envolvida em um único bloco Markdown `json` também
  pode ser lida, sem alterar o JSON ou suas citações. Respostas incompletas,
  texto externo ao JSON, campos repetidos e valores inválidos são rejeitados.
  A conferência das fontes continua obrigatória em todos os formatos; não há
  novas tentativas automáticas ou troca automática de modelo.
  Os pedidos OpenRouter não incluem o parâmetro redundante `n`; o retorno
  continua limitado à conferência de uma única resposta completa.

Erros de HTTP 400, 404 e 422 incluem o código e distinguem causas conhecidas,
como modelo ausente, rota incompatível e contexto acima da janela do modelo.
O corpo de erro do provedor é usado somente para essa classificação; não é
mostrado na página ou escrito nos resultados.

## Examinar os resultados

Cada linha apresenta passagem do relato, trecho de Freud, ligação proposta,
justificativa, referência e limites/situação. Os detalhes permitem inspecionar
o contexto, IDs, posições e pontuações. Relações descartadas e propostas que
falharam na conferência ficam em áreas separadas. Há controles para limpar,
copiar a tabela e exportar o JSON completo.

### Relatos e relações salvos

O banco SQLite fica, por padrão, em `instance/agente_analista.sqlite3`, separado
dos dados do Lingua. Para usar outro arquivo, defina `AGENTE_ANALISTA_DB` com
seu caminho antes de iniciar o aplicativo. Não há servidor de banco de dados
nem dependência adicional a instalar.

A tabela `relatos` guarda o texto original, o vínculo com a vetorização
importada e os dados da busca. A terceira tabela, `vetorizacoes`, mantém os
arquivos importados para permitir novas consultas depois de reiniciar o
aplicativo. O vínculo `relatos.vetorizacao_id` referencia essa tabela. A tabela
`relacoes` guarda as relações conferidas, com chave estrangeira `relato_id`,
passagem do relato, trecho de Freud e justificativa, além das referências e
dos detalhes necessários para inspecionar o resultado. Relações pertinentes,
parciais e descartadas são preservadas. Propostas rejeitadas por falharem nas
conferências não se tornam registros nessa tabela; ficam disponíveis para
inspeção no resultado original.

Use **Salvar relato** para guardar um texto antes de configurar a busca.
Salvar novamente sem editar não cria outra cópia. Ao buscar um relato salvo
que ainda não tem busca, o resultado é associado àquele registro. Repetir
uma busca já realizada cria um novo registro e preserva o resultado anterior.

A seção **Relatos salvos** mostra o histórico, com **Atualizar**, **Carregar
mais** e **Abrir**. Abrir um registro recupera o texto e a tabela sem fazer
outra chamada ao provedor e sem chave de API. Quando existe uma vetorização
associada, ela também é recuperada e fica disponível para uma nova busca sem
importar o arquivo novamente. Registros antigos sem vetorização continuam
acessíveis; para uma nova busca, importe a exportação correspondente. **Limpar**
limpa apenas o formulário e o resultado exibido; não exclui registros do banco.
Não há exclusão automática dos relatos salvos. Para fazer um backup, encerre
o aplicativo e copie o arquivo SQLite. O arquivo permanece no computador
onde o servidor está sendo executado.

Uma busca pode terminar sem ligações utilizáveis. Isso significa que os
candidatos examinados não sustentaram uma relação suficiente, não que o acervo
inteiro careça de material pertinente. Conferências automáticas de integridade
não substituem a avaliação da pertinência pelo usuário.

As páginas são apresentadas como **“páginas registradas no índice; numeração
impressa não conferida”**. Edição, tradutor, editora e ano da edição permanecem
pendentes quando ausentes. A tabela encerra esta etapa; a escrita da resposta
final fica para uma etapa posterior.

## Verificar

No ambiente preparado:

```bash
instance/venv-agente-e5-intel/bin/python -m unittest discover -s tests -p 'test_agente_analista_*.py' -v
```

Para verificar somente o banco e sua integração com a API, sem E5 ou chamadas
externas, use o Python do ambiente que tenha Flask instalado, na raiz do
repositório:

```bash
PYTHONPATH="$PWD/tests" .venv/bin/python -m unittest test_agente_analista_persistencia -v
```

Esses testes usam bancos temporários e não alteram os relatos do usuário.

Para incluir a busca sobre o índice original e uma exportação real já existente,
informe o caminho do arquivo no Mac:

```bash
AGENTE_ANALISTA_VETORIZACAO_REAL="/caminho/vetorizacao.json" \
  instance/venv-agente-e5-intel/bin/python -m unittest discover \
  -s tests -p 'test_agente_analista_*.py' -v
```

Essa verificação usa os vetores recebidos e simula as respostas do provedor.
O índice original precisa estar em `agente_analista/data/Vetor/`.

Os testes verificam importação e reaproveitamento dos vetores, limites de entrada, preservação Unicode e posições,
integridade e reconstrução do corpus, busca e agrupamento, citações e IDs
inexistentes, relações sem sustentação, o fluxo assíncrono, a persistência dos
registros e a separação das credenciais de cada busca. As respostas do
provedor são simuladas nos testes para evitar chamadas cobradas. A consulta
com um arquivo real exportado pela análise linguística e o índice completo
do acervo deve ser validada no ambiente que contém esses dados.

O código antigo continua guardado, sem execução automática, em
`codigos/freud-agent-codigo-20261003-165655.zip`.
