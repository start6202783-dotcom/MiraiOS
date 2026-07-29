# Changelog

Todas as mudanças relevantes do MiraiOS serão documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e
o projeto utiliza [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [0.9.0] - 2026-07-29

### Adicionado

- formato público `.mirai` v1 com `manifest.json` e modelo ONNX;
- comando `mirai pack` com nome, SemVer, descrição e saída configurável;
- contrato de entradas e saídas capturado diretamente do ONNX Runtime;
- perfis declarativos `tensor` e `image` com layout, escala, mean e std;
- suporte a `.mirai` em `validate`, `info`, `run`, `benchmark` e `deploy`;
- armazenamento do pacote original e do modelo extraído no Mirai Agent;
- metadados do pacote, contrato e hashes separados no lifecycle;
- especificação e critérios de validação em `docs/hikari-v0.9.md`.

### Segurança

- schema do manifesto é estrito e rejeita campos ou chaves JSON duplicadas;
- pacotes recusam membros extras ou duplicados, links, criptografia e
  compressão;
- manifesto, modelo e pacote possuem limites explícitos de tamanho;
- SHA-256 interno é verificado antes de carregar o modelo;
- contrato declarado é comparado às entradas e saídas reais do runtime;
- nomes e versões são validados antes de formar caminhos de saída;
- arquivos temporários usam nomes exclusivos e extração para caminho
  controlado.

### Alterado

- o Agent aceita ONNX e `.mirai` pelo mesmo endpoint de deployments;
- o README e a demonstração visual agora apresentam o fluxo do Mirai Package;
- a versão do projeto passou para `0.9.0`.

### Compatibilidade

- comandos, deploys e registros existentes com arquivos `.onnx` continuam
  funcionando;
- o formato `.mirai` usa uma versão própria para permitir evolução explícita;
- a suíte foi ampliada para cobrir reprodução byte a byte, adulteração,
  contrato, CLI e deploy completo no Agent.

## [0.8.0] - 2026-07-28

### Adicionado

- Hikari Link com identidade persistente para cada Mirai Agent;
- certificado X.509 autoassinado e HTTPS automático fora de localhost;
- pinagem do fingerprint SHA-256 antes de qualquer requisição;
- código de pareamento efêmero, de uso único e com expiração de dez minutos;
- token independente e revogável para cada cliente pareado;
- comandos `mirai device pair` e `mirai device revoke`;
- comando `mirai doctor` para conexão, canal, autenticação, versões e runtime;
- persistência de clientes, eventos de pareamento e revogação;
- fluxo HTTPS no container Docker e documentação do modelo de ameaças.

### Segurança

- todos os endpoints operacionais exigem bearer token no modo seguro;
- o Agent armazena somente o SHA-256 dos tokens;
- chave privada, clientes e registro da CLI usam modo `0600` em plataformas
  compatíveis;
- a CLI recusa credenciais em HTTP e Agents remotos sem pareamento;
- respostas JSON recebem `Cache-Control: no-store` e `nosniff`;
- testes cobrem fingerprint incorreto, token ausente, falso e revogado, código
  expirado ou reutilizado e persistência após reinício.

### Alterado

- o registro local de dispositivos passou para o formato v2;
- o Agent ativa o modo seguro automaticamente ao escutar fora de loopback;
- a dependência `cryptography` passou a integrar o pacote.

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
[0.8.0]: https://github.com/start6202783-dotcom/MiraiOS/compare/v0.7.0...v0.8.0
[0.9.0]: https://github.com/start6202783-dotcom/MiraiOS/compare/v0.8.0...v0.9.0
