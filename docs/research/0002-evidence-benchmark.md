# Pesquisa 0002 — Evidência portátil e benchmark Edge

## Objetivo

Este documento registra as fontes primárias consultadas para a v0.14 e evita
transformar inspiração em alegação de conformidade.

## Fontes

### in-toto Attestation Framework

A [Statement v1 do in-toto](https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md)
liga um ou mais subjects identificados por digest a um `predicateType`. A
especificação ajudou a separar identidade do artefato, conteúdo da evidência e
tipo da declaração.

Decisão MiraiOS: Model Passport e Evidence Bundle usam subject e SHA-256, mas
possuem schemas próprios. A v0.14 não declara que esses documentos são
Statements in-toto.

### DSSE

O [protocolo DSSE](https://github.com/secure-systems-lab/dsse/blob/master/protocol.md)
define Pre-Authentication Encoding para ligar o tipo do payload aos bytes
assinados. O MiraiOS já usava DSSE/Ed25519 e a v0.14 amplia a tipagem dos
artefatos sem alterar o envelope.

Decisão MiraiOS: a assinatura continua destacada e cobre um payload contendo
tipo, nome, tamanho e digest do arquivo. Ela prova posse da chave, não a
veracidade das medições nem a identidade civil do signatário.

### SLSA

A [especificação SLSA v1.1](https://slsa.dev/spec/v1.1/) trata proveniência,
integridade e níveis de garantia para cadeias de software.

Decisão MiraiOS: o passaporte distingue metadados observados de claims
fornecidos pelo usuário. Não há builder isolado, transparência ou nível SLSA
declarado.

### CycloneDX

O [CycloneDX](https://cyclonedx.org/) inclui capacidades de inventário para
componentes de software e IA/ML.

Decisão MiraiOS: Model Passport registra contrato e identidade operacional do
modelo, mas não substitui CycloneDX, SBOM ou ML-BOM. Exportação formal pode ser
adicionada depois em um adaptador, sem alterar o passaporte v1.

### MLCommons / MLPerf Inference

A [documentação de submissão do MLPerf Inference](https://docs.mlcommons.org/inference/submission/)
separa sistemas Edge e Datacenter, cenários, divisões, metadados do sistema,
medição e validação de acurácia. O
[MLPerf Tiny](https://mlcommons.org/benchmarks/inference-tiny/) usa cenários
adequados a dispositivos restritos e admite medição opcional de energia.

Decisão MiraiOS: a primeira suíte usa single-stream, metadados de sistema e
métricas explícitas, mas não usa LoadGen, datasets oficiais, regras de
acurácia ou processo de submissão MLCommons. Por isso cada Arena Result afirma
expressamente que não é uma submissão MLPerf.

## Decisões de desenho

1. **Sem nota universal:** Edge Score depende de workload e SLA declarados.
2. **Sem certificação automática:** assinatura não valida o hardware físico.
3. **Sem upload implícito:** Arena v1 produz arquivo local para submissão
   voluntária futura.
4. **Sem dados brutos no benchmark:** entradas viram descritores e digests.
5. **Captura explícita no Replay:** dados necessários à reprodução podem ser
   sensíveis e exigem consentimento por opção de CLI.
6. **Schemas próprios e versionados:** adapters externos podem ser criados sem
   afirmar conformidade inexistente.

## Trabalho futuro que exige infraestrutura externa

- endpoint público autenticado para submissões;
- verificação de duplicatas, abuso e claims de hardware;
- fixtures oficiais versionadas e política de atualização;
- medição portátil de memória e energia;
- revisão por múltiplas chaves ou laboratórios;
- transparência, suspensão e revogação de selos;
- exportadores in-toto, CycloneDX ou formatos de benchmark externos.
