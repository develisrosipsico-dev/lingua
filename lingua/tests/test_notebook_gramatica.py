"""Contrato portátil do notebook de preparação do acervo gramatical."""

import ast
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import venv
import zipfile

from notebook_gramatica import gerar_notebook_gramatica


def fonte(celula):
    valor = celula["source"]
    return valor if isinstance(valor, str) else "".join(valor)


class NotebookGramaticaTests(unittest.TestCase):
    def notebook(self):
        return gerar_notebook_gramatica()

    def test_notebook_valido_celulas_python_compilaveis_e_sem_saidas(self):
        notebook = json.loads(json.dumps(self.notebook(), ensure_ascii=False,
                                         allow_nan=False))
        self.assertEqual((notebook["nbformat"], notebook["nbformat_minor"]), (4, 5))
        try:
            import nbformat
        except ImportError:
            nbformat = None
        if nbformat is not None:
            nbformat.validate(nbformat.from_dict(notebook))
        ids = [celula["id"] for celula in notebook["cells"]]
        self.assertEqual(len(ids), len(set(ids)))
        for celula in notebook["cells"]:
            if celula["cell_type"] == "code":
                with self.subTest(celula=celula["id"]):
                    ast.parse(fonte(celula), filename=celula["id"])
                    for no in ast.walk(ast.parse(fonte(celula))):
                        if (isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
                                and no.func.id == "executar" and no.args
                                and isinstance(no.args[0], ast.Constant)):
                            ast.parse(textwrap.dedent(ast.literal_eval(no.args[0])),
                                      filename=celula["id"] + ":subprocesso")
                    self.assertIsNone(celula["execution_count"])
                    self.assertEqual(celula["outputs"], [])

    def test_notebook_independente_dos_aplicativos_e_sem_caminhos_do_mac(self):
        notebook = self.notebook()
        documento = json.dumps(notebook, ensure_ascii=False)
        for proibido in ("/Users/", "git clone", "raw.githubusercontent.com",
                         "ghp_", "github_pat_"):
            self.assertNotIn(proibido, documento)
        for celula in notebook["cells"]:
            if celula["cell_type"] != "code":
                continue
            arvore = ast.parse(fonte(celula))
            for no in ast.walk(arvore):
                if isinstance(no, ast.Import):
                    nomes = [alias.name.split(".")[0] for alias in no.names]
                elif isinstance(no, ast.ImportFrom):
                    nomes = [(no.module or "").split(".")[0]]
                else:
                    continue
                self.assertTrue(set(nomes).isdisjoint({"app", "agente_analista"}),
                                (celula["id"], nomes))

    def test_destino_colab_gpu_e_explicacao_da_memoria_a100(self):
        notebook = self.notebook()
        self.assertEqual(notebook["metadata"].get("accelerator"), "GPU")
        instrucoes = "\n".join(fonte(c) for c in notebook["cells"]
                               if c["cell_type"] == "markdown")
        self.assertIn("A100", instrucoes)
        self.assertIn("80", instrucoes)
        self.assertIn("GPU", instrucoes)

    def test_gerador_nao_exige_modelos_nem_bibliotecas_de_inferencia_instalados(self):
        programa = r'''
import builtins
import json
import runpy
import sys

original = builtins.__import__
proibidos = {"torch", "transformers", "sentence_transformers", "pymupdf", "fitz", "numpy"}

def importar(nome, *args, **kwargs):
    if nome.split(".")[0] in proibidos:
        raise AssertionError("O gerador tentou importar: " + nome)
    return original(nome, *args, **kwargs)

builtins.__import__ = importar
modulo = runpy.run_path(sys.argv[1])
notebook = modulo["gerar_notebook_gramatica"]()
print(json.dumps({"nbformat": notebook["nbformat"], "celulas": len(notebook["cells"])}))
'''
        gerador = Path(__file__).resolve().parents[1] / "notebook_gramatica.py"
        resultado = subprocess.run([sys.executable, "-I", "-c", programa, str(gerador)],
                                   check=False, capture_output=True, text=True, timeout=20)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        dados = json.loads(resultado.stdout)
        self.assertEqual(dados["nbformat"], 4)
        self.assertGreater(dados["celulas"], 1)

    def test_fluxo_portatil_com_pdf_real_sem_rede_e_modelo_controlado(self):
        """Exercita as células e os scripts filhos, sem instalar ou baixar E5."""
        try:
            import pymupdf
        except ImportError:
            self.skipTest("PyMuPDF necessário para o PDF real deste teste.")
        notebook = self.notebook()
        celulas = {c["id"]: fonte(c) for c in notebook["cells"] if c["cell_type"] == "code"}
        fontes_ast = ast.parse(celulas["fontes"])
        fontes = ast.literal_eval(fontes_ast.body[0].value)
        with tempfile.TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            codigo = raiz / "codigo"
            codigo.mkdir()
            for nome, conteudo in fontes.items():
                (codigo / nome).write_text(conteudo, encoding="utf-8")
            # Este módulo local só substitui o tokenizador nos subprocessos do teste.
            (codigo / "transformers.py").write_text(textwrap.dedent(r'''
                import re
                class AutoTokenizer:
                    @classmethod
                    def from_pretrained(cls, *args, **kwargs):
                        return cls()
                    def encode(self, texto, add_special_tokens=True, truncation=False):
                        assert add_special_tokens is True and truncation is False
                        return list(range(len(re.findall(r"\w+|[^\w\s]", texto)) + 2))
            '''), encoding="utf-8")
            pdf = raiz / "gramatica-teste.pdf"
            with pymupdf.open() as documento:
                for numero in range(3):
                    pagina = documento.new_page()
                    pagina.insert_text((72, 30), "GRAMATICA DE TESTE")
                    pagina.insert_text((72, 120), "Orações subordinadas")
                    texto = ("Os inte-\ngrantes discutem as orações relativas e concessivas. "
                             "Embora chova, vamos estudar. O livro que li contém exemplos. ")
                    pagina.insert_textbox(pymupdf.Rect(72, 170, 520, 740), texto * 5)
                documento.set_toc([[1, "Orações subordinadas", 1]])
                documento.save(pdf)

            def executar_subprocesso(*args, **opcoes):
                try:
                    return subprocess.run(*args, **opcoes, capture_output=True, text=True)
                except subprocess.CalledProcessError as erro:
                    self.fail("Falha no script filho:\n" + erro.stdout + erro.stderr)

            escopo = {
                "Path": Path, "hashlib": hashlib, "json": json,
                "subprocess": SimpleNamespace(run=executar_subprocesso), "textwrap": textwrap,
                "PYTHON_LIVRO": Path(sys.executable), "PASTA_CODIGO": codigo,
                "CAMINHO_PDF": str(pdf), "PASTA_BASE": raiz / "trabalho",
                "TITULO": "Gramática fictícia", "AUTOR": "Autoria de teste",
                "EDICAO": "1", "ANO": "2026", "ALVO_TOKENS": 380,
                "LIMITE_TOKENS": 490, "LOTE_INICIAL": 2,
                "HASHES_FONTES": {n: hashlib.sha256(t.encode()).hexdigest()
                                   for n, t in fontes.items()},
                "PAGINAS_INCLUIR": None, "USAR_CORPO_CUNHA_RECONHECIDO": True,
                "EXTRACAO_CONFERIDA": False, "FRAGMENTOS_CONFERIDOS": False,
            }
            runner = next(no for no in ast.parse(celulas["ambiente"]).body
                          if isinstance(no, ast.FunctionDef) and no.name == "executar")
            exec(compile(ast.Module(body=[runner], type_ignores=[]), "runner", "exec"), escopo)

            def executar_celula(nome, ajustes=None):
                arvore = ast.parse(celulas[nome])
                for no in arvore.body:
                    if (isinstance(no, ast.Assign) and len(no.targets) == 1
                            and isinstance(no.targets[0], ast.Name)
                            and no.targets[0].id in (ajustes or {})):
                        no.value = ast.parse(repr(ajustes[no.targets[0].id]), mode="eval").body
                ast.fix_missing_locations(arvore)
                with redirect_stdout(io.StringIO()):
                    exec(compile(arvore, nome, "exec"), escopo)

            executar_celula("pdf")
            executar_celula("extrair")
            executar_celula("inspecionar")
            executar_celula("candidatos-margem")
            grupos = escopo["grupos"]
            self.assertTrue(grupos, "O cabeçalho repetido deve aparecer para conferência.")
            executar_celula("selecionar-margens", {
                "GRUPOS_MARGEM_CONFIRMADOS": [g["grupo_id"] for g in grupos],
            })
            executar_celula("preparar", {"RECOMPOR_PALAVRAS": ["integrantes"]})
            executar_celula("conferir")
            with self.assertRaises(RuntimeError):
                executar_celula("fragmentar")
            escopo["EXTRACAO_CONFERIDA"] = True
            executar_celula("fragmentar")
            with self.assertRaises(RuntimeError):
                executar_celula("vetorizar")
            pasta = escopo["PASTA_EXECUCAO"]
            paginas = [json.loads(linha) for linha in (pasta / "paginas.jsonl").read_text(
                encoding="utf-8").splitlines()]
            self.assertEqual(len(paginas), 3)
            fragmentos = json.loads((pasta / "fragmentos_preparados.json").read_text(encoding="utf-8"))
            self.assertTrue(fragmentos)
            self.assertTrue(all(f["tokens_embedding"] <= 490 for f in fragmentos))
            preparado = json.loads((pasta / "preparacao.json").read_text(encoding="utf-8"))
            self.assertTrue(preparado["preparacao"]["exclusoes"])
            self.assertTrue(preparado["preparacao"]["normalizacoes"])
            self.assertTrue(any("integrantes" in f["texto"] for f in fragmentos))
            self.assertFalse(any("GRAMATICA DE TESTE" in f["texto"] for f in fragmentos))
            por_pagina = {p["pagina_pdf"]: p for p in paginas}
            for fragmento in fragmentos:
                for origem in fragmento["origem"]:
                    original = por_pagina[origem["pagina_pdf"]]
                    self.assertTrue(original["texto"][origem["offset_inicio"]:origem["offset_fim"]])
                    self.assertIn(origem["bloco_id"], {b["id"] for b in original["blocos"]})
            escopo["executar"](r'''
                import numpy as np
                from transformers import AutoTokenizer
                from gramatica_acervo import vetorizar_fragmentos
                class ModeloControlado:
                    dtype = "float32"
                    max_seq_length = config['limite_tokens']
                    tokenizer = AutoTokenizer()
                    def get_sentence_embedding_dimension(self):
                        return 1024
                    def encode(self, entradas, **opcoes):
                        matriz = np.zeros((len(entradas), 1024), dtype=np.float32)
                        matriz[:, 0] = 1
                        return matriz
                fragmentos = json.loads((pasta / 'fragmentos_preparados.json').read_text())
                vetorizar_fragmentos(fragmentos, ModeloControlado(), pasta,
                    {'pdf_sha256': config['pdf_sha256'], 'limite_tokens': config['limite_tokens']},
                    lote_inicial=2)
            ''')
            executar_celula("exportar")
            pacote_path = json.loads((pasta / "pacote_download.json").read_text())["caminho"]
            with zipfile.ZipFile(pacote_path) as pacote:
                manifesto = json.loads(pacote.read("manifesto.json"))
                for nome in ("config_notebook.json", "extracao.json", "preparacao.json",
                             "auditoria_fragmentos.json", "paginas.jsonl", "vetores.npy"):
                    self.assertIn(nome, pacote.namelist())
                for nome, metadados in manifesto["arquivos"].items():
                    dados = pacote.read(nome)
                    self.assertEqual(hashlib.sha256(dados).hexdigest(), metadados["sha256"])
                    self.assertEqual(len(dados), metadados["bytes"])
                import numpy as np
                matriz = np.load(io.BytesIO(pacote.read("vetores.npy")), allow_pickle=False)
                self.assertEqual(matriz.shape, (len(fragmentos), 1024))

    def test_pip_host_instala_wheel_local_em_venv_sem_pip(self):
        """Valida o caminho --python sem ensurepip, rede ou pacotes do modelo."""
        ajuda = subprocess.run([sys.executable, "-m", "pip", "--help"],
                               capture_output=True, text=True, check=False)
        if ajuda.returncode != 0 or "--python" not in ajuda.stdout:
            self.skipTest("Este teste requer pip do host com suporte a --python (22.3+).")
        with tempfile.TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            ambiente = raiz / "venv_sem_pip"
            venv.EnvBuilder(with_pip=False, system_site_packages=False).create(ambiente)
            python = ambiente / "bin" / "python"
            sem_pip = subprocess.run([str(python), "-I", "-c",
                "import importlib.util; assert importlib.util.find_spec('pip') is None"],
                capture_output=True, text=True, check=False)
            self.assertEqual(sem_pip.returncode, 0, sem_pip.stderr)
            dist_info = "lingua_notebook_offline_probe-0.0.1.dist-info"
            arquivos = {
                "lingua_notebook_offline_probe/__init__.py": "VALOR = 42\n",
                dist_info + "/METADATA": "Metadata-Version: 2.1\nName: lingua-notebook-offline-probe\nVersion: 0.0.1\n",
                dist_info + "/WHEEL": "Wheel-Version: 1.0\nGenerator: teste-local\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            }
            arquivos[dist_info + "/RECORD"] = "".join(
                nome + ",,\n" for nome in [*arquivos, dist_info + "/RECORD"])
            wheel = raiz / "lingua_notebook_offline_probe-0.0.1-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as pacote:
                for nome, conteudo in arquivos.items():
                    pacote.writestr(nome, conteudo)
            resultado = subprocess.run([sys.executable, "-m", "pip", "--isolated", "--python",
                str(python), "install", "--no-index", "--no-deps", "--disable-pip-version-check",
                str(wheel)], capture_output=True, text=True, check=False, timeout=30)
            self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
            verificacao = subprocess.run([str(python), "-I", "-c",
                "import importlib.util, json, sys; import lingua_notebook_offline_probe as p; "
                "print(json.dumps({'valor': p.VALOR, 'prefixo': sys.prefix, "
                "'pip_instalado': importlib.util.find_spec('pip') is not None}))"],
                capture_output=True, text=True, check=False)
            self.assertEqual(verificacao.returncode, 0, verificacao.stderr)
            estado = json.loads(verificacao.stdout)
            self.assertEqual(estado, {"valor": 42, "prefixo": str(ambiente), "pip_instalado": False})

    def test_celula_repara_venv_parcial_sem_invocar_ensurepip(self):
        """Python existente não garante configuração concluída após falha anterior."""
        ambiente_source = next(fonte(c) for c in self.notebook()["cells"]
                               if c["id"] == "ambiente")
        arvore = ast.parse(ambiente_source)
        preparacao = []
        for no in arvore.body:
            if (isinstance(no, ast.Expr) and isinstance(no.value, ast.Call)
                    and isinstance(no.value.func, ast.Attribute)
                    and isinstance(no.value.func.value, ast.Name)
                    and no.value.func.value.id == "subprocess"):
                break
            preparacao.append(no)
        with tempfile.TemporaryDirectory() as temporario:
            ambiente = Path(temporario) / "venv_parcial"
            venv.EnvBuilder(with_pip=False, system_site_packages=False).create(ambiente)
            cfg = ambiente / "pyvenv.cfg"
            self.assertIn("include-system-site-packages = false", cfg.read_text())
            self.assertTrue((ambiente / "bin" / "python").exists())

            class BuilderSemEnsurepip(venv.EnvBuilder):
                def _setup_pip(self, context):
                    raise AssertionError("ensurepip não pode ser usado no Colab.")

            for no in preparacao:
                if (isinstance(no, ast.Assign) and len(no.targets) == 1
                        and isinstance(no.targets[0], ast.Name)
                        and no.targets[0].id == "PASTA_AMBIENTE"):
                    no.value = ast.parse("Path(" + repr(str(ambiente)) + ")", mode="eval").body
            programa = ast.fix_missing_locations(ast.Module(body=preparacao, type_ignores=[]))
            escopo = {"Path": Path, "venv": venv}
            with patch.object(venv, "EnvBuilder", BuilderSemEnsurepip):
                for tentativa in range(2):
                    with self.subTest(tentativa=tentativa):
                        # Reproduz o arquivo deixado incompleto pela falha do ensurepip.
                        cfg.write_text(cfg.read_text().replace(
                            "include-system-site-packages = true", "include-system-site-packages = false"))
                        exec(compile(programa, "montagem-ambiente", "exec"), escopo)
                        self.assertIn("include-system-site-packages = true", cfg.read_text())
                        resultado = subprocess.run([str(escopo["PYTHON_LIVRO"]), "-I", "-c",
                            "import json, sys; print(json.dumps({'prefixo':sys.prefix}))"],
                            capture_output=True, text=True, check=False)
                        self.assertEqual(resultado.returncode, 0, resultado.stderr)
                        self.assertEqual(json.loads(resultado.stdout)["prefixo"], str(ambiente))


if __name__ == "__main__":
    unittest.main()
