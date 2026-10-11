#!/usr/bin/env bash
# Aplique somente os ajustes de importação/busca, preservando alterações locais.
# Execute a partir do checkout do repositório, inclusive se este script estiver em /tmp.
set -euo pipefail

raiz=$(git rev-parse --show-toplevel)
cd "$raiz"

arquivos=(
  agente_analista/validacao_vetorial.py
  agente_analista/importacao.py
  agente_analista/busca.py
  agente_analista/entrada.py
  agente_analista/ligacoes.py
  agente_analista/app.py
  agente_analista/static/app.js
  agente_analista/templates/index.html
)
bases=(
  9840df628d8c0d4052cd7a689fb7b12bd7ab52f2
  99a845645d99a7634a9de45296211a7939fec236
  409734defaee7d6835f03b300359d76ea6edf8e0
)

temporario=$(mktemp -d "${TMPDIR:-/tmp}/analista-consulta-flexivel.XXXXXX")
trap 'rm -rf "$temporario"' EXIT

if ! git fetch --no-tags origin organizacao/projetos-20261010; then
  printf '%s\n' 'Não foi possível buscar a atualização. Nenhum arquivo do aplicativo foi alterado.' >&2
  exit 1
fi
destino=$(git rev-parse 'FETCH_HEAD^{commit}')
patch_escolhido=''
base_escolhida=''

for base in "${bases[@]}"; do
  if ! git cat-file -e "$base^{commit}" 2>/dev/null; then
    continue
  fi
  patch_candidato="$temporario/$base.patch"
  git diff --binary "$base" "$destino" -- "${arquivos[@]}" > "$patch_candidato"
  if [[ ! -s "$patch_candidato" ]]; then
    continue
  fi
  if git apply --check "$patch_candidato" 2> "$temporario/$base.erro"; then
    patch_escolhido="$patch_candidato"
    base_escolhida="$base"
    break
  fi
  if git apply --reverse --check "$patch_candidato" 2>/dev/null; then
    printf '%s\n' "Este ajuste já está aplicado aos arquivos verificados (versão $destino)."
    exit 0
  fi
done

if [[ -z "$patch_escolhido" ]]; then
  printf '%s\n' 'A atualização não pôde ser aplicada junto das alterações locais.' >&2
  printf '%s\n' 'Nenhum arquivo do aplicativo foi alterado. Preserve seus arquivos e envie os erros abaixo para adaptar o ajuste:' >&2
  for erro in "$temporario"/*.erro; do
    if [[ -f "$erro" ]]; then
      cat "$erro" >&2
    fi
  done
  exit 1
fi

parent_backup="$raiz/../lingua-agente-backups"
mkdir -p "$parent_backup"
backup=$(mktemp -d "$parent_backup/consulta-flexivel-$(date +%Y%m%d-%H%M%S)-XXXXXX")
cp "$patch_escolhido" "$backup/atualizacao.patch"
if ! tar -czf "$backup/arquivos-antes.tar.gz" "${arquivos[@]}"; then
  printf '%s\n' "Falha ao criar o backup em $backup. Nenhum ajuste foi aplicado." >&2
  exit 1
fi

# Confira novamente após o backup. Sem --reject, uma falha impede o conjunto todo.
if ! git apply --check "$patch_escolhido" || ! git apply "$patch_escolhido"; then
  printf '%s\n' "A aplicação falhou. O Git não aplicou o ajuste parcialmente; o backup está em $backup." >&2
  exit 1
fi

printf '%s\n' "Ajuste aplicado somente aos arquivos do Analista listados no script."
printf '%s\n' "Versão de origem: $base_escolhida"
printf '%s\n' "Versão aplicada: $destino"
printf '%s\n' "Backup: $backup"
printf '%s\n' 'Reinicie somente o Agente Analista para carregar o ajuste.'
