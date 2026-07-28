# Changelog

Todas as mudanças relevantes do MiraiOS serão documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e
o projeto utiliza [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [0.7.0] - 2026-07-28

### Adicionado

- lifecycle persistente de deployments com estados `ready` e `active`;
- endpoint para listar deployments e identificar o modelo ativo;
- ativação explícita com `mirai activate`;
- visualização do lifecycle com `mirai status`;
- inferência no Agent com `mirai run --device`;
- retorno de resultado, latência de inferência e tempo total no destino;
- eventos de ativação, sucesso e falha de inferência;
- inventário de CPU e memória para futura orquestração;
- identidade visual oficial e demonstração animada no README.

### Segurança

- corpos JSON de inferência são limitados a 1 MB;
- entradas remotas são validadas antes de chegar ao runtime;
- caminhos de imagens remotas são rejeitados para impedir leitura de arquivos
  arbitrários no Agent;
- a API continua restrita a desenvolvimento local até a implementação de
  pareamento e autenticação.

## [0.6.0] - 2026-07-27

### Adicionado

- Mirai Agent HTTP com armazenamento local de modelos e eventos;
- registro persistente de dispositivos em `~/.mirai/devices.json`;
- comandos `mirai device add/list/info/remove`;
- comando `mirai deploy` com validação local e verificação SHA-256;
- comando `mirai logs` para consultar eventos recentes;
- comando `mirai agent start` com escuta segura em localhost;
- ambiente Docker Compose para simular um dispositivo sem hardware físico;
- testes integrados entre CLI, cliente HTTP e Agent.

### Segurança

- o Agent escuta somente em `127.0.0.1` por padrão;
- uploads são limitados a 512 MB;
- nomes de arquivo são sanitizados antes do armazenamento;
- modelos são validados e carregados no runtime antes de ficarem prontos;
- esta versão não deve ser exposta diretamente à internet.

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
[0.6.0]: https://github.com/start6202783-dotcom/MiraiOS/compare/v0.5.1...v0.6.0
[0.7.0]: https://github.com/start6202783-dotcom/MiraiOS/compare/v0.6.0...v0.7.0
