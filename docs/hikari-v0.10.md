# Projeto Hikari v0.10 — Mirai Pilot

## Objetivo

A v0.10 transforma as operações isoladas do MiraiOS em um procedimento de
aceitação repetível. O **Mirai Pilot** recebe um projeto JSON, valida o
artefato e o dispositivo, implanta o modelo, executa uma inferência de saúde,
mede o desempenho no destino e decide objetivamente se a entrega foi
aprovada.

O resultado não depende apenas de uma mensagem no terminal. Cada execução
gera duas evidências: um documento JSON para automação e um relatório Markdown
para leitura, envio e revisão humana.

## Dois caminhos de uso

### Caminho rápido

Quando a intenção é apenas colocar um modelo para funcionar:

```bash
mirai launch modelo.mirai --device edge --input 5.0
```

`launch` executa `validate → doctor → deploy → activate → run`. Se a inferência
final falhar, a ativação anterior é restaurada. Use `--no-run` somente quando
a entrada necessária não puder ser representada pela API remota atual.

### Piloto com critérios e evidências

Crie o projeto declarativo:

```bash
mirai pilot init
```

Edite `mirai-pilot.json` e execute:

```bash
mirai pilot run
```

Também é possível informar outro arquivo:

```bash
mirai pilot run examples/mirai-pilot.json
```

Todos os caminhos relativos são resolvidos a partir do diretório do arquivo de
configuração, e não do diretório atual do terminal.

## Formato do projeto

```json
{
  "schema_version": 1,
  "name": "dummy-local",
  "artifact": "dummy-1.0.0.mirai",
  "device": "local",
  "inputs": ["5.0"],
  "layout": "auto",
  "benchmark": {
    "runs": 20,
    "warmup": 3
  },
  "acceptance": {
    "expected_result": 6.0,
    "result_tolerance": 0.000001,
    "max_p95_ms": 100.0,
    "min_ips": 5.0
  },
  "report": {
    "directory": ".mirai/reports",
    "include_inputs": false
  }
}
```

| Campo | Função |
| --- | --- |
| `schema_version` | Fixa o contrato da configuração; nesta versão deve ser `1`. |
| `name` | Identifica o piloto e forma o nome dos relatórios. |
| `artifact` | Caminho para um ONNX ou pacote `.mirai`. |
| `device` | Nome de um dispositivo já cadastrado ou pareado. |
| `inputs` | Mesma sintaxe repetível aceita por `mirai run --input`. |
| `layout` | `auto`, `nchw` ou `nhwc`. |
| `benchmark.runs` | Quantidade de inferências medidas no Agent. |
| `benchmark.warmup` | Inferências descartadas antes da medição. |
| `acceptance.expected_result` | Resultado esperado, inclusive listas e objetos. |
| `acceptance.result_tolerance` | Tolerância absoluta e relativa para números. |
| `acceptance.max_p95_ms` | Maior P95 permitido em milissegundos. |
| `acceptance.min_ips` | Menor vazão permitida em inferências por segundo. |
| `report.directory` | Destino dos relatórios JSON e Markdown. |
| `report.include_inputs` | Registra entradas no relatório; o padrão seguro é `false`. |

Campos desconhecidos e configurações acima dos limites são recusados. Um
critério só é avaliado quando aparece no arquivo; assim, o projeto pode começar
com um teste funcional e ganhar metas de desempenho depois de uma medição
realista do hardware.

## Pipeline transacional

Uma execução completa possui estas etapas:

1. `validate_artifact`: valida ONNX ou `.mirai` e registra seu SHA-256;
2. `resolve_device`: localiza o dispositivo sem copiar credenciais;
3. `doctor`: verifica conexão, identidade, versão e runtime;
4. `deploy`: transfere e valida o artefato no Agent;
5. `activate`: torna o candidato ativo;
6. `health_inference`: confirma que o modelo responde com a entrada declarada;
7. `benchmark`: mede warm-up, média, mediana, P95 e IPS no destino;
8. `acceptance`: compara os resultados com todos os critérios;
9. `final_status`: confirma que o candidato aprovado continua ativo.

Cada etapa registra horário, duração, status e detalhes seguros. A execução só
termina como `passed` quando todas as etapas e todos os critérios terminam bem.

## Rollback seguro

Antes do deploy, o Pilot registra qual deployment está ativo.

| Situação | Resultado do rollback |
| --- | --- |
| Havia um modelo ativo diferente | O deployment anterior é reativado. |
| Não havia modelo ativo | O candidato é desativado e volta ao estado `ready`. |
| O candidato já era o ativo | Nenhuma transição desnecessária é feita. |
| Outra ativação ocorreu durante o piloto | O rollback não sobrescreve a nova seleção. |
| O próprio rollback falha | A falha é registrada no relatório para intervenção. |

O candidato reprovado não é apagado. Isso preserva os arquivos e eventos para
diagnóstico, mas evita que ele permaneça atendendo inferências por acidente.

## Relatórios

Os nomes combinam o projeto, o horário UTC e um identificador aleatório:

```text
.mirai/reports/
├── dummy-local-20260731T180000Z-a1b2c3d4.json
└── dummy-local-20260731T180000Z-a1b2c3d4.md
```

O JSON contém identidade e hash do artefato, dados não secretos do dispositivo,
deployment, inferência, benchmark, critérios, etapas, erro e rollback. Token,
código de pareamento e chave privada nunca entram no relatório. As entradas
também ficam ocultas por padrão; habilite `include_inputs` somente com dados de
teste que possam ser armazenados.

O Markdown resume as mesmas evidências em tabelas. Ele pode acompanhar uma
proposta de piloto, uma entrega técnica ou um chamado de suporte, enquanto o
JSON pode ser processado por CI, painéis ou sistemas futuros.

## Limitações conhecidas

> Esta seção registra o estado da v0.10. Uploads remotos, assinaturas e
> retenção foram entregues posteriormente na
> [especificação v0.11](hikari-v0.11.md).

- o benchmark remoto mede a latência informada pelo ONNX Runtime no Agent; o
  tempo de rede não faz parte do P95 do modelo;
- a inferência remota ainda não transfere imagens ou outros arquivos;
- os relatórios são evidências de execução, mas ainda não possuem assinatura
  digital;
- a API não implementa retenção ou remoção automática de deployments antigos;
- não há execução paralela, agendamento ou gerenciamento de frota;
- os limites de desempenho devem ser calibrados no hardware do cliente;
- o projeto permanece alpha e deve operar em localhost ou rede privada.

## Compatibilidade

Todos os comandos da v0.9 permanecem disponíveis. O endpoint de desativação é
aditivo e protegido pelas mesmas regras de TLS e autenticação das demais
operações. ONNX direto e Mirai Package v1 continuam aceitos sem migração.
