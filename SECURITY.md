# Política de segurança

## Versões atendidas

O MiraiOS está em estágio alpha. Correções de segurança são preparadas para a
série mais recente; versões minor anteriores devem ser atualizadas antes de
receber suporte.

| Série | Correções de segurança |
| --- | --- |
| 0.12.x | Sim |
| 0.11.x e anteriores | Não |

## Como relatar

Não publique exploit, token, chave, modelo privado ou dados de cliente em uma
issue pública.

1. Abra a aba **Security** do repositório.
2. Use **Report a vulnerability** quando a opção estiver disponível.
3. Inclua versão, impacto, pré-condições, passos mínimos e uma sugestão de
   correção, se houver.
4. Dê tempo para triagem e correção antes de divulgar detalhes.

Se o formulário privado não estiver disponível, abra apenas uma issue curta
pedindo um canal privado, sem incluir detalhes técnicos da vulnerabilidade.

## Escopo prioritário

- escape de diretório ou leitura de arquivos do Agent;
- bypass de autenticação, RBAC, pinning TLS ou limitação de pareamento;
- execução de código por pacote `.mirai`, NPY, imagem ou plugin;
- verificação incorreta de hash ou assinatura;
- exposição de tokens, chaves privadas ou entradas ocultadas de relatório;
- remoção do deployment ativo ou de arquivos fora da política de retenção.
- bypass da política `signed`, trust store ou quarentena estrutural;
- adulteração não detectada entre deploy, ativação e inferência;
- divergência, truncamento ou edição não detectada da cadeia de auditoria.

## Limites conhecidos

O Agent deve operar em localhost ou rede privada protegida por firewall. A
v0.12 não oferece gateway público, autoridade certificadora, mTLS, antivírus,
cofre de chaves, isolamento de processo ou ancoragem externa automática da
auditoria. mDNS encontra candidatos, mas não autentica dispositivos. Consulte
o [modelo de ameaças](docs/threat-model.md).
