# Central de documentação do MiraiOS

Use esta página para encontrar o nível de detalhe adequado sem percorrer o
histórico inteiro do projeto.

## Quero começar

- [Primeiros passos](getting-started.md): instalação, primeiro Agent, launch e
  Docker.
- [Referência da CLI](cli-reference.md): comandos organizados por tarefa.
- [Operação](operations.md): pareamento, admissão assinada, Pilot, frota,
  entradas e checklist de hardware.

## Quero entender as garantias

- [Modelo de ameaças](threat-model.md)
- [Política de segurança](../SECURITY.md)
- [Mirai Shield — pesquisa e decisões](research/0001-mirai-shield.md)
- [Evidência e benchmark — pesquisa](research/0002-evidence-benchmark.md)

## Quero entender a arquitetura por marco

| Versão | Contrato principal |
| --- | --- |
| [v0.6](hikari-v0.6.md) | Mirai Agent e deploy verificável |
| [v0.7](hikari-v0.7.md) | Lifecycle e inferência remota |
| [v0.8](hikari-v0.8.md) | Hikari Link e pareamento seguro |
| [v0.9](hikari-v0.9.md) | Mirai Package |
| [v0.10](hikari-v0.10.md) | Mirai Pilot, critérios e rollback |
| [v0.11](hikari-v0.11.md) | Trust, Fleet e Secure Inputs |
| [v0.12](hikari-v0.12.md) | Mirai Shield |
| [v0.13](hikari-v0.13.md) | Fleet Control, observabilidade, âncora e Fit |
| **[v0.14](hikari-v0.14.md)** | **Passport, Replay, Evidence, Arena, Matrix e Failure Lab** |

## Quero contribuir

- [Guia de contribuição](../CONTRIBUTING.md)
- [Changelog](../CHANGELOG.md)
- [Issues](https://github.com/start6202783-dotcom/MiraiOS/issues)
- [Pull requests](https://github.com/start6202783-dotcom/MiraiOS/pulls)

## Escopo atual

A v0.14 é alpha e foi desenhada para laboratório, localhost e redes privadas
controladas. Ela não é um sistema operacional Linux, gateway público, serviço
SaaS, ranking público ou programa de certificação automática.
