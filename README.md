# Lingua

Projeto independente de análise linguística, em Python com interface HTML.
Nesta primeira etapa, recebe um texto e registra o conteúdo original em SQLite,
com um identificador e a data do envio. A análise linguística e a exportação de
pacotes de dados serão etapas posteriores.

## Obter uma cópia local

Requer Git e Python 3.10 ou superior, com `pip`. No terminal:

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

## Dados locais

O banco SQLite é criado automaticamente em `instance/textos.sqlite3` e não é
versionado nem enviado ao GitHub. Cada computador começa com seu próprio banco
vazio. Para fazer uma cópia de segurança ou transferir os textos, copie esse
arquivo com o aplicativo encerrado.

A tabela `submissions` guarda `id`, `content` e `created_at` (data em UTC no formato
ISO 8601). O conteúdo preserva espaços, acentos e quebras de linha. Textos vazios
são recusados e cada requisição tem limite de 2 MB. Para escolher outro caminho
para o banco, defina a variável `ANALISE_DB` antes de iniciar o aplicativo.

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
Se `requirements.txt` mudar, execute novamente o comando de instalação de
dependências correspondente ao seu sistema.

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

Os testes usam bancos temporários e verificam persistência após reiniciar o
aplicativo, preservação do texto, validação dos envios e exibição segura de HTML.
