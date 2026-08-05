# Modelo de ameaças do MiraiOS v0.13

## Escopo

Este documento descreve o que a v0.13 protege, o que somente detecta e o que
permanece fora do escopo. O cenário recomendado é um Agent em localhost ou em
rede privada controlada, operado por pessoas que conseguem confirmar um
fingerprint e distribuir uma chave pública por canal confiável.

## Ativos

- chave privada de assinatura no computador de release;
- chave privada TLS e tokens do Agent;
- pacotes, modelos, contratos e deployments ativos;
- entradas e resultados de inferência;
- registros de clientes, lifecycle e auditoria;
- ledger de âncoras, planos de rollout e relatórios do Mirai Fit;
- métricas operacionais sem entradas ou saídas brutas;
- disponibilidade de CPU, memória e disco do dispositivo.

## Fronteiras

```mermaid
flowchart LR
    U[Operador] -->|chave privada| C[CLI/Pilot]
    C -->|TLS + pinning + token| A[Agent]
    K[Chave pública confiável] --> A
    A --> Q[Admissão e quarentena]
    Q --> R[Runtime ONNX]
    A --> D[(Disco local)]
    A --> L[(Auditoria)]
    C --> X[(Ledger externo local)]
    L -->|prova de extensão| X
    A --> M[(Métricas limitadas)]
```

| Fronteira | Dados não confiáveis | Controle principal |
| --- | --- | --- |
| Rede | URL, headers, JSON, uploads | TLS, pinning, token, RBAC, limites, JSON estrito |
| Artefato | `.mirai`, ONNX, DSSE | assinatura exigível, SHA-256, schema, quarentena |
| Runtime | grafo e tensores | budgets estruturais e concorrência limitada |
| Disco | estado anterior ou alterado | escrita atômica, permissões, digest em uso |
| Auditoria | edição/reordenação/truncamento | hash chain e checkpoint |
| Control plane | tags, seletor, política e concorrência | schema, limites, plano padrão, gate e rollback |
| Âncora externa | regressão ou cadeia reescrita | checkpoint conhecido e prova contínua de extensão |
| Observabilidade | cardinalidade, dados privados e disco | labels controlados, resumos limitados e flush em lote |
| Fit | variante incorreta ou publicação parcial | comparação, gates, staging e restauração transacional |

## Ameaças tratadas

- intermediário de rede sem o certificado fixado;
- reutilização, força bruta limitada ou revogação de credenciais;
- cliente autenticado operando acima do papel recebido;
- pacote não assinado em Agent com política `signed`;
- assinatura por chave desconhecida ou ligada a outro arquivo;
- path traversal, link, arquivo extra e colisão de nomes;
- JSON ambíguo, não finito, profundo ou volumoso;
- ONNX com external data ou estrutura acima dos budgets;
- arquivo alterado entre validação, ativação e inferência;
- escrita parcial ou colisão de temporário;
- edição, reordenação e truncamento comum da auditoria;
- regressão do Agent em relação a um head já ancorado no control plane;
- rollout acidental sem confirmação e propagação após exceder o gate de falhas;
- persistência de entradas, imagens ou resultados completos na telemetria;
- publicação de variante INT8 reprovada ou substituição parcial de artefatos;
- rajada de requisições ou operações pesadas acima dos limites locais.

## Ameaças parcialmente tratadas

| Ameaça | Mitigação atual | Risco residual |
| --- | --- | --- |
| Exaustão de recursos pelo modelo | limites estruturais e de concorrência | um operador ONNX válido ainda pode ser caro |
| Compromisso do dispositivo | permissões, hashes e auditoria | administrador/root pode substituir processo, chaves e checkpoint |
| Compromisso da chave de release | trust store explícito | não há expiração, revogação distribuída ou threshold |
| Decodificador vulnerável | allowlist, limites e validação | Pillow, NumPy e ONNX são dependências complexas |
| Negação de serviço na rede | timeout, fila e semáforos | não há proxy dedicado, rate limit geral ou proteção volumétrica |
| Compromisso simultâneo | âncora fora do Agent | controle do Agent e do control plane permite reescrever os dois ledgers |
| Drift de dados | mudança de média em duas janelas | não mede acurácia, rótulos ou mudança conceitual |
| Otimização por hardware | hardware do benchmark registrado | Fit v1 mede no control plane, não no destino |

## Fora do escopo da v0.13

- internet pública e ambiente multi-tenant hostil;
- isolamento forte entre modelos;
- código Python ou plugins não confiáveis;
- proteção contra administrador/root já comprometido;
- confidencialidade de modelo contra o dono do dispositivo;
- custódia profissional de chaves, HSM/KMS ou assinatura threshold;
- implementação compatível completa com TUF/Uptane;
- serviço público de transparência ou testemunhas independentes da âncora;
- garantia estatística de concept drift ou monitoramento de acurácia;
- calibração INT8 estática, compilação para NPU ou certificação da variante;
- certificação de segurança ou prova formal.

## Regras operacionais

1. Mantenha a chave privada de release fora do Agent.
2. Entregue a chave pública e o fingerprint por canal independente.
3. Use `--admission signed` em pilotos que exigem proveniência.
4. Restrinja a porta por firewall e nunca faça port forwarding público.
5. Execute o Agent com usuário sem privilégios e diretório exclusivo.
6. Execute `mirai fleet anchor` com frequência e exporte o ledger para uma
   fronteira administrativa independente quando a ameaça exigir.
7. Atualize dependências após os gates e testes do projeto.
8. Trate CUDA, DirectML, plugins e RISC-V como experimentais até validação real.

## Próximas reduções de risco

- subprocesso dedicado por runtime com limites de memória/CPU e watchdog;
- mTLS e rotação com recuperação testada;
- metadados de expiração, revogação e threshold inspirados em TUF;
- transparência independente e assinatura periódica do ledger de âncoras;
- fuzzing contínuo dos parsers e corpus de modelos malformados;
- proxy de produção ou serviço assíncrono endurecido antes de qualquer uso
  fora de rede privada.
