# Changelog

Todas as mudanças relevantes do MiraiOS serão documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e
o projeto utiliza [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [0.5.1] - 2026-07-27

### Adicionado

- suíte automatizada com pytest;
- CI para Python 3.10, 3.11, 3.12 e 3.13;
- entradas repetidas no formato `--input nome=valor`;
- arrays JSON e múltiplas entradas;
- detecção de imagens NCHW e NHWC;
- warm-up, mediana e P95 no benchmark;
- metadados e URLs do projeto no pacote Python.

### Corrigido

- `validate` agora carrega o modelo e executa `onnx.checker.check_model`;
- valores respeitam dtype e shape declarados pelo modelo;
- imagens `uint8` não são convertidas indevidamente para `float32`;
- dimensões NHWC usam altura e largura corretas;
- saídas extensas são resumidas no terminal;
- dependência Pillow foi adicionada ao `requirements.txt`.

### Alterado

- CLI, inspeção, entradas, runtime e benchmark foram separados em módulos;
- benchmark passou a medir inferências individualmente após aquecimento;
- documentação passou a registrar capacidades e limitações reais.

### Removido

- implementação duplicada e desatualizada em `cli/main.py`.

## [0.5.0] - 2026-07-27

### Adicionado

- entrada básica de imagens;
- publicação do pacote `miraios` no PyPI.

[0.5.1]: https://github.com/start6202783-dotcom/MiraiOS/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/start6202783-dotcom/MiraiOS/releases/tag/v0.5.0
