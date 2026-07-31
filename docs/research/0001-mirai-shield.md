# Pesquisa 0001 — Mirai Shield

**Estado:** aceito na v0.12  
**Pergunta:** como admitir e operar artefatos Edge AI sem transformar uma
assinatura em uma promessa falsa de segurança?

## Princípio adotado

Assinatura, validação estrutural, persistência e auditoria resolvem problemas
diferentes. A v0.12 mantém essas camadas separadas e falha de forma fechada
quando uma política foi declarada.

```text
proveniência ≠ segurança do modelo ≠ integridade em disco ≠ isolamento
```

## Fontes primárias estudadas

### ONNX Runtime

A [documentação oficial do ONNX Runtime](https://onnxruntime.ai/docs/) alerta
que um modelo ONNX malicioso pode consumir quantidades excessivas de memória
ou computação e recomenda inspeção e teste seguros de modelos não confiáveis.

**Decisão:** carregar sem dados externos, inspecionar subgrafos e tensores,
aplicar budgets antes da sessão e documentar que ainda falta sandbox.

### Python `http.server` e JSON

A [documentação de `http.server`](https://docs.python.org/3/library/http.server.html)
declara que ele não é recomendado para produção. A
[documentação de `json`](https://docs.python.org/3/library/json.html) explica
que o módulo aceita, por padrão, números não finitos e nomes repetidos.

**Decisão:** manter o Agent restrito a laboratório/rede privada, aplicar
limites e cabeçalhos, e criar um codec estrito compartilhado. Uma futura
superfície pública exige substituir ou isolar o servidor.

### TUF e Uptane

A [especificação TUF](https://theupdateframework.github.io/specification/latest/)
define objetivos contra rollback, freeze, mix-and-match e comprometimento de
chaves, além de snapshots consistentes. O
[padrão Uptane](https://uptane.org/docs/1.0.0/standard/uptane-standard)
adapta esse raciocínio para dispositivos conectados e atualizações.

**Decisão:** introduzir política e trust store explícitos agora, sem alegar
compatibilidade TUF/Uptane. Expiração, revogação, threshold e metadados de
atualização continuam no roadmap.

### Sigstore, DSSE e in-toto

O [Cosign](https://github.com/sigstore/cosign) trabalha com assinaturas e
bundles verificáveis; DSSE e predicados in-toto separam o envelope do conteúdo
de proveniência.

**Decisão:** preservar a assinatura destacada DSSE da v0.11, ligar o digest ao
nome original e exigir verificação de claims, não apenas da operação
criptográfica. A v0.12 não implementa transparência Sigstore nem identidade
OIDC.

### OCI Distribution

A [Distribution Specification da OCI](https://github.com/opencontainers/distribution-spec)
e seus descritores tratam conteúdo por digest e tamanho.

**Decisão:** instalar pacote e modelo por conteúdo, reutilizar bytes idênticos
e revalidar digest em uso. O Mirai Package não se torna uma imagem OCI.

### Mender e KubeEdge

O [Mender](https://github.com/mendersoftware/mender) enfatiza atualização
atômica, rollback e resistência a perda de energia. O
[KubeEdge](https://github.com/kubeedge/kubeedge) enfatiza autonomia Edge,
colaboração confiável e agente leve.

**Decisão:** manter Agent local, pequeno e capaz de operar sem um cluster;
usar transições recuperáveis e checkpoints duráveis antes de adicionar um
control plane de frota.

### GitHub Actions

O guia oficial de
[uso seguro do GitHub Actions](https://docs.github.com/en/actions/reference/security/secure-use)
recomenda permissões mínimas e fixação de actions de terceiros por SHA
completo. A documentação de
[artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)
descreve proveniência verificável de builds.

**Decisão:** `contents: read`, actions fixadas por SHA, Dependabot e gates de
qualidade agora. Attestation do bundle de release fica para uma etapa que
inclua desenho explícito de permissões e publicação.

### OWASP API Security

O [OWASP API Security Top 10](https://owasp.org/API-Security/editions/2023/en/0x11-t10/)
trata consumo irrestrito de recursos como risco de API.

**Decisão:** timeout de socket, fila limitada, semáforo de requisições e
orçamento separado para inferência/validação.

## Alternativas rejeitadas nesta versão

- **Aceitar qualquer assinatura válida:** uma assinatura sem trust store não
  responde quem está autorizado.
- **Confiar só no hash do upload:** não detecta alteração posterior em disco.
- **Carregar ONNX para descobrir se é válido:** pode resolver external data
  antes que a política tenha chance de recusar.
- **Chamar hash chain de log inviolável:** log e checkpoint no mesmo host não
  resistem a controle administrativo total.
- **Migrar imediatamente para Kubernetes/Triton:** aumentaria a superfície e o
  custo antes de consolidar a fronteira local que diferencia o projeto.
- **Prometer zero falhas:** testes demonstram comportamentos conhecidos; não
  provam ausência de vulnerabilidades.

## Resultado

O Mirai Shield é uma composição pequena de mecanismos conhecidos, aplicada ao
fluxo específico do MiraiOS. A inovação útil não é inventar criptografia: é
tornar a decisão de admissão observável, reproduzível e ligada à integridade
que será verificada novamente no momento de uso.
