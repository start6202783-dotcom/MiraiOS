# Primeiros passos com o MiraiOS

Este guia leva um modelo ONNX de exemplo até uma inferência local pelo Mirai
Agent. Nenhuma placa física é necessária: localhost e Docker permitem validar
o protocolo antes de escolher o hardware.

## Pré-requisitos

- Python 3.10, 3.11, 3.12 ou 3.13;
- Linux, macOS ou Windows para a CLI;
- Docker somente para o cenário em container.

O caminho mais validado é Linux com ONNX Runtime CPU. O projeto está em alpha
e deve ser usado em laboratório, localhost ou rede privada controlada.

## Instalação da v0.14 a partir do repositório

Crie um ambiente isolado:

```bash
git clone https://github.com/start6202783-dotcom/MiraiOS.git
cd MiraiOS
python -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
mirai --version
```

No Windows PowerShell:

```powershell
git clone https://github.com/start6202783-dotcom/MiraiOS.git
cd MiraiOS
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --editable .
mirai --version
```

O badge PyPI do README mostra a série publicada, que pode ficar
temporariamente atrás da branch `main` durante o fechamento de uma release.

## Ambiente de desenvolvimento

Para contribuir, instale também os extras de desenvolvimento no clone já
criado:

```bash
python -m pip install --editable ".[dev]"
```

## Primeiro fluxo local

### 1. Gere o modelo didático

```bash
python scripts/create_dummy_model.py
mirai validate examples/dummy_model.onnx
mirai info examples/dummy_model.onnx
```

O modelo possui uma entrada numérica e soma `1` ao valor recebido.

### 2. Crie um pacote verificável

```bash
mirai pack examples/dummy_model.onnx \
  --name dummy \
  --package-version 1.0.0

mirai validate dummy-1.0.0.mirai
```

O `.mirai` liga manifesto, contrato e modelo por SHA-256. A validação confere
estrutura, conteúdo e compatibilidade do contrato declarado.

### 3. Inicie o Agent

No primeiro terminal:

```bash
mirai agent start
```

O endereço padrão é `http://127.0.0.1:8080`. Esse modo aceita somente a
própria máquina e deliberadamente dispensa pareamento.

### 4. Registre e execute

No segundo terminal:

```bash
mirai device add local --url http://127.0.0.1:8080
mirai doctor --device local
mirai launch dummy-1.0.0.mirai --device local --input 5.0
```

O resultado esperado é `6.0`. O `launch` faz validação, deploy, ativação e
teste. Se a última etapa falhar, a ativação anterior é restaurada.

Consulte o estado e os eventos:

```bash
mirai status --device local
mirai logs --device local
```

## Agent em Docker

O container simula um dispositivo separado e preserva o estado do Agent em um
volume:

```bash
docker compose up --build -d
docker compose logs mirai-agent
```

Os logs exibem um código de pareamento de uso único e o fingerprint do
certificado. Confira os valores e execute:

```bash
mirai device pair docker \
  --url https://127.0.0.1:8080 \
  --code CODIGO-EXIBIDO \
  --fingerprint SHA256-EXIBIDO

mirai doctor --device docker
mirai launch dummy-1.0.0.mirai --device docker --input 5.0
```

Encerre o container sem apagar o volume:

```bash
docker compose down
```

## Próximo passo: evidência v0.14

Depois de confirmar a inferência, crie a identidade do artefato e uma medição
padronizada:

```bash
mirai passport create dummy-1.0.0.mirai \
  --output model-passport.json \
  --intended-use "aprendizado local"

mirai arena run dummy-1.0.0.mirai \
  --output benchmark.json \
  --category experimental \
  --runs 20 \
  --warmup 3

mirai arena verify benchmark.json --artifact dummy-1.0.0.mirai
mirai compatibility add benchmark.json
mirai compatibility show
```

Use `experimental` até conhecer a categoria correta do hardware. O resultado
não inclui hostname, rede ou número de série.

## Problemas comuns

### `mirai: command not found`

Confirme que o ambiente virtual está ativo e execute:

```bash
python -m pip show miraios
python -m pip install --editable .
```

### O Agent não aceita HTTP pela rede

Esse comportamento é intencional. Fora de loopback, o Agent ativa HTTPS e
autenticação. Siga o pareamento descrito em [operations.md](operations.md).

### O pacote foi recusado no modo `signed`

Verifique se o envelope corresponde exatamente aos bytes e ao nome do pacote,
e se a chave pública está na trust store do Agent. Veja
[Projeto Hikari v0.12](hikari-v0.12.md).

### Um benchmark não representa o hardware

Repita a medição no dispositivo final, com o mesmo artefato, provider, entrada,
warm-up e quantidade de execuções. Resultado de CI ou do computador de
desenvolvimento não substitui teste físico.

## Continue

- [Referência da CLI](cli-reference.md)
- [Operação e segurança](operations.md)
- [Evidência, reprodução e compatibilidade](hikari-v0.14.md)
- [Modelo de ameaças](threat-model.md)
- [Como contribuir](../CONTRIBUTING.md)
