"""CLI da etapa 09: uma execução nova a partir de uma saída validada da 08."""

import argparse
import json
from pathlib import Path
import sys

from vetorizacao import vetorizar_contexto


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contexto", type=Path, help="JSON integral da etapa 08.")
    parser.add_argument("--saida", type=Path, required=True,
                        help="Novo JSON; um arquivo existente nunca é sobrescrito.")
    parser.add_argument("--perfil", default="e5_analista")
    parser.add_argument("--opcoes", default="{}", help="Opções do perfil como objeto JSON.")
    parser.add_argument("--execucao-id")
    parser.add_argument("--registrado-em", help="Data ISO 8601 com fuso; omitida usa o instante atual.")
    args = parser.parse_args(argv)
    try:
        if args.saida.exists():
            raise ValueError("A saída já existe. Escolha outro arquivo para preservar o experimento anterior.")
        with args.contexto.open(encoding="utf-8", newline="") as arquivo:
            contexto = json.load(arquivo)
        opcoes = json.loads(args.opcoes)

        def progresso(estado):
            print(f"{estado.get('etapa', 'vetorizacao')}: "
                  f"{estado.get('concluidos', 0)}/{estado.get('total', 0)}", file=sys.stderr)

        registro = vetorizar_contexto(contexto, perfil_id=args.perfil, opcoes=opcoes,
                                     execucao_id=args.execucao_id, registrado_em=args.registrado_em,
                                     progresso=progresso)
        texto = json.dumps(registro, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        # Modo exclusivo também protege contra outra execução criada durante a inferência.
        with args.saida.open("x", encoding="utf-8", newline="") as arquivo:
            arquivo.write(texto)
        print(str(args.saida))
        return 0
    except (ValueError, OSError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
