#!/usr/bin/env bash
# Ajuste baseado nos oito arquivos enviados por Rodrigo em 11/10/2026.
# Execute dentro do checkout; este script pode estar salvo fora do repositório.
set -euo pipefail

raiz=$(git rev-parse --show-toplevel)
cd "$raiz"
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Python 3 não encontrado. Nenhum ajuste foi aplicado.' >&2
  exit 1
fi
temporario=$(mktemp -d "${TMPDIR:-/tmp}/analista-rodrigo-20261011.XXXXXX")
trap 'rm -rf "$temporario"' EXIT

git fetch --no-tags origin organizacao/projetos-20261010
destino=$(git rev-parse 'FETCH_HEAD^{commit}')
for arquivo in ajuste_rodrigo_20261011.patch ajuste_rodrigo_20261011.json; do
  if ! git show "$destino:agente_analista/scripts/$arquivo" > "$temporario/$arquivo"; then
    printf '%s\n' 'O pacote de atualização não está disponível nessa versão. Nenhum ajuste foi aplicado.' >&2
    exit 1
  fi
done

python3 - "$raiz" "$temporario" "$destino" <<'PY'
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

raiz, temporario = (Path(valor).resolve() for valor in sys.argv[1:3])
destino = sys.argv[3]
arquivos = (
    "agente_analista/validacao_vetorial.py",
    "agente_analista/importacao.py",
    "agente_analista/busca.py",
    "agente_analista/entrada.py",
    "agente_analista/ligacoes.py",
    "agente_analista/app.py",
    "agente_analista/static/app.js",
    "agente_analista/templates/index.html",
)
patch = temporario / "ajuste_rodrigo_20261011.patch"
manifesto = temporario / "ajuste_rodrigo_20261011.json"

def falhar(mensagem, detalhe=""):
    print(mensagem, file=sys.stderr)
    if detalhe:
        print(detalhe.rstrip(), file=sys.stderr)
    raise SystemExit(1)

def sha256(conteudo):
    return hashlib.sha256(conteudo).hexdigest()

def executar(*argumentos):
    return subprocess.run(argumentos, cwd=raiz, capture_output=True, text=True)

def hashes_locais():
    resultado = {}
    for nome in arquivos:
        caminho = raiz
        for parte in Path(nome).parts:
            caminho = caminho / parte
            if caminho.is_symlink():
                falhar(f"O caminho {nome} contém um link simbólico. Nenhum ajuste foi aplicado.")
        if not caminho.is_file():
            falhar(f"Arquivo ausente: {nome}. Nenhum ajuste foi aplicado.")
        resultado[nome] = sha256(caminho.read_bytes())
    return resultado

try:
    dados = json.loads(manifesto.read_text(encoding="utf-8"))
    if (dados["schema_version"] != "1.0.0" or dados["identificacao"] != "ajuste_rodrigo_20261011"
            or set(dados["arquivos"]) != set(arquivos)):
        raise ValueError("Manifesto de arquivos incompatível.")
    antes = {nome: dados["arquivos"][nome]["antes_sha256"] for nome in arquivos}
    depois = {nome: dados["arquivos"][nome]["depois_sha256"] for nome in arquivos}
    if not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
               for v in [*antes.values(), *depois.values(), dados["patch_sha256"]]):
        raise ValueError("Hash inválido no pacote.")
    if sha256(patch.read_bytes()) != dados["patch_sha256"]:
        raise ValueError("O patch falhou na conferência de integridade.")
except (KeyError, TypeError, ValueError, OSError) as erro:
    falhar("Pacote inválido. Nenhum ajuste foi aplicado.", str(erro))

# O patch e seu destino devem pertencer à mesma versão publicada, já fixada.
for nome in arquivos:
    publicado = subprocess.run(["git", "show", f"{destino}:{nome}"], cwd=raiz, capture_output=True)
    if publicado.returncode or sha256(publicado.stdout) != depois[nome]:
        falhar("O pacote não corresponde ao código dessa versão publicada. Nenhum ajuste foi aplicado.", nome)

estatistica = executar("git", "apply", "--numstat", str(patch))
try:
    caminhos_patch = [linha.split("\t")[2] for linha in estatistica.stdout.splitlines()]
except IndexError:
    caminhos_patch = []
if (estatistica.returncode or not caminhos_patch or len(set(caminhos_patch)) != len(caminhos_patch)
        or not set(caminhos_patch) <= set(arquivos)):
    falhar("O patch contém um escopo inválido. Nenhum ajuste foi aplicado.", estatistica.stderr)

atuais = hashes_locais()
if atuais == depois:
    print("O ajuste baseado no seu ZIP já está aplicado. Nenhum arquivo foi alterado.")
    raise SystemExit(0)
if atuais != antes:
    diferentes = [nome for nome in arquivos if atuais[nome] != antes[nome]]
    falhar("O código local mudou desde o ZIP enviado. Nenhum arquivo foi alterado.\n"
           "Envie um novo ZIP desses arquivos para adaptar o ajuste:", "\n".join(diferentes))

conferencia = executar("git", "apply", "--check", str(patch))
if conferencia.returncode:
    falhar("O patch não passou na conferência. Nenhum arquivo foi alterado.", conferencia.stderr)

parent_backup = raiz.parent / "lingua-agente-backups"
parent_backup.mkdir(exist_ok=True)
backup = Path(tempfile.mkdtemp(prefix="ajuste-rodrigo-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-",
                             dir=parent_backup))
try:
    with tarfile.open(backup / "arquivos-antes.tar.gz", "w:gz") as pacote:
        for nome in arquivos:
            pacote.add(raiz / nome, arcname=nome, recursive=False)
    shutil.copyfile(patch, backup / patch.name)
    shutil.copyfile(manifesto, backup / manifesto.name)
    (backup / "versao-publicada.txt").write_text(destino + "\n", encoding="utf-8")
except (OSError, tarfile.TarError) as erro:
    falhar("Falha ao salvar o backup. Nenhum ajuste foi aplicado.", str(erro))

if hashes_locais() != antes:
    falhar("O código mudou durante o backup. Nenhum ajuste foi aplicado.", f"Backup: {backup}")
conferencia = executar("git", "apply", "--check", str(patch))
if conferencia.returncode:
    falhar("O patch deixou de passar na conferência. Nenhum ajuste foi aplicado.", conferencia.stderr)
aplicacao = executar("git", "apply", str(patch))
if aplicacao.returncode:
    falhar(f"A aplicação não foi concluída. Backup: {backup}", aplicacao.stderr)
if hashes_locais() != depois:
    falhar(f"A conferência final encontrou arquivos divergentes. Backup: {backup}")

print("Ajuste aplicado aos oito arquivos de código enviados no ZIP; hashes finais conferidos.")
print(f"Versão publicada: {destino}")
print(f"Backup: {backup}")
print("Reinicie somente o Agente Analista para carregar o ajuste.")
PY
