from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from compatibilidade_embeddings import (
    ErroCompatibilidadeEmbeddings, avaliar_compatibilidade_backends,
)


class AdaptadorControlado:
    """Exclusivo dos testes de contrato, sem alegação de inferência neural."""

    def __init__(self, *, descricao=None, vetor=None, modificar_tokens=None):
        self.descricao = descricao or {
            'backend': 'controle-a', 'versao_backend': 'teste',
            'modelo': {'identificacao': 'fixture-testes', 'revisao': 'fixture-controlada'},
            'tokenizador': {'identificacao': 'fixture-caracteres', 'revisao': 'fixture-controlada'},
            'dimensao': 2, 'limite_tokens': 256, 'pooling': 'mean', 'dispositivo': 'cpu',
            'precisao_inferencia': 'float32', 'prefixo_automatico': False,
            'truncamento_automatico': False, 'bibliotecas': {'controle': 'testes'},
        }
        self.vetor = [3.0, 4.0] if vetor is None else vetor
        self.modificar_tokens = modificar_tokens
        self.textos_tokenizados = []
        self.lotes_gerados = []

    def descrever(self):
        return deepcopy(self.descricao)

    def tokenizar(self, texto):
        self.textos_tokenizados.append(texto)
        tamanho = len(texto) + 2
        dados = {'input_ids': [1] + [ord(c) + 3 for c in texto] + [2],
                 'attention_mask': [1] * tamanho,
                 'special_tokens_mask': [1] + [0] * len(texto) + [1],
                 'offsets': [[0, 0]] + [[i, i+1] for i in range(len(texto))] + [[0, 0]]}
        if self.modificar_tokens:
            self.modificar_tokens(dados)
        return dados

    def gerar(self, entradas):
        self.lotes_gerados.append(list(entradas))
        return [deepcopy(self.vetor) for _ in entradas]


class TestCompatibilidadeEmbeddings(unittest.TestCase):
    def test_aproximacao_amostral_nao_concede_compatibilidade_global(self):
        a, b = AdaptadorControlado(), AdaptadorControlado(vetor=[3.01, 4.0])
        b.descricao['backend'] = 'controle-b'
        b.descricao['dispositivo'] = 'cuda:1'
        b.descricao['precisao_inferencia'] = 'float16'
        textos = ['query: Não saí.', 'passage:  e\u0301 😀\r\n\t']
        relatorio = avaliar_compatibilidade_backends(a, b, textos)
        self.assertEqual(relatorio['estado'], 'compatibilidade_aproximada_amostral')
        self.assertFalse(relatorio['autoriza_operacoes_similaridade'])
        self.assertEqual(relatorio['compatibilidade_global'], 'nao_comprovada')
        self.assertEqual(relatorio['descricoes']['a'], a.descricao)
        self.assertEqual(relatorio['descricoes']['b'], b.descricao)
        self.assertEqual(a.lotes_gerados, [textos])
        self.assertEqual(b.lotes_gerados, [textos])
        for texto, entrada in zip(textos, relatorio['entradas']):
            self.assertEqual(entrada['texto'], texto)
            self.assertEqual(entrada['sha256_utf8'], hashlib.sha256(texto.encode('utf-8')).hexdigest())
            self.assertGreater(entrada['numerica']['cosseno'], 0.99)
            self.assertFalse(entrada['numerica']['bytes_float32_iguais'])
        self.assertEqual(json.loads(json.dumps(relatorio, ensure_ascii=False)), relatorio)

    def test_renormaliza_as_duas_pontas_incluindo_valores_carregados_fp16(self):
        # Simula somente o arredondamento numérico de armazenamento FP16.
        a_valores = [struct.unpack('<e', struct.pack('<e', x))[0] for x in (0.7, 0.2)]
        a = AdaptadorControlado(vetor=a_valores)
        b = AdaptadorControlado(vetor=[v * 10 for v in a_valores])
        r = avaliar_compatibilidade_backends(a, b, ['query: teste'])
        numerica = r['entradas'][0]['numerica']
        self.assertAlmostEqual(numerica['cosseno'], 1.0)
        self.assertAlmostEqual(numerica['norma_a'], math.hypot(*a_valores))
        self.assertAlmostEqual(numerica['norma_b'], math.hypot(*b.vetor))
        self.assertGreater(numerica['erro_maximo_absoluto'], 1.0)
        self.assertAlmostEqual(numerica['erro_maximo_absoluto_normalizado'], 0.0)
        self.assertFalse(numerica['bytes_float32_iguais'])
        self.assertTrue(numerica['bytes_float32_normalizados_iguais'])

    def test_metricas_e_bytes_float32_iguais_na_amostra(self):
        r = avaliar_compatibilidade_backends(AdaptadorControlado(), AdaptadorControlado(), ['abc'])
        metrica = r['entradas'][0]['numerica']
        self.assertTrue(metrica['bytes_float32_iguais'])
        self.assertEqual(metrica['sha256_float32_a'], hashlib.sha256(struct.pack('<ff', 3., 4.)).hexdigest())
        self.assertEqual(metrica['erro_maximo_absoluto'], 0.0)
        self.assertEqual(metrica['erro_rms'], 0.0)
        self.assertEqual(r['compatibilidade_global'], 'nao_comprovada')

    def test_limiar_estrito_e_divergencia_numerica(self):
        r = avaliar_compatibilidade_backends(AdaptadorControlado(vetor=[1., 0.]),
            AdaptadorControlado(vetor=[1., 0.]), ['abc'], limiar_cosseno=1.0)
        self.assertEqual(r['estado'], 'divergencia_na_amostra')
        self.assertFalse(r['entradas'][0]['numerica']['acima_limiar'])
        r = avaliar_compatibilidade_backends(AdaptadorControlado(vetor=[1., 0.]),
            AdaptadorControlado(vetor=[0., 1.]), ['abc'])
        self.assertEqual(r['entradas'][0]['numerica']['cosseno'], 0.0)
        self.assertEqual(r['entradas'][0]['numerica']['erro_maximo_absoluto'], 1.0)
        self.assertEqual(r['entradas'][0]['numerica']['erro_rms'], 1.0)

    def test_todas_as_entradas_precisam_passar_sem_media_esconder_divergencia(self):
        b = AdaptadorControlado()
        b.gerar = lambda textos: [[3.,4.],[4.,-3.]]
        r = avaliar_compatibilidade_backends(AdaptadorControlado(), b, ['a','b'])
        self.assertEqual(r['estado'], 'divergencia_na_amostra')
        self.assertEqual(r['resumo']['cosseno_minimo'], 0.0)

    def test_modelo_tokenizador_revisoes_pooling_nao_inferidos_pelo_cosseno(self):
        for campo in ('modelo.identificacao', 'modelo.revisao', 'tokenizador.identificacao',
                      'tokenizador.revisao', 'pooling'):
            a, b = AdaptadorControlado(), AdaptadorControlado()
            if '.' in campo:
                chave, sub = campo.split('.')
                b.descricao[chave][sub] = 'outra-identidade'
            else:
                b.descricao[campo] = 'cls'
            with self.subTest(campo=campo):
                r = avaliar_compatibilidade_backends(a, b, ['abc'])
                self.assertEqual(r['estado'], 'divergencia_na_amostra')
                self.assertTrue(r['resumo']['todos_cossenos_acima_limiar'])
                self.assertFalse(next(v for v in r['verificacoes_metadata'] if v['campo']==campo)['igual'])

    def test_pooling_configuracoes_comparadas_quando_ambas_declaradas(self):
        a, b = AdaptadorControlado(), AdaptadorControlado()
        a.descricao['pooling_configuracao']={'media':True}
        b.descricao['pooling_configuracao']={'media':1}
        r = avaliar_compatibilidade_backends(a,b,['abc'])
        self.assertEqual(r['estado'],'divergencia_na_amostra')
        self.assertFalse(r['verificacoes_metadata'][-1]['igual'])

    def test_compara_listas_completas_ids_masks_especiais_e_offsets(self):
        mutacoes = {
            'input_ids': lambda dados: dados['input_ids'].__setitem__(2, 999),
            'attention_mask': lambda dados: dados['attention_mask'].__setitem__(2, 0),
            'special_tokens_mask': lambda dados: dados['special_tokens_mask'].__setitem__(0, 0),
            'offsets': lambda dados: dados['offsets'].__setitem__(2, [0, 2]),
        }
        for campo, mutacao in mutacoes.items():
            with self.subTest(campo=campo):
                r=avaliar_compatibilidade_backends(AdaptadorControlado(),
                    AdaptadorControlado(modificar_tokens=mutacao), ['query: conteúdo'])
                self.assertEqual(r['estado'],'divergencia_na_amostra')
                self.assertFalse(r['entradas'][0]['tokenizacao']['campos_iguais'][campo])
                self.assertFalse(r['entradas'][0]['tokenizacao']['identica_campos_verificados'])

    def test_token_type_opcional_declarado_sem_preenchimento_ficticio(self):
        def com_tipo(dados): dados['token_type_ids']=[0]*len(dados['input_ids'])
        r=avaliar_compatibilidade_backends(AdaptadorControlado(),
            AdaptadorControlado(modificar_tokens=com_tipo), ['abc'])
        tipos=r['entradas'][0]['tokenizacao']['token_type_ids']
        self.assertEqual(tipos,{'presente_a':False,'presente_b':True,'igual':None})
        self.assertNotIn('token_type_ids',r['entradas'][0]['tokenizacao']['a'])
        def diferente(dados): dados['token_type_ids']=[1]*len(dados['input_ids'])
        r=avaliar_compatibilidade_backends(AdaptadorControlado(modificar_tokens=com_tipo),
            AdaptadorControlado(modificar_tokens=diferente), ['abc'])
        self.assertEqual(r['estado'],'divergencia_na_amostra')

    def test_dimensoes_diferentes_sem_comparacao_artificial(self):
        a, b=AdaptadorControlado(),AdaptadorControlado(vetor=[3.,4.,5.])
        b.descricao['dimensao']=3
        r=avaliar_compatibilidade_backends(a,b,['abc'])
        self.assertEqual(r['estado'],'divergencia_na_amostra')
        self.assertIsNone(r['entradas'][0]['numerica']['cosseno'])
        self.assertEqual(r['entradas'][0]['numerica']['estado'],'dimensoes_diferentes')

    def test_preserva_unicode_pontuacao_tab_crlf_e_nao_muda_adaptadores(self):
        textos=['query:  NÃO... e\u0301\t😀\r\n\r\nFim!','passage: Texto']
        original=deepcopy(textos); a,b=AdaptadorControlado(),AdaptadorControlado()
        ad=deepcopy(a.descricao); av=deepcopy(a.vetor)
        r=avaliar_compatibilidade_backends(a,b,textos)
        self.assertEqual(textos,original)
        self.assertEqual(a.textos_tokenizados,textos)
        self.assertEqual(b.textos_tokenizados,textos)
        self.assertEqual(a.descricao,ad); self.assertEqual(a.vetor,av)
        r['descricoes']['a']['modelo']['revisao']='adulterada'
        r['entradas'][0]['tokenizacao']['a']['input_ids'][1]=999
        self.assertEqual(a.descricao,ad)

    def test_limites_conferidos_antes_da_primeira_inferencia(self):
        a,b=AdaptadorControlado(),AdaptadorControlado()
        b.descricao['limite_tokens']=5
        with self.assertRaisesRegex(ErroCompatibilidadeEmbeddings,'nenhum truncamento'):
            avaliar_compatibilidade_backends(a,b,['a','excede'])
        self.assertEqual(a.lotes_gerados,[]); self.assertEqual(b.lotes_gerados,[])
        r=avaliar_compatibilidade_backends(a,b,['abc'])
        self.assertEqual(r['estado'],'compatibilidade_aproximada_amostral')

    def test_configuracoes_comparadas_sem_impedir_evidencia_amostral_por_dispositivo(self):
        a,b=AdaptadorControlado(),AdaptadorControlado(vetor=[30.,40.])
        a.descricao.update(limite_tokens_selecionado=256,normalizacao_interna='nenhuma')
        b.descricao.update(limite_tokens=512,limite_tokens_selecionado=100,
                          precisao_inferencia='float16',dispositivo='cuda:1',normalizacao_interna='l2')
        r=avaliar_compatibilidade_backends(a,b,['query: conteúdo'])
        self.assertEqual(r['estado'],'compatibilidade_aproximada_amostral')
        verificacoes={v['campo']:v for v in r['verificacoes_configuracao']}
        for campo in ('limite_tokens','limite_tokens_selecionado','precisao_inferencia','dispositivo','normalizacao_interna'):
            self.assertTrue(verificacoes[campo]['presente_a'])
            self.assertTrue(verificacoes[campo]['presente_b'])
            self.assertFalse(verificacoes[campo]['igual'])
        self.assertFalse(r['autoriza_operacoes_similaridade'])

    def test_opcoes_ausentes_sao_explicitas_sem_assumir_normalizacao(self):
        a,b=AdaptadorControlado(),AdaptadorControlado()
        b.descricao['normalizacao_interna']='nenhuma'
        r=avaliar_compatibilidade_backends(a,b,['abc'])
        verificacoes={v['campo']:v for v in r['verificacoes_configuracao']}
        self.assertEqual(verificacoes['normalizacao_interna'],{
            'campo':'normalizacao_interna','caminho':['normalizacao_interna'],
            'presente_a':False,'presente_b':True,'a':None,'b':'nenhuma','igual':None})
        self.assertFalse(verificacoes['limite_tokens_selecionado']['presente_a'])
        self.assertFalse(verificacoes['limite_tokens_selecionado']['presente_b'])
        self.assertIsNone(verificacoes['limite_tokens_selecionado']['igual'])
        self.assertNotIn('normalizacao_interna',r['descricoes']['a'])

    def test_limite_selecionado_respeitado_antes_de_gerar(self):
        a,b=AdaptadorControlado(),AdaptadorControlado()
        b.descricao['limite_tokens_selecionado']=5
        with self.assertRaisesRegex(ErroCompatibilidadeEmbeddings,'nenhum truncamento'):
            avaliar_compatibilidade_backends(a,b,['abcdef'])
        self.assertEqual(a.lotes_gerados,[]); self.assertEqual(b.lotes_gerados,[])
        for invalido in (0,True,300,None):
            b.descricao['limite_tokens_selecionado']=invalido
            with self.subTest(limite=invalido),self.assertRaises(ErroCompatibilidadeEmbeddings):
                avaliar_compatibilidade_backends(a,b,['abc'])

    def test_artefatos_nomes_revisoes_e_hashes_comparados_com_presenca_explicita(self):
        a,b=AdaptadorControlado(),AdaptadorControlado()
        hash_a=hashlib.sha256(b'peso-controlado-testes').hexdigest()
        hash_b=hashlib.sha256(b'outro-peso-controlado-testes').hexdigest()
        a.descricao['artefatos']={'modelo':{'commit':'fixture','arquivos':{'modelo.safetensors':hash_a}},
                                 'tokenizador':{'arquivos':{'tokenizer.json':hash_a}}}
        b.descricao['artefatos']={'modelo':{'commit':'fixture','arquivos':{'modelo.safetensors':hash_b}},
                                 'onnx':{'arquivos_sha256':{'modelo.onnx':hash_b}}}
        r=avaliar_compatibilidade_backends(a,b,['abc'])
        por_caminho={tuple(v['caminho']):v for v in r['verificacoes_configuracao']}
        peso=por_caminho[('artefatos','modelo','arquivos','modelo.safetensors')]
        self.assertEqual(peso['a'],hash_a);self.assertEqual(peso['b'],hash_b)
        self.assertFalse(peso['igual'])
        token=por_caminho[('artefatos','tokenizador','arquivos','tokenizer.json')]
        self.assertTrue(token['presente_a']);self.assertFalse(token['presente_b'])
        self.assertIsNone(token['igual'])
        self.assertEqual(r['descricoes']['a']['artefatos'],a.descricao['artefatos'])
        self.assertFalse(r['autoriza_operacoes_similaridade'])

    def test_tokenizacoes_invalidas_erros_explicitos(self):
        mutacoes=[lambda d:d.pop('offsets'), lambda d:d['offsets'].__setitem__(0,[0,1]),
                  lambda d:d['offsets'].__setitem__(1,[0,99]),
                  lambda d:d['attention_mask'].__setitem__(1,True),
                  lambda d:d['input_ids'].__setitem__(1,-1),
                  lambda d:d.__setitem__('attention_mask',[]),
                  lambda d:d.__setitem__('attention_mask',[0]*len(d['input_ids'])),
                  lambda d:d.__setitem__('token_type_ids',[True]*len(d['input_ids']))]
        for mutacao in mutacoes:
            a,b=AdaptadorControlado(),AdaptadorControlado(modificar_tokens=mutacao)
            with self.subTest(mutacao=mutacao), self.assertRaises(ErroCompatibilidadeEmbeddings):
                avaliar_compatibilidade_backends(a,b,['abc'])
            self.assertFalse(a.lotes_gerados)

    def test_descricao_saida_argumentos_invalidos_e_drift(self):
        for entradas in ([],None,{},'abc',['abc',None],['\ud800']):
            with self.subTest(entradas=repr(entradas)),self.assertRaises(ErroCompatibilidadeEmbeddings):
                avaliar_compatibilidade_backends(AdaptadorControlado(),AdaptadorControlado(),entradas)
        for limiar in (True,float('nan'),float('inf'),-1.01,1.01,1<<10000,'0.99'):
            with self.subTest(limiar=type(limiar).__name__),self.assertRaises(ErroCompatibilidadeEmbeddings):
                avaliar_compatibilidade_backends(AdaptadorControlado(),AdaptadorControlado(),['abc'],limiar_cosseno=limiar)
        for campo, valor in (('dimensao',True),('limite_tokens',0),('pooling',''),
                             ('modelo',{}),('bibliotecas',{'teste':float('nan')}),
                             ('prefixo_automatico',True),('truncamento_automatico',True)):
            b=AdaptadorControlado(); b.descricao[campo]=valor
            with self.subTest(campo=campo),self.assertRaises(ErroCompatibilidadeEmbeddings):
                avaliar_compatibilidade_backends(AdaptadorControlado(),b,['abc'])
        for vetor in ([0.,0.],[float('nan'),1.],[float('inf'),1.],[True,1.],[1.],[1<<10000,1.]):
            with self.subTest(vetor=type(vetor[0]).__name__),self.assertRaises(ErroCompatibilidadeEmbeddings):
                avaliar_compatibilidade_backends(AdaptadorControlado(vetor=vetor),AdaptadorControlado(),['abc'])
        b=AdaptadorControlado()
        def deriva(textos):
            b.descricao['modelo']['revisao']='mudou-durante-geracao'
            return [[3.,4.]]*len(textos)
        b.gerar=deriva
        with self.assertRaisesRegex(ErroCompatibilidadeEmbeddings,'mudou'):
            avaliar_compatibilidade_backends(AdaptadorControlado(),b,['abc'])

    def test_importacao_leitura_nao_carrega_modelos_pesados(self):
        programa='import sys; import compatibilidade_embeddings; assert not any(n in sys.modules for n in ("torch","transformers","sentence_transformers","fastembed","vetorizacao"))'
        p=subprocess.run([sys.executable,'-c',programa],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_cli_json_saida_exclusiva_e_prefixo_nao_acrescentado(self):
        import compatibilidade_embeddings
        entradas=['query: Não 😀\r\n']; a,b=AdaptadorControlado(),AdaptadorControlado()
        with tempfile.TemporaryDirectory() as temp:
            origem=Path(temp)/'entradas.json'; saida=Path(temp)/'resultado.json'
            origem.write_text(json.dumps(entradas,ensure_ascii=False),encoding='utf-8')
            argumentos=['compatibilidade_embeddings.py','--entradas',str(origem),'--saida',str(saida)]
            with patch.object(sys,'argv',argumentos),patch('modelo_embeddings.criar_adaptador',side_effect=[a,b]):
                compatibilidade_embeddings._main()
            resultado=json.loads(saida.read_text(encoding='utf-8'))
            self.assertEqual(resultado['entradas'][0]['texto'],entradas[0])
            anterior=saida.read_bytes()
            with patch.object(sys,'argv',argumentos),patch('modelo_embeddings.criar_adaptador',side_effect=[a,b]),patch('sys.stderr'),self.assertRaises(SystemExit) as erro:
                compatibilidade_embeddings._main()
            self.assertEqual(erro.exception.code,1)
            self.assertEqual(saida.read_bytes(),anterior)


if __name__=='__main__':
    unittest.main()
