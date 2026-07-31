# Projeto Hikari v0.11 — Trust, Fleet e Secure Inputs

## Objetivo

A v0.11 transforma evidências, identidades, arquivos de entrada e hardware em
contratos explícitos. O marco não tenta esconder o estágio alpha: CPU e ARM64
são caminhos validados pela automação; CUDA, DirectML, plugins e RISC-V só são
descritos no nível que o código realmente sustenta.

```text
artefato assinado → cliente autorizado → provider escolhido → execução
          ↓                    ↓                    ↓
    autoria verificável   ação permitida      hardware observável
```

## 1. Assinaturas Ed25519 e DSSE

Crie uma chave local:

```bash
mirai key generate release
```

A chave privada fica em `~/.mirai/keys/release.key` e a pública em
`~/.mirai/keys/release.pub`. Em sistemas compatíveis, a privada recebe modo
`0600`. Ela não deve ser enviada ao Agent, anexada ao release ou armazenada no
Git.

Assine e verifique um pacote:

```bash
mirai sign app-1.0.0.mirai \
  --key ~/.mirai/keys/release.key

mirai verify app-1.0.0.mirai \
  --signature app-1.0.0.mirai.sig \
  --key ~/.mirai/keys/release.pub
```

A assinatura é destacada. O `.mirai` não muda, preservando a reprodução byte
a byte do formato v1. O envelope DSSE contém um payload tipado com nome,
tamanho e SHA-256 do artefato. A assinatura usa Ed25519 e o PAE do DSSE, que
inclui o tipo e o comprimento antes dos bytes assinados.

Para assinar automaticamente o JSON de um piloto:

```json
{
  "report": {
    "directory": ".mirai/reports",
    "include_inputs": false,
    "signing_key": "/caminho/privado/release.key"
  }
}
```

O resultado inclui `RELATORIO.json.sig`. Uma assinatura prova que a chave
indicada aprovou aqueles bytes; ela não prova, sozinha, que a chave pertence a
uma empresa. A chave pública precisa chegar ao verificador por um canal
confiável.

## 2. Histórico e retenção

Consulte as evidências:

```bash
mirai pilot history --directory .mirai/reports --limit 20
mirai pilot history --status failed
mirai pilot show 20260731T180000Z-a1b2c3d4
```

Simule a retenção antes de apagar:

```bash
mirai pilot prune --keep 20
mirai pilot prune --keep 20 --apply
```

Somente o JSON reconhecido e seus irmãos `.md` e `.json.sig` entram no plano.
Arquivos com outro nome ou extensão não são removidos.

Deployments seguem a mesma regra de confirmação:

```bash
mirai cleanup --device edge --keep 5
mirai cleanup --device edge --keep 5 --apply
```

O ativo nunca entra na lista. No Agent, os arquivos são primeiro movidos para
tombstones; se o registro não puder ser persistido, os arquivos voltam ao
nome original.

## 3. Papéis e identidade

O código mostrado no início do Agent concede um único papel definido pelo
operador:

```bash
mirai agent start --host 0.0.0.0 --pairing-role operator
```

| Papel | Pode fazer |
| --- | --- |
| `viewer` | Consultar informações, deployments, eventos e saúde. |
| `operator` | Tudo de viewer, além de deploy, ativação, inferência e retenção. |
| `admin` | Tudo de operator, além de listar clientes e alterar papéis. |

Administre os clientes:

```bash
mirai device clients edge
mirai device role edge ID_DO_CLIENTE viewer
```

O último admin não pode ser rebaixado. Tentativas de pareamento são limitadas
por origem: cinco falhas em uma janela curta bloqueiam novas tentativas por
cinco minutos. O código continua de uso único e expira em dez minutos.

Rotacione a identidade somente com o Agent parado:

```bash
mirai agent rotate-identity \
  --data-dir .mirai-agent \
  --confirm AGENT_ID_ATUAL
```

O comando gera certificado, chave e Agent ID novos, remove a chave privada
antiga e invalida todos os clientes. A trilha em `identity-history/` registra
somente ID, fingerprint e horários antigos. Cada cliente deve conferir o novo
fingerprint e parear novamente.

## 4. Entradas remotas seguras

Imagens e tensores agora usam a mesma interface do modo local:

```bash
mirai run --device edge --input foto.png --layout nchw
mirai run --device edge --input tensor.npy
mirai run --device edge --input dados.json
```

Extensões aceitas: PNG, JPEG, BMP, WebP, JSON e NPY.

As defesas são combinadas:

- até 16 arquivos, 8 MB por arquivo e 10 MB no total;
- base64 estrito, tamanho exato e SHA-256 antes do uso;
- nome informado nunca vira caminho de armazenamento;
- extensão, media type e conteúdo real devem concordar;
- imagens são abertas e verificadas pelo Pillow;
- JSON precisa ser UTF-8 válido e não aceita `NaN`/`Infinity`;
- NPY usa `allow_pickle=False`, recusa objetos e limita rank;
- os arquivos existem somente em um diretório temporário da inferência.

O upload não oferece antivírus ou sandbox de decodificadores. Mantenha
dependências atualizadas e opere o Agent em rede privada e conta sem
privilégios.

## 5. Frota e descoberta

Veja todos os dispositivos cadastrados, inclusive os offline:

```bash
mirai fleet status
```

A consulta é concorrente e exibe perfil de hardware, deployment ativo e
quantidade de deployments. Para mDNS, instale o extra e habilite o anúncio:

```bash
python -m pip install "miraios[discovery]"
mirai agent start --host 0.0.0.0 --discoverable
mirai device discover
```

mDNS é link-local e não é uma identidade confiável. A descoberta nunca salva
um dispositivo e nunca substitui código + fingerprint no `device pair`.

## 6. Providers e hardware

Selecione o comportamento no deploy ou launch:

```bash
mirai deploy app.mirai --device edge --provider-profile cpu
mirai launch app.mirai --device gpu --provider-profile cuda --input 5
```

| Perfil | Ordem solicitada | Situação da v0.11 |
| --- | --- | --- |
| `auto` | CUDA → DirectML → CPU, apenas quando disponíveis | Pronto |
| `cpu` | CPU | Validado em CI x86-64 e ARM64 |
| `cuda` | CUDA → CPU | Configurado; exige ORT GPU e hardware NVIDIA |
| `directml` | DirectML → CPU | Configurado; exige runtime Windows compatível |

Um perfil explícito ausente falha. Não há fallback silencioso de `cuda` para
CPU, porque isso faria um benchmark parecer válido no hardware errado.

DirectML continua utilizável pelo ONNX Runtime, mas está em manutenção; novas
evoluções do ecossistema Windows estão concentradas no Windows ML. A v0.11
mantém suporte compatível e não o apresenta como tecnologia de ponta nova.

## 7. ARM64, plugins e RISC-V

O workflow `CI` possui um job em runner GitHub `ubuntu-24.04-arm`, confirma
`aarch64/arm64` e executa a mesma suíte completa. Isso valida o pacote Python e
o caminho ONNX Runtime CPU em ARM64 Linux; não certifica toda placa, câmera ou
acelerador ARM existente.

O comando abaixo lista o backend interno e entry points externos:

```bash
mirai runtime list
```

Plugins usam o grupo experimental `mirai.runtime_backends`. A v0.11 apenas
descobre e identifica plugins; o formato `.mirai` e a execução estável
continuam ONNX. Hosts `riscv*` são inventariados como
`riscv-experimental`, sem alegação de compatibilidade validada.

## Modelo de ameaças resumido

| Ameaça | Defesa | Limite restante |
| --- | --- | --- |
| Pacote ou relatório adulterado | Ed25519 + DSSE + SHA-256 | Distribuição da chave pública é externa. |
| Token roubado com privilégio excessivo | RBAC e revogação | Tokens ainda são bearer tokens. |
| Força bruta no pareamento | Código forte, TTL, uso único e rate limit | Limite é local ao processo do Agent. |
| Arquivo com nome/caminho hostil | Nome gerado e diretório temporário | Decodificadores ainda processam conteúdo. |
| Provider ausente | Falha explícita | Desempenho precisa ser medido no hardware real. |
| Anúncio mDNS falso | Candidato sempre não confiável | Usuário precisa conferir fingerprint. |

## Referências de engenharia

- [Ed25519 no cryptography](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/)
- [DSSE — Dead Simple Signing Envelope](https://github.com/secure-systems-lab/dsse)
- [OWASP File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)
- [ONNX Runtime Execution Providers](https://onnxruntime.ai/docs/execution-providers/)
- [RFC 6762 — Multicast DNS](https://www.rfc-editor.org/rfc/rfc6762)

## Compatibilidade

O Mirai Package permanece em `format_version: 1`. Registros de dispositivos
v1/v2 e clientes v1 são lidos e migrados. Como a CLI e o Agent validam a série
minor, atualize os dois para v0.11 antes de usar RBAC, anexos ou providers.
