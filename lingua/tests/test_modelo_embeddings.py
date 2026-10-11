from contextlib import nullcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from modelo_embeddings import (AdaptadorFastEmbed, AdaptadorSentenceTransformers,
    ErroModeloEmbeddings, _limite_efetivo, _resolver_snapshot, _selecionar_dispositivo,
    _tokenizar_hf, _validar_tokenizacao, _validar_vetores, criar_adaptador,
    _carregar_fastembed, _carregar_st)
from perfis_vetorizacao import carregar_perfil


class TokenizerControlado:
    is_fast = True
    model_max_length = 900
    pad_token_id = 0
    pad_token = '[PAD]'
    padding_side = 'right'

    def __init__(self):
        self.chamadas = []
        self.lotes = []

    def __call__(self, texto, **opcoes):
        self.chamadas.append((texto, opcoes))
        return {'input_ids': [1] + [ord(c)+3 for c in texto] + [2],
                'attention_mask': [1] * (len(texto)+2),
                'special_tokens_mask': [1] + [0] * len(texto) + [1],
                'offset_mapping': [(0,0)] + [(i,i+1) for i in range(len(texto))] + [(0,0)]}

    def pad(self, lote, **opcoes):
        self.lotes.append(lote)
        return {'input_ids': TensorControlado([item['input_ids'] for item in lote]),
                'attention_mask': TensorControlado([item['attention_mask'] for item in lote])}


class TensorControlado:
    def __init__(self, valores):
        self.valores = valores

    def to(self, dispositivo):
        return self

    def detach(self):
        return self

    def float(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.valores


class TestModeloEmbeddings(unittest.TestCase):
    def test_importacao_indisponivel_informa_dependencia_original_sem_carregar_modelo(self):
        for nome, modulos in (('torch', {'torch': None}),
                              ('sentence_transformers', {'torch': SimpleNamespace(), 'sentence_transformers': None})):
            with self.subTest(nome=nome), patch.dict(sys.modules, modulos), \
                    patch('modelo_embeddings._resolver_snapshot') as resolver:
                with self.assertRaises(ErroModeloEmbeddings) as erro:
                    _carregar_st(carregar_perfil('e5_analista'))
                self.assertIn(nome, str(erro.exception))
                self.assertIsInstance(erro.exception.__cause__, ImportError)
                resolver.assert_not_called()

    def test_import_e_fabrica_nao_carregam_dependencias_pesadas(self):
        programa = ('import sys; from modelo_embeddings import criar_adaptador; '
                    'from perfis_vetorizacao import carregar_perfil; '
                    'criar_adaptador(carregar_perfil()); '
                    'assert not any(n in sys.modules for n in ("torch","sentence_transformers","fastembed"))')
        resultado = subprocess.run([sys.executable, '-c', programa], capture_output=True, text=True)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)

    def test_tokenizacao_preserva_string_unicode_e_nao_trunca(self):
        texto = 'query:  Não\t e\u0301 😀\r\n\n' * 30
        tokenizer = TokenizerControlado()
        resultado = _tokenizar_hf(tokenizer, texto)
        self.assertEqual(tokenizer.chamadas[0][0], texto)
        opcoes = tokenizer.chamadas[0][1]
        self.assertFalse(opcoes['truncation'])
        self.assertFalse(opcoes['padding'])
        self.assertEqual(len(resultado['input_ids']), len(texto)+2)
        self.assertEqual(resultado['offsets'][-2], [len(texto)-1, len(texto)])

    def test_tokenizador_sem_offsets_erro(self):
        tokenizer = TokenizerControlado()
        tokenizer.is_fast = False
        with self.assertRaises(ErroModeloEmbeddings):
            _tokenizar_hf(tokenizer, 'texto')
        with self.assertRaises(ErroModeloEmbeddings):
            _tokenizar_hf(TokenizerControlado(), None)

    def test_mascara_limites_e_tipagem_estritos(self):
        dados = _tokenizar_hf(TokenizerControlado(), '😀')
        for campo, valor in (('offsets', [[0,0],[0,2],[0,0]]), ('attention_mask', [1,True,1]),
                             ('input_ids', [1,-1,2]), ('token_type_ids', [0]),
                             ('special_tokens_mask', [1,0])):
            invalido = dict(dados)
            invalido[campo] = valor
            with self.subTest(campo=campo), self.assertRaises(ErroModeloEmbeddings):
                _validar_tokenizacao(invalido, '😀')

    def test_capacidade_varia_com_modelo_sem_constantes_e5(self):
        config = SimpleNamespace(max_position_embeddings=16384)
        tokenizer = SimpleNamespace(model_max_length=8192)
        self.assertEqual(_limite_efetivo(tokenizer, config, None), 8192)
        self.assertEqual(_limite_efetivo(tokenizer, config, 1000), 1000)
        with self.assertRaises(ErroModeloEmbeddings):
            _limite_efetivo(tokenizer, config, 9000)
        with self.assertRaises(ErroModeloEmbeddings):
            _limite_efetivo(SimpleNamespace(model_max_length=10**30), SimpleNamespace(), None)
        self.assertEqual(_limite_efetivo(SimpleNamespace(model_max_length=10**30), config, None), 16384)

    def test_gpu_indice_mps_sem_fallback_silencioso(self):
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True, device_count=lambda: 2),
                                backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)))
        self.assertEqual(_selecionar_dispositivo(torch, 'cpu'), 'cpu')
        self.assertEqual(_selecionar_dispositivo(torch, 'cuda'), 'cuda:0')
        self.assertEqual(_selecionar_dispositivo(torch, 'cuda:1'), 'cuda:1')
        for dispositivo in ('cuda:2', 'mps'):
            with self.assertRaises(ErroModeloEmbeddings):
                _selecionar_dispositivo(torch, dispositivo)
        torch.cuda.is_available = lambda: False
        with self.assertRaises(ErroModeloEmbeddings):
            _selecionar_dispositivo(torch, 'cuda')

    def test_revisao_local_verificada_por_hash_real(self):
        with tempfile.TemporaryDirectory() as temp:
            pasta = Path(temp)
            (pasta / 'config.json').write_bytes(b'{"modelo":"teste"}')
            hashes = {'config.json': hashlib.sha256((pasta/'config.json').read_bytes()).hexdigest()}
            revisao = hashlib.sha256(json.dumps(hashes, sort_keys=True, ensure_ascii=False,
                separators=(',',':')).encode()).hexdigest()
            caminho, manifesto = _resolver_snapshot(temp, revisao, temp, True)
            self.assertEqual(caminho, str(pasta.resolve()))
            self.assertEqual(manifesto['arquivos'], hashes)
            (pasta/'config.json').write_bytes(b'adulterado')
            with self.assertRaises(ErroModeloEmbeddings):
                _resolver_snapshot(temp, revisao, temp, True)

    def test_hf_download_explicitamente_controlado_e_revisao_conferida(self):
        modulo = SimpleNamespace(snapshot_download=lambda **kwargs: '/cache/'+kwargs['revision'])
        with patch.dict(sys.modules, {'huggingface_hub': modulo}):
            with patch.object(modulo, 'snapshot_download', wraps=modulo.snapshot_download) as download:
                caminho, manifesto = _resolver_snapshot('org/modelo', 'a'*40, '/cache', True)
                self.assertEqual(manifesto['commit'], 'a'*40)
                self.assertTrue(download.call_args.kwargs['local_files_only'])
                self.assertIn('*.bin', download.call_args.kwargs['ignore_patterns'])
            modulo.snapshot_download = lambda **kwargs: '/cache/revisao-incorreta'
            with self.assertRaises(ErroModeloEmbeddings):
                _resolver_snapshot('org/modelo', 'a'*40, '/cache', True)

    def test_st_forward_explicito_lotes_sem_prompt_sem_normalizacao(self):
        perfil = carregar_perfil(opcoes={'lote': 2})
        adapter = AdaptadorSentenceTransformers(perfil)
        tokenizer = TokenizerControlado()
        recurso = {'lock': threading.RLock(), 'tokenizer': tokenizer, 'dispositivo': 'cpu',
                   'torch': SimpleNamespace(inference_mode=nullcontext)}
        adapter._recurso = recurso
        adapter._descricao = {'dimensao': 3, 'limite_tokens': 12}
        chamados = []
        def modulo(features):
            chamados.append(features['input_ids'].valores)
            return {'sentence_embedding': TensorControlado([[3.0,4.0,1.0] for _ in features['input_ids'].valores])}
        adapter._modules = [modulo]
        entradas = ['query: A', 'passage: B', '😀']
        resultado = adapter.gerar(entradas)
        self.assertEqual([t for t, opts in tokenizer.chamadas], entradas)
        self.assertEqual(resultado, [[3.0,4.0,1.0]]*3)
        self.assertEqual([len(lote) for lote in chamados], [2,1])
        self.assertNotAlmostEqual(sum(v*v for v in resultado[0]), 1.0)
        self.assertEqual(adapter.gerar([]), [])
        with self.assertRaises(ErroModeloEmbeddings):
            adapter.gerar(['x'*20])
        self.assertEqual(len(chamados), 2)

    def test_dimensao_finitude_vetor_zero(self):
        for vetores in ([[1.0]], [[float('nan'), 1.0]], [[float('inf'),1.0]], [[0.0,0.0]], [[True,1.0]], []):
            with self.subTest(vetores=vetores), self.assertRaises(ErroModeloEmbeddings):
                _validar_vetores(vetores, 1, 2)
        self.assertEqual(_validar_vetores([[1.0,2.0,3.0]],1,3), [[1.0,2.0,3.0]])

    def test_fastembed_exige_artefato_revisado_e_pooling_explicito(self):
        adapter = criar_adaptador(carregar_perfil(opcoes={'backend': 'fastembed'}))
        self.assertIsInstance(adapter, AdaptadorFastEmbed)
        with self.assertRaisesRegex(ErroModeloEmbeddings, 'arquivo_onnx'):
            adapter.descrever()
        with self.assertRaises(ErroModeloEmbeddings):
            criar_adaptador(carregar_perfil(opcoes={'backend': 'fastembed', 'pooling': 'modelo'}))

    def test_fastembed_pooling_mascara_sem_l2_e_padding_limpo(self):
        import numpy as np
        for pooling, esperado in (('mean', [[2.0,3.0]]), ('cls', [[1.0,2.0]]),
                                   ('max', [[3.0,4.0]]), ('last_token', [[3.0,4.0]])):
            with self.subTest(pooling=pooling):
                adapter = AdaptadorFastEmbed(carregar_perfil(opcoes={'backend':'fastembed', 'pooling':pooling}))
                tokenizer = SimpleNamespace(no_padding=lambda: None, enable_padding=lambda **kwargs: None)
                modelo = SimpleNamespace(onnx_embed=lambda lote: SimpleNamespace(
                    model_output=np.array([[[1.,2.],[3.,4.],[500.,600.]]]),
                    attention_mask=np.array([[1,1,0]])))
                adapter._recurso = {'np':np, 'lock':threading.RLock(), 'tokenizer':tokenizer,
                    'hf_tokenizer':TokenizerControlado(), 'modelo':modelo}
                adapter._descricao = {'dimensao':2, 'limite_tokens':4}
                adapter.tokenizar = lambda texto: {'input_ids':[1,2]}
                self.assertEqual(adapter.gerar(['entrada']), esperado)
                self.assertEqual(adapter.gerar([]), [])
                adapter.tokenizar = lambda texto: {'input_ids':list(range(5))}
                with self.assertRaises(ErroModeloEmbeddings):
                    adapter.gerar(['longa'])

    @unittest.skipUnless(all(importlib.util.find_spec(n) for n in ('onnx', 'fastembed', 'transformers')),
                         'Teste de integração ONNX controlado exige dependências opcionais.')
    def test_fastembed_grafo_controlado_external_data_symlinks_e_precision(self):
        """Tabela controlada testa o backend bruto; não é avaliação de modelo neural."""
        import numpy as np
        import onnx
        from onnx import TensorProto, helper, numpy_helper
        from tokenizers import Tokenizer, models, pre_tokenizers, processors
        from transformers import AutoConfig, AutoTokenizer
        vocab = {'[UNK]':0, '[CLS]':1, '[SEP]':2, '[PAD]':3, 'Eu':4, 'não':5, '😀':6}
        raw = Tokenizer(models.WordLevel(vocab=vocab, unk_token='[UNK]'))
        raw.pre_tokenizer = pre_tokenizers.Whitespace()
        raw.post_processor = processors.TemplateProcessing(single='[CLS] $A [SEP]',
            special_tokens=[('[CLS]',1),('[SEP]',2)])
        class HFControlado(TokenizerControlado):
            backend_tokenizer = raw
            pad_token_id = 3
            def __call__(self, texto, **kwargs):
                item = raw.encode(texto)
                return {'input_ids':item.ids, 'attention_mask':item.attention_mask,
                        'special_tokens_mask':item.special_tokens_mask, 'offset_mapping':item.offsets}
        hf = HFControlado()
        with tempfile.TemporaryDirectory() as temp:
            pasta = Path(temp)
            blobs = pasta/'blobs'
            snapshots = pasta/'snapshots'
            blobs.mkdir()
            snapshots.mkdir()
            tabela = np.arange(14, dtype=np.float32).reshape(7,2) + 1
            def gravar(dtype):
                tensor = numpy_helper.from_array(tabela.astype(dtype), name='pesos')
                graph = helper.make_graph([helper.make_node('Gather', ['pesos','input_ids'], ['tokens'], axis=0)],
                    'fixture-controlada', [helper.make_tensor_value_info('input_ids',TensorProto.INT64,['lote','tokens'])],
                    [helper.make_tensor_value_info('tokens',TensorProto.FLOAT if dtype==np.float32 else TensorProto.FLOAT16,
                        ['lote','tokens',2])], [tensor])
                model = helper.make_model(graph, opset_imports=[helper.make_opsetid('',13)])
                model.ir_version = 10
                onnx.save_model(model, str(blobs/'graph'), save_as_external_data=True,
                    all_tensors_to_one_file=True, location='model.onnx_data', size_threshold=0)
            gravar(np.float32)
            (snapshots/'model.onnx').symlink_to(blobs/'graph')
            (snapshots/'model.onnx_data').symlink_to(blobs/'model.onnx_data')
            graph_sha = hashlib.sha256((blobs/'graph').read_bytes()).hexdigest()
            perfil = carregar_perfil(opcoes={'backend':'fastembed', 'arquivo_onnx':str(snapshots/'model.onnx'),
                'modelo_onnx_sha256':graph_sha, 'limite_tokens':6})
            with patch('modelo_embeddings._resolver_snapshot', return_value=(temp, {'commit':'controlado'})), \
                 patch.object(AutoConfig, 'from_pretrained', return_value=SimpleNamespace(max_position_embeddings=20)), \
                 patch.object(AutoTokenizer, 'from_pretrained', return_value=hf):
                recurso = _carregar_fastembed(perfil)
                self.assertEqual(recurso['arquivo_onnx'], str(snapshots/'model.onnx'))
                self.assertEqual(set(recurso['arquivos_onnx_sha256']), {'model.onnx','model.onnx_data'})
                adapter = AdaptadorFastEmbed(perfil)
                adapter._recurso = recurso
                descricao = adapter.descrever()
                self.assertEqual(descricao['limite_tokens'],20)
                self.assertEqual(descricao['limite_tokens_selecionado'],6)
                self.assertEqual(adapter.tokenizar('Eu não 😀')['input_ids'], [1,4,5,6,2])
                esperado = tabela[[1,4,2]].mean(axis=0)
                np.testing.assert_allclose(adapter.gerar(['Eu'])[0], esperado)
                adulterado = dict(perfil, arquivos_onnx_sha256={'model.onnx':graph_sha})
                with self.assertRaisesRegex(ErroModeloEmbeddings, 'Hashes'):
                    _carregar_fastembed(adulterado)
                (blobs/'model.onnx_data').unlink()
                gravar(np.float16)
                perfil['modelo_onnx_sha256'] = hashlib.sha256((blobs/'graph').read_bytes()).hexdigest()
                with self.assertRaisesRegex(ErroModeloEmbeddings, 'precisão'):
                    _carregar_fastembed(perfil)


if __name__ == '__main__':
    unittest.main()
