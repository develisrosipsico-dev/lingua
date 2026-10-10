"""A mudança de pasta conserva dados existentes e permite uma cópia isolada."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


PROJETO = Path(__file__).resolve().parents[1]


class OrganizacaoTests(unittest.TestCase):
    def copiar(self, destino):
        shutil.copytree(PROJETO, destino, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("tests", "docs", "examples",
                                                      "instance", "__pycache__"))

    def executar(self, pasta, script):
        ambiente = dict(os.environ, PYTHONPATH=str(pasta))
        ambiente.pop("ANALISE_DB", None)
        resultado = subprocess.run(
            [sys.executable, "-c", script], cwd=pasta, env=ambiente,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
        return json.loads(resultado.stdout)

    def test_historico_e_cache_continuam_disponiveis_depois_de_mover_projeto(self):
        with TemporaryDirectory() as pasta:
            repositorio = Path(pasta)
            self.copiar(repositorio)
            (repositorio / ".git").mkdir()
            original = self.executar(repositorio, """
import json
from pathlib import Path
from app import create_app
from perfis_vetorizacao import carregar_perfil
# Reproduza os caminhos usados antes da reorganização.
app = create_app({'TESTING':True, 'DATABASE':str(Path.cwd()/'instance/textos.sqlite3')})
resposta = app.test_client().post('/envios', data={'content':'Relato fictício com ação.\\r\\nSegunda linha.'})
assert resposta.status_code == 303
cache = Path(carregar_perfil(opcoes={'cache_modelos':str(Path.cwd()/'instance/modelos')})['cache_modelos'])
cache.mkdir(parents=True)
(cache/'marcador').write_text('cache-anterior', encoding='utf-8')
print(json.dumps({'database':app.config['DATABASE'], 'cache':str(cache), 'url':resposta.headers['Location']}))
""")
            projeto = repositorio / "lingua"
            projeto.mkdir()
            for arquivo in list(repositorio.iterdir()):
                if arquivo.name not in (".git", "instance", "lingua", "__pycache__"):
                    shutil.move(str(arquivo), str(projeto / arquivo.name))
            atualizado = self.executar(projeto, """
import json,sqlite3
from pathlib import Path
from app import create_app
from perfis_vetorizacao import carregar_perfil
app = create_app({'TESTING':True})
assert app.test_client().get('/envios/1').status_code == 200
with sqlite3.connect(app.config['DATABASE']) as banco:
    assert banco.execute('SELECT content FROM submissions').fetchall() == [('Relato fictício com ação.\\r\\nSegunda linha.',)]
    assert banco.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
cache = Path(carregar_perfil()['cache_modelos'])
assert (cache/'marcador').read_text(encoding='utf-8') == 'cache-anterior'
assert not (Path.cwd()/'instance/textos.sqlite3').exists()
print(json.dumps({'database':app.config['DATABASE'], 'cache':str(cache)}))
""")
            self.assertEqual(atualizado["database"], original["database"])
            self.assertEqual(atualizado["cache"], original["cache"])

    def test_copia_independente_funciona_sem_repositorio_ou_agente(self):
        with TemporaryDirectory() as pasta:
            projeto = Path(pasta) / "projeto-isolado"
            self.copiar(projeto)
            resultado = self.executar(projeto, """
import json
from pathlib import Path
from app import create_app
from perfis_vetorizacao import carregar_perfil
app = create_app({'TESTING':True})
assert app.test_client().get('/').status_code == 200
assert Path(app.config['DATABASE']) == Path.cwd()/'instance/textos.sqlite3'
assert Path(carregar_perfil()['cache_modelos']) == Path.cwd()/'instance/modelos'
print(json.dumps({'pagina':True}))
""")
            self.assertTrue(resultado["pagina"])


if __name__ == "__main__":
    unittest.main()
