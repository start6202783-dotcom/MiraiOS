<div align="center">

<img src="https://raw.githubusercontent.com/start6202783-dotcom/MiraiOS/main/docs/assets/miraios-hero.png" alt="MiraiOS — The Future Runs Local" width="100%">

<br>

[![CI](https://github.com/start6202783-dotcom/MiraiOS/actions/workflows/ci.yml/badge.svg)](https://github.com/start6202783-dotcom/MiraiOS/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/miraios.svg?color=00bfe8&label=PyPI)](https://pypi.org/project/miraios/)
[![Python](https://img.shields.io/badge/Python-3.10%E2%80%933.13-247cff)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-f2c94c.svg)](LICENSE)
[![ONNX](https://img.shields.io/badge/Runtime-ONNX-00d9ff)](https://onnxruntime.ai/)

**Uma camada operacional local-first para implantar, executar e observar IA
em dispositivos Edge.**

[Começar](#comece-em-3-minutos) ·
[Mirai Pilot](#mirai-pilot) ·
[Parear](#conecte-um-dispositivo-na-rede) ·
[Confiança v0.11](#o-marco-da-v011) ·
[Arquitetura](#arquitetura) ·
[CLI](#comandos) ·
[Roadmap](#roadmap)

</div>

## O que é o MiraiOS?

O **MiraiOS** é um projeto open-source do **Projeto Hikari** que transforma um
modelo ONNX em um artefato distribuível e um serviço de inferência operável
em Linux:

```text
assinar → verificar → deploy → medir → aceitar → evidência assinada
```

A CLI permanece no computador do desenvolvedor. O **Mirai Agent** roda no
destino, mantém sua própria identidade, verifica o modelo e executa a
inferência. O primeiro destino pode ser o próprio computador ou um container;
o protocolo é independente de fabricante e não exige uma Raspberry Pi para
começar.

> **Do modelo ao dispositivo físico em um único fluxo verificável, com
> critérios e rollback.**

## O marco da v0.11

![Demonstração do Mirai Package no MiraiOS](https://raw.githubusercontent.com/start6202783-dotcom/MiraiOS/main/docs/assets/miraios-demo.gif)

A v0.11 torna o **Mirai Pilot** verificável e amplia o Agent para arquivos,
papéis, retenção e hardware explícito. O fluxo continua pequeno, mas agora
responde quatro perguntas fundamentais: quem assinou, quem pode agir, o que
foi executado e em qual classe de hardware.

| Etapa | O que acontece |
| --- | --- |
| **Assine** | Ed25519 + DSSE protege pacotes e relatórios sem alterar o `.mirai`. |
| **Autorize** | `viewer`, `operator` e `admin` limitam cada cliente pareado. |
| **Envie** | Imagem, JSON e NPY recebem hash, limites e validação do conteúdo. |
| **Escolha** | CPU, CUDA ou DirectML é declarado; ausência não vira fallback oculto. |
| **Observe** | Histórico, retenção e visão concorrente da frota reduzem trabalho manual. |
| **Valide** | A suíte completa roda também em um runner Linux ARM64 nativo. |

O desenho, os limites e o modelo de ameaças estão documentados em
[Projeto Hikari v0.11](docs/hikari-v0.11.md). O fluxo de aceite que originou o
Pilot permanece em [Projeto Hikari v0.10](docs/hikari-v0.10.md).

## Por que este projeto existe

- **Local-first:** a inferência acontece onde o dado é produzido.
- **Sem hardware obrigatório:** Linux local e Docker validam o protocolo antes
  da compra de uma placa.
- **Canal verificável:** HTTPS, fingerprint fixado e token por cliente evitam
  confiar silenciosamente no primeiro servidor encontrado.
- **Artefato reproduzível:** modelo, contrato e pré-processamento possuem uma
  identidade única verificável por SHA-256.
- **Lifecycle explícito:** receber um arquivo não significa ativá-lo; cada
  transição é intencional e observável.
- **Aceite reproduzível:** resultado, P95 e vazão podem virar critérios
  executáveis, não apenas promessas.
- **Falha recuperável:** um piloto reprovado não substitui silenciosamente o
  deployment que já funcionava.
- **Evidência portátil:** relatórios JSON e Markdown podem acompanhar a entrega
  técnica de um piloto.
- **Formato aberto:** ONNX reduz o acoplamento a um framework de treinamento.
- **Base pequena e auditável:** CLI, cliente, Agent, segurança e runtime são
  módulos Python independentes, sem framework web obrigatório.

## Status atual

| Capacidade | v0.11 |
| --- | --- |
| Validação estrutural com `onnx.checker` | Pronto |
| Pacote `.mirai` determinístico com manifesto estrito | Pronto |
| Verificação de hash e contrato contra o ONNX real | Pronto |
| Pré-processamento declarativo de imagens | Pronto localmente |
| Inferência local numérica, JSON e imagens | Pronto |
| Benchmark com warm-up, mediana, P95 e IPS | Pronto |
| Deploy verificado e lifecycle `ready` / `active` | Pronto |
| Inferência remota, eventos e métricas | Pronto |
| Identidade TLS persistente e fingerprint SHA-256 | Pronto |
| Pareamento de uso único e autenticação por cliente | Pronto |
| Diagnóstico e revogação pela CLI | Pronto |
| Launch completo em um comando | Pronto |
| Piloto declarativo com critérios de aceite | Pronto |
| Benchmark remoto e relatórios JSON/Markdown | Pronto |
| Rollback automático após reprovação | Pronto |
| Assinatura Ed25519/DSSE destacada | Pronto |
| Histórico e retenção segura | Pronto |
| RBAC, rate limit e rotação de identidade | Pronto |
| PNG/JPEG/BMP/WebP/JSON/NPY em inferência remota | Pronto |
| Visão de frota e descoberta mDNS opcional | Pronto; mDNS não autentica |
| Perfis CPU/CUDA/DirectML | Seleção pronta; CPU validado |
| ARM64 Linux / ONNX Runtime CPU | Job nativo no CI |
| Plugins de runtime e RISC-V | Experimental, não validado |

O projeto está em estágio **alpha**. A v0.11 foi desenhada para laboratório,
localhost e redes privadas controladas; ela ainda não é um gateway para
exposição direta à internet.

## Arquitetura

```mermaid
flowchart TD
    CLI["Mirai CLI"]
    PILOT["Mirai Pilot<br/>critérios + pipeline"]
    REG["Registro local protegido"]
    LINK["Hikari Link<br/>TLS + pinning + token"]
    TRUST["Trust<br/>Ed25519 + DSSE + RBAC"]
    API["Mirai Agent API v1"]
    PKG["Mirai Package<br/>manifesto + ONNX + SHA-256"]
    LIFE["Lifecycle persistente"]
    ORT["ONNX Runtime"]
    REPORT["Evidências<br/>JSON + Markdown"]
    INPUT["Secure Inputs<br/>imagem + JSON + NPY"]
    FLEET["Fleet<br/>inventário + retenção"]
    EDGE["Linux x86-64 · ARM64 · Docker"]

    CLI --> PILOT
    PILOT --> REG
    CLI --> PKG
    PKG --> LINK
    TRUST --> PKG
    PILOT --> LINK
    LINK --> API
    INPUT --> API
    API --> LIFE
    FLEET --> API
    LIFE --> ORT
    ORT --> EDGE
    PILOT --> REPORT
```

O Agent usa armazenamento simples e inspecionável:

| Item | Função |
| --- | --- |
| `identity.json` + `agent-*.pem` | Identidade e certificado persistentes. |
| `clients.json` | Clientes pareados; contém somente hashes dos tokens. |
| `packages/` | Pacotes `.mirai` originais identificados pelo hash. |
| `models/` | Modelos ONNX validados e identificados pelo hash. |
| `deployments.json` | Deployments, estados e seleção ativa. |
| `events.jsonl` | Histórico de pareamentos, deploys, ativações e inferências. |

Confiança, anexos e frota estão especificados em
[Projeto Hikari v0.11](docs/hikari-v0.11.md), o piloto em
[Projeto Hikari v0.10](docs/hikari-v0.10.md), o
formato em [Projeto Hikari v0.9](docs/hikari-v0.9.md) e o
modelo de confiança da rede em [Projeto Hikari v0.8](docs/hikari-v0.8.md).

## Comece em 3 minutos

### 1. Instale a versão do repositório

O MiraiOS requer Python 3.10 ou superior:

```bash
git clone https://github.com/start6202783-dotcom/MiraiOS.git
cd MiraiOS
python -m venv .venv
```

Ative o ambiente no Linux ou macOS:

```bash
source .venv/bin/activate
python -m pip install --editable ".[dev]"
```

No Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install --editable ".[dev]"
```

Também é possível instalar a versão publicada:

```bash
python -m pip install --upgrade miraios
```

### 2. Inicie um Agent local

No primeiro terminal:

```bash
mirai agent start
```

O endereço padrão é `http://127.0.0.1:8080`. Esse modo deliberadamente
dispensa pareamento porque só aceita conexões da própria máquina.

### 3. Empacote e faça o primeiro launch

No segundo terminal:

```bash
python scripts/create_dummy_model.py
mirai pack examples/dummy_model.onnx \
  --name dummy \
  --package-version 1.0.0
mirai device add local --url http://127.0.0.1:8080
mirai launch dummy-1.0.0.mirai --device local --input 5.0
```

O modelo de exemplo soma `1` à entrada, portanto o resultado esperado é
`6.0`. O `launch` valida o pacote e o dispositivo, faz deploy, ativa e testa o
modelo. Se o teste final falhar, ele restaura a ativação anterior.

## Mirai Pilot

Quando você precisa provar que uma entrega funciona e atende a uma meta, crie
um projeto de piloto:

```bash
mirai pilot init
```

O arquivo `mirai-pilot.json` já nasce com o exemplo local. Depois de revisar os
valores, execute:

```bash
mirai pilot run
```

O Pilot executa validação, diagnóstico, deploy, ativação, health check,
benchmark remoto e critérios de aceite. No fim, ele grava:

```text
.mirai/reports/
├── dummy-local-DATA-ID.json  # evidência estruturada para automação
├── dummy-local-DATA-ID.json.sig # assinatura DSSE opcional
└── dummy-local-DATA-ID.md    # relatório legível para a entrega
```

Se qualquer etapa ou critério falhar, o relatório registra o motivo e o
deployment anterior é restaurado. Veja todos os campos, limites e garantias em
[Projeto Hikari v0.10](docs/hikari-v0.10.md).

Consulte ou aplique retenção sem scripts próprios:

```bash
mirai pilot history --limit 20
mirai pilot prune --keep 20        # simulação
mirai pilot prune --keep 20 --apply
```

Para assinatura automática, informe `report.signing_key` no projeto. A chave
privada permanece apenas na máquina que executa o Pilot.

## Conecte um dispositivo na rede

No dispositivo de destino, inicie o Agent em um endereço de rede:

```bash
mirai agent start --host 0.0.0.0 --pairing-role operator
```

Fora de localhost, o Agent ativa HTTPS e autenticação automaticamente. Ele
exibe um **código de uso único** e um **fingerprint SHA-256**. Confira esses
dois valores diretamente no terminal do dispositivo e, no computador com a
CLI, execute:

```bash
mirai device pair edge \
  --url https://192.168.1.40:8080 \
  --code CODIGO-EXIBIDO \
  --fingerprint SHA256-EXIBIDO

mirai doctor --device edge
```

Substitua o IP e os valores pelos exibidos pelo Agent. O fingerprint é
verificado antes que o código seja transmitido. Depois do pareamento, os
comandos existentes usam o canal autenticado sem receber segredos na linha de
comando.

Para encerrar o acesso dessa CLI:

```bash
mirai device revoke edge
```

O papel é escolhido por código emitido: `viewer` consulta, `operator` opera
modelos e `admin` também gerencia clientes. Para descoberta link-local
opcional, instale `miraios[discovery]`, inicie com `--discoverable` e use
`mirai device discover`; o candidato encontrado ainda precisa do mesmo
pareamento com fingerprint.

## Comandos

| Comando | Descrição |
| --- | --- |
| `mirai pack modelo.onnx --name app --package-version 1.0.0` | Cria `app-1.0.0.mirai`. |
| `mirai validate ARQUIVO` | Valida integralmente um ONNX ou `.mirai`. |
| `mirai info ARQUIVO` | Exibe contrato, hashes, tipos, shapes e nós. |
| `mirai run ARQUIVO --input 5` | Executa inferência local. |
| `mirai benchmark ARQUIVO` | Mede latência, P95 e vazão local. |
| `mirai launch ARQUIVO --device edge --input 5` | Faz o fluxo rápido até a inferência. |
| `mirai pilot init` | Cria um projeto declarativo de piloto. |
| `mirai pilot run [ARQUIVO]` | Executa critérios, relatório e rollback. |
| `mirai pilot history/prune` | Consulta evidências e aplica retenção confirmada. |
| `mirai key generate release` | Cria um par Ed25519 local. |
| `mirai sign/verify ARQUIVO ...` | Assina ou verifica pacote/relatório com DSSE. |
| `mirai agent start` | Inicia o Agent local. |
| `mirai agent start --host 0.0.0.0` | Inicia um Agent HTTPS pareável. |
| `mirai device add/list/info/remove` | Gerencia destinos locais. |
| `mirai device pair edge ...` | Verifica e pareia um Agent HTTPS. |
| `mirai device revoke edge` | Revoga o token e remove o cadastro. |
| `mirai device clients/role edge ...` | Administra papéis dos clientes pareados. |
| `mirai device discover` | Encontra candidatos mDNS sem confiar neles. |
| `mirai doctor --device edge` | Diagnostica canal, versões e runtime. |
| `mirai deploy ARQUIVO --device edge --provider-profile cpu` | Envia com provider explícito. |
| `mirai cleanup --device edge --keep 5` | Simula/aplica retenção de deployments. |
| `mirai fleet status` | Consulta a frota em paralelo, preservando hosts offline. |
| `mirai runtime list` | Lista ONNX e plugins experimentais descobertos. |
| `mirai status --device edge` | Lista deployments e o modelo ativo. |
| `mirai activate ID --device edge` | Ativa um deployment pronto. |
| `mirai run --device edge --input 5` | Executa no deployment ativo. |
| `mirai logs --device edge` | Consulta eventos recentes. |

Execute `mirai COMANDO --help` para ver todas as opções.

## Entradas e inferência

### Local

Escalares e arrays JSON são convertidos para o dtype e o shape esperados:

```bash
mirai run modelo.onnx --input 5.0
mirai run modelo.onnx --input "[[1, 2, 3]]"
```

Modelos com múltiplas entradas aceitam nome ou ordem:

```bash
mirai run soma.onnx --input x=5 --input y=7
mirai run soma.onnx --input 5 --input 7
```

Imagens NCHW e NHWC são suportadas localmente:

```bash
mirai run visao.onnx --input foto.jpg --layout auto
```

Um pacote pode fixar layout e normalização para impedir que esses parâmetros
se percam entre máquinas:

```bash
mirai pack visao.onnx \
  --name visao \
  --package-version 1.0.0 \
  --image-input images \
  --layout nchw \
  --scale 0.00392156862745098 \
  --mean "[0.485, 0.456, 0.406]" \
  --std "[0.229, 0.224, 0.225]"

mirai run visao-1.0.0.mirai --input foto.jpg
```

O formato v1 usa resize `stretch`, canais `L`/`RGB`/`RGBA` e a transformação
`(pixel × scale - mean) / std`. Letterbox, BGR, tokenização e arquivos
auxiliares ainda não fazem parte do contrato.

### No Agent

A inferência remota aceita escalares, arrays, entradas nomeadas e arquivos:

```bash
mirai run --device edge --input 5
mirai run --device edge --input x=5 --input y=7
mirai run --device edge --input image=foto.png --layout nchw
mirai run --device edge --input tensor.npy
mirai run --device edge --input dados.json
```

PNG, JPEG, BMP, WebP, JSON e NPY são enviados com tamanho e SHA-256, validados
contra extensão, tipo e conteúdo real, materializados com nome gerado e
eliminados depois da inferência. O limite é 8 MB por arquivo e 10 MB por
requisição; NPY recusa pickle e objetos Python.

## Benchmark local

```bash
mirai benchmark modelo-1.0.0.mirai --runs 100 --warmup 5
```

O carregamento do modelo não entra na medição. O relatório inclui tempo total,
latência média, mediana, percentil 95 e inferências por segundo.

No Mirai Pilot, as medições acontecem no Agent e podem ser comparadas a
`max_p95_ms` e `min_ips`. O tempo de rede não entra na latência do modelo.

## Docker: dispositivo sem placa física

O repositório inclui um Agent HTTPS isolado em container:

```bash
docker compose up --build -d
docker compose logs mirai-agent
```

Copie dos logs o código e o fingerprint e faça o pareamento:

```bash
mirai device pair docker \
  --url https://127.0.0.1:8080 \
  --code CODIGO-EXIBIDO \
  --fingerprint SHA256-EXIBIDO
mirai doctor --device docker
```

O volume `mirai-agent-data` preserva identidade, clientes, pacotes, modelos,
lifecycle e eventos. Para encerrar:

```bash
docker compose down
```

## Segurança

O Hikari Link estabelece uma fronteira clara entre desenvolvimento local e
acesso pela rede:

- HTTP sem autenticação é aceito somente em endereços de loopback;
- qualquer escuta fora de loopback ativa HTTPS automaticamente;
- o Agent gera certificado e chave RSA persistentes e exige TLS 1.2 ou mais;
- a CLI fixa o fingerprint SHA-256 antes de enviar qualquer segredo;
- o código de pareamento tem 12 caracteres, expira em dez minutos, fica apenas
  em memória e só pode ser usado uma vez;
- cada cliente recebe um token aleatório próprio e revogável;
- cada cliente recebe `viewer`, `operator` ou `admin`, e toda operação exige o
  papel mínimo correspondente;
- cinco falhas recentes de pareamento por origem acionam bloqueio temporário;
- o Agent persiste somente o SHA-256 do token; o registro da CLI usa permissão
  `0600` em sistemas compatíveis;
- somente `/v1/health` e `/v1/pair` são públicos no modo seguro;
- respostas da API usam `Cache-Control: no-store`;
- pacotes recusam arquivos extras, duplicados, links, compressão e manifesto
  fora do schema;
- o hash interno protege o ONNX e o contrato declarado é comparado ao runtime;
- Ed25519 e DSSE assinam digests tipados de pacotes e relatórios;
- uploads remotos têm allowlist, hash, limites, validação de conteúdo e vida
  somente temporária;
- a identidade pode ser rotacionada offline, invalidando clientes e sem
  arquivar a chave privada antiga;
- um Pilot reprovado restaura a ativação anterior ou desativa o primeiro
  candidato;
- relatórios não recebem token, código de pareamento ou chave privada e, por
  padrão, ocultam as entradas de inferência.

Ainda não há autoridade certificadora, mTLS, rotação automática, malware
scanning, transparência de assinaturas ou cofre de chaves. A assinatura prova
posse da chave, mas a distribuição confiável da chave pública continua sendo
responsabilidade do operador. Use firewall, mantenha a porta em uma rede
privada e não exponha o Agent diretamente à internet.

## Roadmap

### Entregue

- [x] **v0.5.1 — Runtime:** validação real, inferência, imagens, benchmark,
  testes e CI.
- [x] **v0.6 — Deploy:** Agent, registro de dispositivos, upload verificado,
  logs e Docker.
- [x] **v0.7 — Operação:** lifecycle persistente, ativação, inferência remota
  e métricas.
- [x] **v0.8 — Confiança:** identidade TLS, pinning, pareamento, autenticação,
  diagnóstico e revogação.
- [x] **v0.9 — Distribuição:** pacote `.mirai` reproduzível, manifesto estrito,
  contrato verificável, pré-processamento e deploy compatível.
- [x] **v0.10 — Aceite:** launch unificado, projeto declarativo, health check,
  benchmark remoto, critérios, evidências e rollback automático.
- [x] **v0.11 — Confiança e frota:** Ed25519/DSSE, RBAC, rotação, rate limit,
  anexos seguros, retenção, frota, providers explícitos e CI ARM64.

### Próximo

- [ ] Trust store e políticas para exigir assinatura antes do deploy.
- [ ] mTLS e rotação automatizada, com recuperação documentada.
- [ ] Dashboard web local sobre a API de frota.
- [ ] Testes físicos publicados para NVIDIA CUDA e Windows DirectML/WinML.
- [ ] Assinatura e distribuição de plugins de runtime.
- [ ] Compatibilidade RISC-V somente após runner e runtime reais.

O roadmap prioriza um protocolo seguro e útil antes de ampliar a quantidade de
hardwares suportados.

## Desenvolvimento

Instale as dependências de desenvolvimento e execute a suíte:

```bash
python -m pip install --editable ".[dev]"
python -m compileall -q src tests scripts
python -m pytest
```

O CI executa 180 testes em Python 3.10, 3.11, 3.12 e 3.13 no Linux x86-64 e a
suíte completa em Linux ARM64 nativo. Consulte
[CONTRIBUTING.md](CONTRIBUTING.md) antes de enviar mudanças.

Os ativos visuais do README são reproduzíveis:

```bash
python scripts/render_readme_assets.py
```

## Projeto Hikari

**Hikari** é a primeira fase do MiraiOS: construir uma camada pequena,
portátil e verificável entre modelos de IA e hardware local. O nome Mirai
significa “futuro”; Hikari, “luz”.

Documentação dos marcos:

- [v0.6 — Mirai Agent](docs/hikari-v0.6.md)
- [v0.7 — lifecycle e inferência remota](docs/hikari-v0.7.md)
- [v0.8 — Hikari Link](docs/hikari-v0.8.md)
- [v0.9 — Mirai Package](docs/hikari-v0.9.md)
- [v0.10 — Mirai Pilot](docs/hikari-v0.10.md)
- [v0.11 — Trust, Fleet e Secure Inputs](docs/hikari-v0.11.md)
- [Changelog completo](CHANGELOG.md)
- [Política de segurança](SECURITY.md)

## Licença

Distribuído sob a [licença MIT](LICENSE).

<div align="center">

<img src="https://raw.githubusercontent.com/start6202783-dotcom/MiraiOS/main/docs/assets/miraios-logo-primary.png" alt="Logotipo MiraiOS" width="420">

**The Future Runs Local**

</div>
