# Projeto Hikari v0.9 — Mirai Package

A v0.9 introduz o **Mirai Package**, o primeiro formato de distribuição do
projeto. Um arquivo `.mirai` mantém o modelo ONNX, sua identidade, seu contrato
de entradas e saídas e o pré-processamento esperado como uma única unidade
verificável.

O objetivo não é substituir ONNX. O pacote preserva ONNX como runtime e adiciona
os metadados operacionais que um arquivo de modelo isolado não carrega de forma
padronizada para o MiraiOS.

## Fluxo

```text
modelo.onnx
    │
    ├─ validação ONNX + ONNX Runtime
    ├─ contrato real de entradas e saídas
    ├─ perfil de pré-processamento
    └─ SHA-256 do modelo
            ↓
       modelo-1.0.0.mirai
            ↓
   validate → info → run → deploy → activate
```

O fluxo anterior com `.onnx` continua aceito. Isso permite adotar o formato aos
poucos sem invalidar scripts ou deployments existentes.

## Estrutura do formato v1

Um `.mirai` v1 é um arquivo ZIP sem compressão e contém exatamente duas
entradas:

```text
manifest.json
model/model.onnx
```

Diretórios, links simbólicos, entradas duplicadas, arquivos extras, conteúdo
criptografado e outros métodos de compressão são recusados. O manifesto é
limitado a 64 KiB, o modelo a 512 MiB e o pacote completo a aproximadamente
513 MiB.

Pacotes produzidos pela CLI são determinísticos:

- nomes e ordem dos membros são fixos;
- timestamps do ZIP são fixados em `1980-01-01T00:00:00`;
- permissões dos membros são fixas;
- o JSON usa UTF-8, chaves ordenadas e formatação estável;
- não há timestamp de criação no manifesto;
- as entradas usam `ZIP_STORED`, sem variação causada por versões do zlib.

Para o mesmo modelo, manifesto e versão da CLI, duas execuções produzem os
mesmos bytes e o mesmo SHA-256.

## Manifesto

Exemplo reduzido:

```json
{
  "created_by": {
    "tool": "miraios",
    "version": "0.9.0"
  },
  "description": "Modelo que soma um à entrada",
  "format": "mirai.package",
  "format_version": 1,
  "inputs": [
    {
      "name": "input",
      "preprocessing": {
        "kind": "tensor"
      },
      "shape": [
        1
      ],
      "type": "tensor(float)"
    }
  ],
  "model": {
    "path": "model/model.onnx",
    "sha256": "<64 caracteres hexadecimais>",
    "size_bytes": 123,
    "source_name": "dummy_model.onnx"
  },
  "name": "dummy",
  "outputs": [
    {
      "name": "output",
      "shape": [
        1
      ],
      "type": "tensor(float)"
    }
  ],
  "runtime": "onnxruntime",
  "version": "1.0.0"
}
```

Campos desconhecidos são recusados no formato v1. O nome possui no máximo 64
caracteres e usa letras minúsculas, números, `.`, `_` ou `-`. A versão do
modelo segue SemVer e é independente da versão do MiraiOS.

## Pré-processamento de imagens

Toda entrada começa com o perfil `tensor`. Uma entrada pode ser declarada como
imagem ao empacotar:

```bash
mirai pack vision.onnx \
  --name vision \
  --package-version 1.0.0 \
  --image-input images \
  --layout nchw \
  --scale 0.00392156862745098 \
  --mean "[0.485, 0.456, 0.406]" \
  --std "[0.229, 0.224, 0.225]"
```

O perfil de imagem v1 registra:

- layout `nchw` ou `nhwc`;
- redimensionamento `stretch`;
- escala positiva, com padrão `1/255` para ponto flutuante e `1` para `uint8`;
- média e desvio com um valor ou um valor por canal.

A transformação numérica é:

```text
(pixel × scale - mean) / std
```

O runtime escolhe `L`, `RGB` ou `RGBA` de acordo com 1, 3 ou 4 canais. A v0.9
não implementa letterbox, BGR, tokenização, arquivos auxiliares ou execução
remota de caminhos de imagem. Esses recursos exigirão uma nova capacidade
compatível do formato, sem mudar silenciosamente a semântica v1.

## Comandos

Crie um pacote:

```bash
mirai pack examples/dummy_model.onnx \
  --name dummy \
  --package-version 1.0.0 \
  --description "Modelo de demonstração"
```

O destino padrão é `dummy-1.0.0.mirai`. Use `--output` para escolher outro
caminho e `--replace` para permitir substituição explícita.

Valide, inspecione e execute:

```bash
mirai validate dummy-1.0.0.mirai
mirai info dummy-1.0.0.mirai
mirai run dummy-1.0.0.mirai --input 5
mirai benchmark dummy-1.0.0.mirai --runs 100 --warmup 5
```

Faça deploy pelo mesmo canal autenticado da v0.8:

```bash
mirai deploy dummy-1.0.0.mirai --device edge
mirai status --device edge
mirai activate ID --device edge
mirai run --device edge --input 5
```

O Agent preserva o `.mirai` original, extrai o ONNX para execução e registra
separadamente o hash do pacote e o hash do modelo.

## Validação e segurança

Antes de executar ou aceitar um pacote, o MiraiOS verifica:

1. tamanho, extensão e estrutura exata do ZIP;
2. ausência de entradas inseguras ou duplicadas;
3. JSON UTF-8 sem chaves duplicadas;
4. schema estrito e versão suportada;
5. tamanho e SHA-256 do ONNX contra o manifesto;
6. validade estrutural com `onnx.checker`;
7. carregamento no ONNX Runtime CPU;
8. igualdade entre o contrato declarado e o contrato real do modelo.

O SHA-256 detecta corrupção e adulteração acidental, mas não comprova autoria:
o formato v1 ainda não possui assinatura digital. No deploy, o Hikari Link usa
TLS, fingerprint fixado, token por cliente e um hash do pacote inteiro para
proteger o transporte até o Agent.

Pacotes continuam sendo código de computação não confiável para um runtime.
Use apenas modelos de origem conhecida e mantenha o Agent em uma rede privada.

## Critérios de validação

A v0.9 está pronta quando:

1. empacotar o mesmo conteúdo duas vezes produz bytes idênticos;
2. `pack → validate → info → run` funciona localmente;
3. `deploy → activate → run` funciona com `.mirai`;
4. o Agent preserva pacote, modelo, contrato e os dois hashes;
5. adulteração, membros extras e contratos divergentes são recusados;
6. modelos `.onnx` e registros criados nas versões anteriores continuam
   funcionando;
7. a suíte passa em Python 3.10, 3.11, 3.12 e 3.13.
