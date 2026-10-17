# Validação executada — Nexo UV 0.1.0

Host: Linux x86-64, Blender 4.3.2. Código e logo novos; nenhum binário ou módulo
Python do addon original foi executado. O REA foi usado para investigar o pacote
original; sua evidência está em [rea/README.md](rea/README.md).

| Verificação | Resultado observado |
| --- | --- |
| `tests/blender_smoke.py` | **17 testes aprovados**, processo terminou com código 0 e `NEXO_UV_SMOKE_OK tests=17 blender=4.3.2`. |
| `tests/blender_bake_smoke.py` | AO, Normal tangente, Diffuse e Emission executados de fato em Cycles CPU; imagem de saída, restauração dos materiais/configurações e limpeza em cancelamento verificadas. `BAKE_SMOKE_PASSED blender=4.3.2`, código 0. |
| `tests/blender_install_smoke.py` | Instalação do ZIP legacy em diretório isolado, importação a partir desse diretório (fora do checkout), registro, unwrap e desativação aprovados. `NEXO_INSTALL_SMOKE_OK`, código 0. |
| Instalação do ZIP extension | `bpy.ops.extensions.package_install_files` instalou/ativou em repositório local isolado; namespace `bl_ext.user_default.nexo_uv` confirmado e unwrap executado. `NEXO_EXTENSION_INSTALL_OK`, código 0. |
| Validador de extensões do Blender | Manifesto da pasta e do ZIP extension aceitos. |
| Build | Manifesto, licença, imports, código, conteúdo e CRC de ambos os ZIPs validados; duas gerações produziram os mesmos hashes. |
| REA sobre a recriação | `inspect-artifact` no ZIP final, código 0; hash do pacote coincide com `dist/SHA256SUMS`. [Evidence](rea/recreated-package.evidence.json). |

Os 17 testes integrados cobrem registro/desregistro repetido; unwrap de cubo,
esfera e cilindro; backup/restauração; preservação da seleção e Edit Mode;
recusa segura de malha compartilhada; diagnóstico de topologia; tiles e ausência
de overlap entre bounding boxes do atlas; pins; ferramentas de partes, costuras
e ilhas; conectividade UV; normalização da densidade entre objetos com escala
global diferente; rollback completo após erro em múltiplos objetos; flags de
seleção/pins/render/clone; nomes longos de mapas UV; persistência após salvar e
reabrir `.blend`; preview de cores com restauração dos dados originais.

Durante o desenvolvimento, os testes detectaram e levaram à correção de:

- Invalidação de referências RNA pela criação preguiçosa dos atributos de flags
  UV, incluindo um encerramento fatal no primeiro protótipo. Cópias usam as
  coleções modernas e recuperam as camadas pelo nome.
- Normalização de densidade feita separadamente por objeto; agora a área é
  calculada no espaço global e normalizada no atlas compartilhado.
- Perda da escolha do UV de render/clone no backup e no rollback.
- Nomes de backup que ultrapassavam o limite de 63 bytes do Blender.
- Slot de material residual após bake em objeto originalmente sem materiais.

Os logs de execução final desta máquina estão em `/tmp/nexo_final_smoke.log` e
`/tmp/nexo_final_bake.log`. Eles são temporários; os testes versionáveis acima
permitem repetir as verificações sem os arquivos enviados.

## Repetir a instalação isolada

Execute após `python tools/build.py`. Use diretórios descartáveis e execute de
fora do checkout para comprovar que o código instalado veio do ZIP:

```bash
cd /tmp
BLENDER_USER_SCRIPTS=/workspace/.test-output/nexo-install/scripts \
BLENDER_USER_CONFIG=/workspace/.test-output/nexo-install/config \
BLENDER_USER_EXTENSIONS=/workspace/.test-output/nexo-install/extensions \
blender --background --factory-startup --python-exit-code 1 \
  --python /workspace/Blender-Dev/tests/blender_install_smoke.py
```

## Cobertura ainda não comprovada

- Execução em macOS Intel, macOS Apple Silicon e Windows.
- Blender 4.2.0 e outras versões além da 4.3.2 usada aqui.
- Interface visível, Undo interativo, comportamento em projetos muito grandes e
  comparação visual/quantitativa contra OptCuts em um corpus de produção.

A [matriz CI](../.github/workflows/validate.yml) está configurada para duas versões
do Blender e quatro plataformas, mas os jobs não foram executados neste chat.
A tentativa de consultar os checksums no servidor oficial de downloads recebeu
HTTP 403 do proxy deste ambiente. Isso não bloqueou os testes no Blender já
instalado e não foi contornado desativando TLS ou verificações de integridade.

As diferenças funcionais desta recriação estão no [README](../README.md).
Não há alegação de equivalência com o algoritmo OptCuts, nem de teste aprovado
nas plataformas não executadas.
