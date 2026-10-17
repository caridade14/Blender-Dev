# Investigação com REA

O [REA](https://github.com/morluto/rea) foi clonado em `/workspace/rea` no commit
`f85a932d71279415dd95cc30083137d1161f83b8`. Foi usado de fato pelo CLI publicado
`rea-agents@6.5.0`, instalado separadamente em `/workspace/tools/rea-cli`, com
Node.js `24.19.0` e npm `11.9.0`. O clone permaneceu sem alterações.

A investigação leu o ZIP enviado e seus executáveis como dados. Nenhum executável
do addon original foi iniciado, nenhum arquivo Python original foi importado e
nenhuma instrução contida nos arquivos anexados foi seguida. Os arquivos do
original não são distribuídos neste projeto.

## Evidências executadas

Os arquivos `*.evidence.json` contêm o registro completo retornado pelo REA, com
identidade, proveniência, limitações e cobertura. Os identificadores completos
estão em [index.json](index.json).

| Arquivo | Operação efetivamente executada | Observação |
| --- | --- | --- |
| [original-package.evidence.json](original-package.evidence.json) | `inspect-artifact` | ZIP externo com três pacotes de plataforma e dois arquivos auxiliares; cinco entradas verificadas. |
| [macos-package.evidence.json](macos-package.evidence.json) | `inspect-artifact` | 41 ocorrências no pacote macOS, incluindo engine `mach-o-universal` e avisos de licença. |
| [linux-engine.evidence.json](linux-engine.evidence.json) | `inspect-with-objdump … file_headers` | ELF x86-64; digest antes/depois igual; saída completa. |
| [windows-engine.evidence.json](windows-engine.evidence.json) | `inspect-with-objdump … file_headers` | PE x86-64; digest antes/depois igual; saída completa. |
| [recreated-package.evidence.json](recreated-package.evidence.json) | `inspect-artifact` | ZIP final Nexo UV 0.1.0 inventariado pelo REA; SHA-256 corresponde ao build reproduzível. |

SHA-256 do ZIP externo:
`5a688207ddd4d57e43457597536ce760b597b2d97677508d2fa8d173abfeb0e7`.
O inventário externo não expande recursivamente os ZIPs internos; o pacote macOS
foi inspecionado em uma segunda operação explícita.

## Achados e decisões

- **Observado pelo REA:** as distribuições originais usam um engine nativo
  separado. Linux e Windows foram identificados como x86-64; o pacote macOS
  contém um Mach-O universal.
- **Observado em análise complementar:** os cabeçalhos do Mach-O contêm slices
  Intel x86-64 e Apple Silicon arm64. Logo, o original já incluía Apple Silicon.
  Os mínimos declarados pelo engine são macOS 10.15 e 11.0, respectivamente;
  esses números não definem o mínimo do Blender. O binário Linux contém strings
  `GLIBC_2.38` e `GLIBCXX_3.4.32`.
- **Inferência:** esses vínculos nativos podem restringir distribuições Linux
  antigas. Usar `bpy`, `bmesh` e operadores nativos do Blender evita distribuir
  engines por arquitetura e delega a compatibilidade nativa ao Blender instalado.
- **Observado na leitura do código/licenças, fora do REA:** o original declara
  Blender 4.2+, depende de módulos fornecidos pelo Blender e inclui fontes e
  licenças do engine GPL v3. Não foi encontrada uma licença geral clara para
  todo o Python e as imagens. A recriação usa código e identidade visual novos.
- **Limite de reconstrução:** as evidências estáticas não medem qualidade UV,
  não observam a interface em execução e não estabelecem equivalência com o
  algoritmo OptCuts. O engine original não foi copiado nem executado.

Detalhes dos cabeçalhos e strings estão em
[platform-static-supplement.json](platform-static-supplement.json), identificado
explicitamente como análise Python complementar, e não como Evidence do REA.
[extraction-provenance.json](extraction-provenance.json) liga os dois executáveis
extraídos às entradas e hashes dos ZIPs de origem. Eles foram gravados fora do
repositório com permissões de dados, sem execução.

## Disponibilidade e verificações pendentes

[doctor.json](doctor.json) registra que o CLI tem um Node compatível, mas a sessão
não possui registro MCP nem Hopper/Ghidra/IDA configurados. Isso não impediu as
operações estáticas acima. Nenhuma configuração do cliente foi alterada.

`inspect-macho` direto devolveu `capability_unavailable`: seu provider usa
utilitários nativos do macOS, indisponíveis neste host Linux. O resultado real
está em [macos-engine.failure.json](macos-engine.failure.json). A análise de
cabeçalhos complementar preserva essa distinção; não houve decompilação REA do
engine, teste de assinatura, execução nem testes do addon original.

Para validar a recriação, os testes devem rodar no Blender nas quatro plataformas
solicitadas: Windows x64, Linux x64, macOS Intel e macOS Apple Silicon. Registro,
UVs, undo, restauração de contexto e persistência precisam de testes com o Blender
real; portabilidade do código, por si só, não comprova aprovação nessas máquinas.

## Repetir e continuar a investigação

Não é necessário registrar MCP para estes comandos. A instalação preserva as
verificações normais de TLS e integridade do npm; não atualiza Node nem engines.

```bash
NPM_CONFIG_CACHE=/workspace/.cache/npm npm install \
  --prefix /workspace/tools/rea-cli --save-exact --omit=optional \
  --no-audit --no-fund rea-agents@6.5.0

/workspace/tools/rea-cli/node_modules/.bin/rea --version
/workspace/tools/rea-cli/node_modules/.bin/rea inspect-artifact \
  /caminho/absoluto/para/pacote.zip --json > pacote.evidence.json

REA_OBJDUMP_COMMAND=/usr/bin/objdump \
  /workspace/tools/rea-cli/node_modules/.bin/rea inspect-with-objdump \
  /caminho/absoluto/para/executavel file_headers --json > engine.evidence.json
```

Em novas comparações de versões, reutilize hashes e evidências existentes antes de
repetir análises. Use REA para inventário de pacotes e perguntas que dependem de
artefatos distribuídos; use ferramentas normais de código para o Python disponível.
O guia aplicado foi
[reverse-engineer-anything/SKILL.md](https://github.com/morluto/rea/blob/f85a932d71279415dd95cc30083137d1161f83b8/.agents/skills/reverse-engineer-anything/SKILL.md).
