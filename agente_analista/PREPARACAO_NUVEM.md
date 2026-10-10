# Preparação parcial do Agente Analista — 10/10/2026

## Organização dos projetos

Na branch `organizacao/projetos-20261010`, o código do Língua está em
`lingua/` e o agente continua em `agente_analista/`. Os testes do agente estão
em `agente_analista/tests/`; seus comandos atuais estão no [README](README.md).
Os quatro arquivos originais antes preservados na raiz foram movidos, sem
alteração de conteúdo, para [legado/](legado/README.md). Os bancos, ambientes
Python e caches locais permanecem em `instance/` e `.venv/` na raiz do
repositório. As referências de caminhos e os comandos do registro histórico
abaixo descrevem a disposição anterior à reorganização.

## Atualização: entrada por arquivo vetorial

O fluxo atual recebe um único `vetorizacao.json` pelo upload da página e usa
os vetores já calculados de períodos, janelas contextuais e documento. O agente
não gera embeddings, não importa módulos do Língua e não depende de sua
instalação ou execução. Os módulos de leitura e transporte pertencem ao
pacote `agente_analista`; os arquivos existentes do outro projeto foram
preservados. A antiga dependência de `_dividir` foi removida.

Consulte [README.md](README.md) para os comandos e o fluxo atuais. O histórico
SQLite preserva os registros anteriores e vincula os novos relatos à exportação
importada. A matriz original do acervo continua ausente nesta nuvem; a busca
com o arquivo vetorial real e o índice do Mac ainda precisa ser conferida nesse
ambiente. O agente não exige o cache do E5 para usar os vetores recebidos.

A suíte atual executou 160 testes: 157 aprovados e três pulados por ausência
da matriz original ou da exportação real opcional. O teste de pacote isolado
bloqueou todos os módulos Python da raiz e aprovou upload e persistência.
O Chromium validou upload único, busca, preservação de CRLF, histórico,
invalidação após edição e chave transitória, usando corpus e vetores controlados
com transporte externo simulado. Isso não valida a qualidade semântica do E5
real. Nenhum relato de teste foi inserido no banco padrão.

## Registro da preparação inicial

As verificações, decisões e comandos abaixo documentam o estado anterior à
entrada por arquivo vetorial. As instruções operacionais atuais estão no README.

Esta preparação mantém os caminhos do pacote de Rodrigo e preserva todos os
arquivos já versionados do Lingua. A integração da busca permanece pendente,
conforme a escolha feita neste atendimento. O ambiente não está pronto para
uma busca completa na nuvem.

## Referências

- Base conferida da `main`: `3e3b69036534d8523fdea35b7e127eb39e9ebd5c`.
- Pacote principal: `agente-analista-codigo-20261010-074528.zip`.
- SHA-256 do ZIP:
  `984673c8c69b70d804e9934c678e1f02667423acb77f4c1864a5be48ce6e5551`.
- O `vetorizacao.py` recebido separadamente é idêntico ao arquivo do ZIP.
  SHA-256:
  `0e48788afd82135e1cdf9f790f1ea0ccecd09592b14ece5f89a66d03bff7fcec`.
- Pasta de trabalho informada por Rodrigo:
  `/Users/rodrigodevellis/lingua-agente`.
  A cópia em Downloads não é a pasta de trabalho.

## Arquivos incorporados

Os quatro módulos/manifesto novos na raiz mantêm os caminhos originais do ZIP:

- `api_narrativas.py`
- `contratos_vetorizacao.py`
- `embeddings_e5.py`
- `requirements-embeddings.txt`

Também foram incorporados, sem alterações, os seis arquivos
`tests/test_agente_analista_*.py` do pacote: app, busca, conexão,
credenciais, ligações e modelos.

Esses dez arquivos coincidem byte a byte com o ZIP. Os arquivos já existentes
em `agente_analista/`, incluindo `corpus.py`, já coincidiam com o pacote.
Nenhum arquivo já versionado foi substituído. Não foram incorporadas as
versões do ZIP de app, regras, persistência, templates, documentação ou
vetorização do Lingua.

## Verificações executadas na nuvem

- Suíte original do Lingua, antes de adicionar os novos testes do agente:
  589 testes executados, 588 aprovados e 1 opcional pulado.
- Ligações, modelos e diagnóstico de conexão do agente:
  60 testes executados e aprovados.
- Adaptador E5, com o teste original externo do ZIP:
  25 testes executados, 24 aprovados e 1 inferência real opt-in pulada.
- Suíte completa disponível do agente:
  93 testes executados, resultado com 3 falhas, 4 erros e 1 teste pulado.
  A ausência de `_dividir` impede a coleta dos testes de busca e causa os
  demais erros/falhas ao importar a busca no serviço e no tratamento de erro.
  Contagens de falhas/erros incluem subtestes; não representam sete casos
  distintos aprovados/reprovados.
- A tentativa adicional de coletar `test_api_narrativas` do pacote não
  conseguiu importar `narrativas`, módulo do outro fluxo não incorporado.
  Isso não valida a suíte completa desse módulo.
- Manifesto, textos, IDs, hashes, posições e reconstrução conferidos:
  8.087 fragmentos e 6.249 blocos. Consulta BM25 textual validada.
  A matriz real não foi validada nem simulada.
- Página do agente respondeu HTTP 200. `/api/status` informou
  `pronto: false`, por ausência do índice completo e do cache/configuração E5.
- `pip check`: nenhuma dependência incompatível encontrada.
  Foram instalados Torch 2.6.0 CPU e as dependências fixadas em
  `requirements-embeddings.txt` nesta máquina.
- Download de configuração do Hugging Face bloqueado pelo proxy (HTTP 403);
  nenhum peso E5 real foi baixado.

Comando dos 60 testes independentes, a partir da raiz do checkout:

```bash
PYTHONPATH="$PWD/tests" .venv/bin/python -m unittest \
  test_agente_analista_ligacoes \
  test_agente_analista_modelos \
  test_agente_analista_conexao -v
```

A descoberta global `unittest discover -s tests` passa a incluir os testes
novos do agente e permanece bloqueada pela integração descrita abaixo.
Isso não significa que a suíte original do Lingua deixou de passar.

## Pendências

1. **Divisão de consultas E5.** `agente_analista/busca.py` importa
   `_dividir` da raiz. O arquivo recebido possui a função; o arquivo do Lingua
   atualmente versionado não possui. Substituí-lo na raiz alteraria o Lingua.
   Não houve substituição, extração, mudança de imports ou mudança de caminhos.
   A integração permanece pendente de uma decisão que preserve os projetos.

2. **Índice original.** Falta
   `agente_analista/data/Vetor/vetores_fragmentos_e5.npy` nesta máquina e
   no material anexado. O manifesto exige matriz `float32` com formato
   `(8087, 1024)`, valores finitos, normalização L2 e a ordem exata do JSONL.
   Não foram gerados vetores substitutos.

3. **Pacote completo.** O material anexado não inclui
   `agente_analista/preparar_e5_mac_intel.sh` nem
   `agente_analista/iniciar_mac_intel.sh`. É necessário conferir os originais
   do pacote completo `agente_analista.zip`; não foram inventados scripts.

4. **E5 real.** Rodrigo informou que o modelo já está preparado no Mac.
   Esses arquivos locais não estão acessíveis na nuvem. A revisão requerida é
   `3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3` de
   `intfloat/multilingual-e5-large`; configuração em
   `instance/agente_analista_e5.json` e cache em `instance/huggingface`.
   A consulta híbrida real e os 106 testes completos ainda precisam ser
   executados no ambiente que dispõe dos arquivos e da vetorização correta.

## Validação pendente no Mac de Rodrigo

Estes comandos somente verificam o ambiente local; não sincronizam nem
substituem arquivos. Use a pasta de trabalho, não a cópia de Downloads:

```bash
cd /Users/rodrigodevellis/lingua-agente
instance/venv-agente-e5-intel/bin/python -c \
  'from vetorizacao import _dividir; print("Divisor E5 disponível")'
instance/venv-agente-e5-intel/bin/python -m unittest discover \
  -s tests -p 'test_agente_analista_*.py' -v
AGENTE_ANALISTA_TESTE_E5_REAL=1 \
  instance/venv-agente-e5-intel/bin/python -m unittest discover \
  -s tests -p 'test_agente_analista_busca.py' -v
```

A última execução inclui a validação da matriz e uma consulta híbrida E5 real,
usando o cache local já preparado. Ela não chama o provedor de justificativas.
Os comandos acima não foram executados no Mac por este atendimento.

A publicação desta preparação em uma branch não integra os projetos, não
publica o ambiente da nuvem e não comprova que o fluxo de busca funciona.
