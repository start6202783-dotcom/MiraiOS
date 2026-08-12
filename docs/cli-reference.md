# Referência da CLI do MiraiOS v0.14

Esta página organiza os comandos por objetivo. Execute
`mirai COMANDO --help` para consultar todos os argumentos e valores padrão da
versão instalada.

## Artefatos e execução local

| Comando | Função |
| --- | --- |
| `mirai init` | Confirma a inicialização do ambiente Hikari. |
| `mirai pack MODELO --name NOME --package-version SEMVER` | Cria um pacote `.mirai` reproduzível. |
| `mirai validate ARQUIVO` | Valida um ONNX ou `.mirai`. |
| `mirai info ARQUIVO` | Mostra contrato, shapes, tipos, hashes e grafo. |
| `mirai run ARQUIVO --input VALOR` | Executa inferência local. |
| `mirai benchmark ARQUIVO` | Mede média, mediana, P95, P99, estabilidade e IPS. |
| `mirai hardware` | Exibe identidade de hardware sem identificadores pessoais. |
| `mirai runtime list` | Lista o backend interno e plugins experimentais. |

Entradas podem ser escalares, arrays JSON, valores nomeados, imagens e NPY,
conforme o contexto. Consulte [operations.md](operations.md#entradas-e-inferência).

## Assinatura e confiança

| Comando | Função |
| --- | --- |
| `mirai key generate NOME` | Gera par Ed25519 local. |
| `mirai sign ARTEFATO --key CHAVE` | Cria envelope DSSE destacado. |
| `mirai verify ARTEFATO --signature SIG --key PUB` | Verifica assinatura e digest. |
| `mirai audit anchor --device NOME` | Ancora e verifica a extensão da auditoria. |

A chave privada fica no control plane. O Agent recebe somente a chave pública
quando a política de admissão assinada é usada.

## Agent e dispositivos

| Comando | Função |
| --- | --- |
| `mirai agent start` | Inicia o Agent local. |
| `mirai agent start --host 0.0.0.0` | Inicia Agent HTTPS pareável em rede privada. |
| `mirai agent rotate` | Rotaciona a identidade offline e invalida clientes. |
| `mirai device add/list/info/remove` | Gerencia destinos registrados. |
| `mirai device pair` | Pareia após verificar código e fingerprint. |
| `mirai device revoke` | Revoga o cliente atual e remove o cadastro. |
| `mirai device clients/role` | Administra clientes e papéis. |
| `mirai device tag` | Define tags não secretas para seleção. |
| `mirai device discover` | Encontra candidatos mDNS; não autentica. |
| `mirai doctor --device NOME` | Diagnostica canal, versão e runtime. |

## Deploy e lifecycle

| Comando | Função |
| --- | --- |
| `mirai launch ARQUIVO --device NOME` | Valida, implanta, ativa e testa. |
| `mirai deploy ARQUIVO --device NOME` | Envia e instala sem ativar automaticamente. |
| `mirai activate ID --device NOME` | Ativa um deployment pronto. |
| `mirai status --device NOME` | Lista deployments e seleção ativa. |
| `mirai run --device NOME` | Executa no deployment ativo. |
| `mirai logs --device NOME` | Consulta eventos recentes. |
| `mirai cleanup --device NOME --keep N` | Simula ou aplica retenção de deployments. |

Operações destrutivas ou de rollout são conservadoras por padrão: consulte a
saída e use a confirmação explícita exigida pelo comando.

## Pilot e frota

| Comando | Função |
| --- | --- |
| `mirai pilot init` | Cria `mirai-pilot.json`. |
| `mirai pilot run` | Executa pipeline, critérios, relatório e rollback. |
| `mirai pilot history/show` | Consulta evidências anteriores. |
| `mirai pilot prune` | Simula ou aplica retenção dos relatórios. |
| `mirai fleet status --selector ...` | Consulta uma seleção de dispositivos. |
| `mirai fleet rollout ARQUIVO ...` | Planeja canário e lotes; `--apply` executa. |
| `mirai fleet observe` | Agrega métricas e sinais heurísticos de drift. |
| `mirai fleet anchor` | Ancora checkpoints dos dispositivos selecionados. |

## Fit e providers

| Comando | Função |
| --- | --- |
| `mirai fit MODELO ...` | Gera Dynamic INT8, compara, mede e aplica gates. |
| `--provider-profile auto` | Seleciona provider disponível segundo a política. |
| `--provider-profile cpu` | Exige CPUExecutionProvider. |
| `--provider-profile cuda` | Exige CUDAExecutionProvider instalado. |
| `--provider-profile directml` | Exige DmlExecutionProvider instalado. |

O Fit mede no host do control plane. A candidata deve ser medida novamente no
hardware final.

## Evidência v0.14

| Comando | Função |
| --- | --- |
| `mirai passport create/verify/show` | Cria e confere o Model Passport. |
| `mirai replay capture/run` | Captura com consentimento e reproduz inferência. |
| `mirai evidence create/verify` | Reúne e verifica evidências portáteis. |
| `mirai arena run/verify/score` | Mede, valida e compara com um SLA. |
| `mirai compatibility add/show` | Mantém a matriz local de medições. |
| `mirai failure-lab ARQUIVO` | Testa falhas em cópias temporárias. |

O contrato completo, inclusive privacidade, schemas e limites, está em
[Projeto Hikari v0.14](hikari-v0.14.md).

## Códigos de saída e automação

Os comandos retornam `0` em sucesso e valor diferente de zero em erro. Para
automação, prefira os relatórios JSON produzidos por Pilot, Arena, Failure Lab
e outras operações em vez de analisar texto destinado a humanos.

Nunca coloque código de pareamento, token, chave privada ou entrada sensível em
scripts versionados.
