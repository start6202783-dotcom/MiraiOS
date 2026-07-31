# Contribuindo com o MiraiOS

Obrigado por contribuir com o **MiraiOS** e o **Projeto Hikari**. Correções,
testes, documentação e propostas para novos dispositivos são bem-vindos.

## Antes de abrir uma issue

Pesquise as issues existentes. Ao relatar um bug, inclua:

- comportamento esperado e comportamento observado;
- passos e comandos para reproduzir;
- sistema operacional e arquitetura;
- versão do Python e do MiraiOS;
- modelo mínimo ou metadados de entradas e saídas, quando possível.

Não publique modelos, dados ou credenciais confidenciais.
Vulnerabilidades devem seguir [SECURITY.md](SECURITY.md), sem detalhes em
issues públicas.

## Ambiente de desenvolvimento

Faça um fork, clone o repositório e crie uma branch:

```bash
git clone https://github.com/SEU_USUARIO/MiraiOS.git
cd MiraiOS
git switch -c feat/minha-funcionalidade
```

Crie um ambiente virtual e instale o projeto em modo editável:

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install --editable ".[dev]"
```

Ative o ambiente antes da instalação quando necessário:

```bash
source .venv/bin/activate
```

No Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

## Validação local

Antes de enviar sua alteração, execute:

```bash
python -m compileall -q src tests scripts
python -m ruff check src tests
python -m mypy src/mirai
python -m bandit -q -r src/mirai
python -m pip_audit --requirement requirements.txt
python -m coverage run -m pytest
python -m coverage report
```

Teste também os fluxos afetados com o modelo de exemplo:

```bash
mirai validate examples/dummy_model.onnx
mirai info examples/dummy_model.onnx
mirai run examples/dummy_model.onnx --input 5
mirai benchmark examples/dummy_model.onnx --runs 10 --warmup 2
mirai pack examples/dummy_model.onnx \
  --name dummy \
  --package-version 1.0.0 \
  --output /tmp/dummy-1.0.0.mirai
mirai validate /tmp/dummy-1.0.0.mirai
mirai run /tmp/dummy-1.0.0.mirai --input 5
```

## Padrões do projeto

- Preserve compatibilidade com Python 3.10–3.13.
- Mantenha mensagens de terminal objetivas e acionáveis.
- Adicione testes para correções e novos comportamentos.
- Não esconda limitações específicas de modelo ou hardware.
- Não marque hardware como validado sem um teste em runner ou dispositivo da
  arquitetura correspondente.
- Preserve compatibilidade de leitura do formato `.mirai` v1.
- Não reduza budgets de segurança ou o piso de cobertura sem justificar a
  mudança no PR e atualizar o modelo de ameaças.
- Todo novo parser, upload ou endpoint deve ter limites explícitos e casos
  negativos.
- Não implemente criptografia própria; use primitivas revisadas e mantenha
  política, assinatura e distribuição de chaves como problemas separados.
- Prefira funções pequenas e módulos com responsabilidade clara.
- Atualize README e CHANGELOG quando o comportamento público mudar.

## Commits e Pull Requests

Use mensagens de commit curtas e descritivas, por exemplo:

```text
fix: valida estrutura real de modelos ONNX
feat: adiciona entradas nomeadas ao runtime
test: cobre imagens NHWC e uint8
```

O Pull Request deve explicar:

- o problema ou objetivo;
- a solução adotada;
- impacto para usuários e desenvolvedores;
- limitações conhecidas;
- comandos usados para validar.

O CI executará 1.239 testes em Python 3.10, 3.11, 3.12 e 3.13, repetirá a
suíte em Linux ARM64 e bloqueará lint, tipos, segurança, dependências ou
cobertura abaixo do piso. Aguarde todos os checks antes da revisão final.
