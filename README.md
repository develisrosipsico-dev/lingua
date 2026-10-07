# Lingua

Projeto independente de análise linguística, em Python com interface HTML.
Recebe um texto, registra o original em SQLite, gera uma preparação validada
e segmenta sua cópia de trabalho em tokens, períodos e parágrafos. Os registros
JSON contêm versões, hashes SHA-256, metadados e posições que apontam até o
original. A interpretação linguística será uma etapa posterior.

## Obter uma cópia local

Requer Git e Python de 3.10 a 3.14, com `pip`. A dependência spaCy 3.8.16
aceita Python anterior a 3.15. No terminal:

```bash
git clone --branch main https://github.com/develisrosipsico-dev/lingua.git
cd lingua
```

O repositório público pode ser clonado sem login; para enviar alterações, autentique
sua conta no GitHub Desktop ou configure a autenticação Git no terminal.

## Executar

Execute os comandos na pasta `lingua`. A instalação inicial das dependências
precisa de internet; depois, o aplicativo funciona localmente, sem serviços externos.

### macOS ou Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

### Windows (PowerShell ou Prompt de Comando)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Abra <http://127.0.0.1:5001> no navegador do mesmo computador, cole o texto e
clique em **Registrar texto**. A confirmação mostra o identificador do registro
e a data em horário de Brasília. Atualizar a confirmação não repete o envio.
Mantenha o terminal aberto durante o uso e pressione `Ctrl+C` para encerrar.

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
quando houver versões anteriores, e clique em **Tokenizar e segmentar** no
painel dessa preparação. O resultado mostra as contagens e os
períodos; **Baixar segmentação JSON** exporta o registro completo. Cada geração
acrescenta um registro ao histórico, vinculado à preparação escolhida, sem
modificar o texto original ou resultados anteriores.

A etapa 04 usa `spacy.blank("pt")` e regras próprias versionadas para períodos,
sem baixar um modelo linguístico. Ela prepara a estrutura para a etapa 05;
suas fronteiras podem precisar de revisão em textos ambíguos.
Veja [uso direto, regras e API HTTP](docs/segmentacao.md) e o
[exemplo de segmentação](examples/segmentacao.json).

## Dados locais

O banco SQLite é criado automaticamente em `instance/textos.sqlite3` e não é
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

## Sincronizar o código

Trabalhamos na branch `main`. Antes de editar, execute `git status` e preserve
alterações locais: faça um commit do trabalho concluído ou guarde temporariamente
o trabalho em andamento antes de mudar de branch ou atualizar.

Com a cópia sem alterações pendentes:

```bash
git switch main
git pull --ff-only origin main
```

Depois de editar, revise com `git diff` e selecione as alterações para o commit
com `git add -p`. Para arquivos novos, use `git add` seguido do caminho de cada
arquivo que deseja incluir. Confira a seleção com `git diff --cached` e execute:

```bash
git commit -m "Descreva sua alteração"
git pull --rebase origin main
git push origin main
```

Se o rebase apontar conflitos, resolva-os antes de continuar com
`git rebase --continue`; use `git rebase --abort` para voltar ao estado anterior
ao rebase. Evite `push --force` para preservar o histórico compartilhado.

O Git sincroniza o código; o banco e o ambiente virtual permanecem locais.
Depois de atualizar para a etapa 04, instale a nova dependência spaCy.
Na raiz `lingua`, no macOS ou Linux:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

No Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Repita a instalação quando `requirements.txt` mudar.

## Verificar

Na raiz `lingua`, execute os testes com o Python do ambiente virtual.
No macOS ou Linux:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

No Windows:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Os testes usam bancos temporários e verificam persistência após reiniciar,
preservação literal e Unicode, mapas CRLF, metadados/hashes, ida e volta pelo
JSON, histórico de preparações e segmentações, contexto dos períodos, erros
explícitos e exibição segura de HTML. Os exemplos curtos anotados manualmente
servem como referência; o relato de três parágrafos e dezesseis períodos ainda
não foi fornecido e suas contagens não foram verificadas.
