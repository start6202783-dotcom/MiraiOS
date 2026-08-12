# Operação do MiraiOS v0.14

Este guia reúne as práticas necessárias depois do primeiro teste local. Ele
não substitui o [modelo de ameaças](threat-model.md).

## Pareamento em rede privada

No dispositivo, inicie o Agent:

```bash
mirai agent start --host 0.0.0.0 --pairing-role operator
```

Fora de loopback, HTTPS e autenticação são ativados automaticamente. O Agent
exibe um código de uso único e o fingerprint SHA-256 do certificado. Confira
ambos diretamente no terminal do dispositivo.

Na máquina do operador:

```bash
mirai device pair edge \
  --url https://192.168.1.40:8080 \
  --code CODIGO-EXIBIDO \
  --fingerprint SHA256-EXIBIDO

mirai doctor --device edge
```

O fingerprint é validado antes de o código ser transmitido. Depois do
pareamento, a CLI usa um token próprio e revogável, armazenado no registro
local com permissão restrita quando o sistema oferece suporte.

### Papéis

| Papel | Uso |
| --- | --- |
| `viewer` | Consulta saúde, estado, eventos e métricas permitidas. |
| `operator` | Também implanta, ativa e executa modelos. |
| `admin` | Também gerencia clientes e papéis. |

O papel é definido no código emitido pelo Agent. Para encerrar o acesso da CLI:

```bash
mirai device revoke edge
```

## Admissão assinada

Gere a chave de release na máquina do operador:

```bash
mirai key generate release
mirai sign app-1.0.0.mirai --key ~/.mirai/keys/release.key
```

Inicie o Agent com a chave pública confiável:

```bash
mirai agent start \
  --admission signed \
  --trust-key ~/.mirai/keys/release.pub
```

Envie o pacote e a assinatura destacada:

```bash
mirai deploy app-1.0.0.mirai \
  --device edge \
  --signature app-1.0.0.mirai.sig
```

Distribua a chave pública por um canal já confiável. Não copie a chave privada
para o dispositivo.

## Mirai Pilot

Crie o projeto:

```bash
mirai pilot init
```

Revise `mirai-pilot.json` e escreva critérios antes da execução. Depois:

```bash
mirai pilot run
```

O pipeline valida o artefato, diagnostica o Agent, faz deploy, ativa, verifica
saúde, mede e aplica critérios. Ele produz:

```text
.mirai/reports/
├── projeto-dispositivo-DATA-ID.json
├── projeto-dispositivo-DATA-ID.json.sig  # quando configurado
└── projeto-dispositivo-DATA-ID.md
```

Se uma etapa ou critério falhar, o relatório registra o motivo e a ativação
anterior é restaurada. Consulte e retenha evidências com:

```bash
mirai pilot history --limit 20
mirai pilot prune --keep 20
mirai pilot prune --keep 20 --apply
```

O primeiro `prune` é simulação. A remoção só ocorre com `--apply`.

## Operação de frota

Tags não devem conter segredos:

```bash
mirai device tag edge-01 --set env=prod --set region=br
mirai device tag edge-02 --set env=prod --set region=br
mirai fleet status --selector env=prod,region=br
```

Planeje um rollout progressivo:

```bash
mirai fleet rollout app-2.0.0.mirai \
  --selector env=prod,region=br \
  --canary 10 \
  --batch-size 5 \
  --max-failure-rate 0
```

Sem `--apply`, nenhum Agent é alterado. Depois de revisar plano e critérios,
repita com `--apply`. Os gates são cumulativos; uma reprovação interrompe a
progressão e tenta restaurar as ativações anteriores.

Colete observabilidade e ancore checkpoints:

```bash
mirai fleet observe --selector env=prod,region=br
mirai fleet anchor --selector env=prod,region=br
```

## Entradas e inferência

### Local

```bash
mirai run modelo.onnx --input 5.0
mirai run modelo.onnx --input "[[1, 2, 3]]"
mirai run soma.onnx --input x=5 --input y=7
mirai run visao.onnx --input foto.jpg --layout auto
```

Um pacote pode fixar layout e normalização de imagem:

```bash
mirai pack visao.onnx \
  --name visao \
  --package-version 1.0.0 \
  --image-input images \
  --layout nchw \
  --scale 0.00392156862745098 \
  --mean "[0.485, 0.456, 0.406]" \
  --std "[0.229, 0.224, 0.225]"
```

### Remota

```bash
mirai run --device edge --input 5
mirai run --device edge --input x=5 --input y=7
mirai run --device edge --input image=foto.png --layout nchw
mirai run --device edge --input tensor.npy
mirai run --device edge --input dados.json
```

PNG, JPEG, BMP, WebP, JSON e NPY são enviados com tamanho e SHA-256,
validados contra conteúdo real, materializados com nome gerado e removidos após
a inferência. NPY recusa pickle e objetos Python.

## Evidência e compatibilidade

O fluxo recomendado é:

```text
Passport → Pilot/Replay → Arena/Failure Lab → Evidence Bundle
```

- **Passport** liga origem declarada e uso pretendido ao artefato.
- **Replay** pode conter entradas sensíveis e exige confirmação explícita.
- **Arena** mede um workload reproduzível e evita identificadores pessoais de
  hardware.
- **Failure Lab** atua somente em cópias temporárias.
- **Evidence Bundle** agrega os resultados por digest sem duplicar o modelo por
  padrão.

Comandos e schemas estão em [hikari-v0.14.md](hikari-v0.14.md).

## Observabilidade e privacidade

O Agent mantém contadores, latência e resumos numéricos limitados. Entradas e
saídas brutas não são persistidas no arquivo de métricas. O sinal de drift
compara janelas de médias: ele não mede acurácia nem prova mudança conceitual.

Replay é uma exceção deliberada porque sua finalidade é reproduzir uma entrada.
Proteja o bundle como protegeria a fixture original.

## Checklist antes de hardware físico

- [ ] A mesma versão da CLI e do Agent foi confirmada com `doctor`.
- [ ] O artefato e, quando aplicável, a assinatura foram validados.
- [ ] O provider selecionado existe no dispositivo.
- [ ] A entrada usada no benchmark representa o workload declarado.
- [ ] P95, IPS, memória e tolerância possuem critérios escritos.
- [ ] A versão anterior continua disponível para rollback.
- [ ] O Agent está em localhost ou rede privada com firewall.
- [ ] Tokens, chaves e dados sensíveis estão fora de logs e do repositório.
- [ ] O relatório final identifica versão, artefato, hardware e limitações.

## Limites operacionais

- Não exponha o Agent diretamente à internet.
- O servidor atual não é um gateway web de produção.
- A quarentena estrutural não é isolamento de processo.
- DSSE prova bytes e posse da chave, não identidade humana ou física.
- A ancoragem padrão continua local ao control plane.
- Arena e Matrix não certificam automaticamente um hardware.
- CUDA e DirectML exigem runtime e teste físicos próprios.

Para a lista completa, consulte [threat-model.md](threat-model.md).
