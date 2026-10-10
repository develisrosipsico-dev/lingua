"""A cópia isolada do agente consome arquivos sem qualquer módulo do Língua."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from test_agente_analista_importacao import (
    construir_exportacao, construir_exportacao_portatil,
)


RAIZ = Path(__file__).resolve().parents[2]


class IndependenciaTests(unittest.TestCase):
    def test_upload_funciona_com_apenas_o_pacote_agente_e_dependencias(self):
        exportacoes = [construir_exportacao(dimensao=1024, limite=512)]
        for versao in ("1.0.0", "1.1.0"):
            exportacoes.append(construir_exportacao_portatil(
                schema_version=versao, dimensao=1024,
            ))
        with TemporaryDirectory() as pasta:
            destino = Path(pasta)
            pacote = destino / "agente_analista"
            shutil.copytree(RAIZ / "agente_analista", pacote,
                            ignore=shutil.ignore_patterns("data", "__pycache__"))
            indice = pacote / "data/Vetor"
            indice.mkdir(parents=True)
            shutil.copyfile(RAIZ / "agente_analista/data/Vetor/manifesto.json",
                            indice / "manifesto.json")
            (destino / "exportacoes.json").write_text(
                json.dumps(exportacoes, ensure_ascii=False), encoding="utf-8",
            )
            # Além da ausência física do outro aplicativo, impeça qualquer
            # import acidental de seus nomes, inclusive depois do startup.
            # Nomes históricos permanecem bloqueados mesmo quando o teste
            # roda numa cópia do agente sem a pasta do outro aplicativo.
            bloqueados = {"app", "preparacao", "segmentacao", "anotacao", "sintaxe_entidades",
                          "regras_linguisticas", "unidades_contexto", "vetorizacao", "perfis_vetorizacao",
                          "modelo_embeddings", "embeddings_e5", "contratos_vetorizacao", "api_narrativas"}
            bloqueados.update(arquivo.stem for arquivo in (RAIZ / "lingua").glob("*.py"))
            script = """
import json,sys
from pathlib import Path
for nome in json.loads(sys.argv[1]):
    sys.modules[nome] = None
from agente_analista.app import criar_app
from agente_analista.importacao import importar_vetorizacao
from agente_analista import ligacoes,modelos,diagnosticar_conexao
app = criar_app({'TESTING':True, 'DATABASE':str(Path.cwd()/'isolado.sqlite3')})
client = app.test_client()
assert client.get('/').status_code == 200
for registro in json.loads(Path('exportacoes.json').read_text(encoding='utf-8')):
    recebido = importar_vetorizacao(registro)
    assert {c['tipo'] for c in recebido['consultas']} == {'periodo','contextual','documento'}
    resposta = client.post('/api/vetorizacoes', json=registro)
    assert resposta.status_code == 201, resposta.get_json()
    resumo = resposta.get_json()
    assert client.get('/api/vetorizacoes/'+resumo['id']).get_json() == resumo
    salvo = client.post('/api/relatos', json={'texto':resumo['relato']['texto'], 'vetorizacao_id':resumo['id']})
    assert salvo.status_code == 201, salvo.get_json()
    busca = client.post('/api/buscas', json={'texto':resumo['relato']['texto'], 'vetorizacao_id':resumo['id'],
        'provedor':'openai', 'modelo':'modelo-teste', 'chave_api':'chave-ficticia'})
    assert busca.status_code == 503, busca.get_json()
assert not any(nome in sys.modules for nome in ('torch','transformers','spacy','sentence_transformers'))
app.extensions['agente_analista']['executor'].shutdown(wait=True)
print('Pacote isolado: upload, histórico e validação sem Língua ou inferência.')
"""
            ambiente = dict(os.environ, PYTHONPATH=str(destino))
            processo = subprocess.run(
                [sys.executable, "-c", script, json.dumps(sorted(bloqueados))],
                cwd=destino, env=ambiente, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(processo.returncode, 0, processo.stdout + processo.stderr)
            self.assertIn("Pacote isolado", processo.stdout)


if __name__ == "__main__":
    unittest.main()
