# Língua e Agente Analista

Este repositório contém dois aplicativos independentes:

| Pasta | Projeto | Instruções |
| --- | --- | --- |
| [`lingua/`](lingua/) | Análise linguística, contexto e vetorização | [README do Língua](lingua/README.md) |
| [`agente_analista/`](agente_analista/) | Busca de relações entre relatos e o acervo teórico | [README do Agente Analista](agente_analista/README.md) |

Cada pasta contém o código, os recursos, as dependências e os testes de seu
projeto. O agente recebe `vetorizacao.json` por upload e usa os vetores já
calculados; os aplicativos não importam código um do outro.

Os arquivos locais ignorados pelo Git permanecem onde já estavam: `.venv/`
e `instance/` na raiz do repositório. Isso preserva os bancos, os ambientes
Python e o cache de modelos existentes. O índice do agente continua em
`agente_analista/data/Vetor/`.

## Atualizar uma cópia existente

Com os aplicativos encerrados e sem alterações locais pendentes, execute
na pasta do repositório:

```bash
git fetch origin
git switch organizacao/projetos-20261010
git pull --ff-only origin organizacao/projetos-20261010
```

A reorganização está nessa branch; a `main` mantém o estado anterior até
a integração das alterações.

## Executar o Língua

Na raiz do repositório, usando o ambiente virtual existente:

```bash
.venv/bin/python -m pip install -r lingua/requirements.txt
.venv/bin/python lingua/app.py
```

O aplicativo atende em <http://127.0.0.1:5001>. Se ainda não existir um
ambiente virtual, crie-o antes com `python3 -m venv .venv`.
Consulte o [manual do Língua](lingua/README.md) para Windows, vetorização,
Colab e as etapas da análise.

## Executar o Agente Analista

No Mac já preparado de Rodrigo, a partir da raiz do repositório:

```bash
instance/venv-agente-e5-intel/bin/python -m pip install -r agente_analista/requirements.txt
instance/venv-agente-e5-intel/bin/python -m agente_analista.app
```

O aplicativo atende em <http://127.0.0.1:5002>. Outro ambiente Python com
as dependências do agente também pode ser usado. Consulte o
[manual do agente](agente_analista/README.md) para importar a vetorização,
configurar as justificativas e consultar o histórico.

## Verificar cada projeto

Na raiz do repositório, com as dependências correspondentes instaladas:

```bash
cd lingua
../.venv/bin/python -m unittest discover -s tests -v
```

Para os testes do agente, execute na raiz do repositório:

```bash
.venv/bin/python -m unittest discover -s agente_analista/tests -p 'test_agente_analista_*.py' -v
```

Os testes usam dados e bancos temporários. As verificações opcionais com
o índice real e uma exportação vetorial real estão descritas no manual do agente.
