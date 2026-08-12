# Ativos visuais do MiraiOS

Os arquivos desta pasta seguem a identidade escura do Projeto Hikari, com
branco, ciano e azul como cores principais.

## Arquivos de marca

- `miraios-logo-primary.png`: logotipo horizontal.
- `miraios-mark.png`: símbolo isolado com transparência.

## Ativos do README v0.14

- `miraios-hero-background.jpg`: arte-base abstrata criada para o projeto em
  agosto de 2026, sem texto ou marcas externas.
- `miraios-hero.png`: composição final com símbolo, versão e promessa.
- `miraios-operating-loop.png`: ciclo implantar, executar, observar e
  recuperar.
- `miraios-demo.gif`: demonstração animada dos comandos da cadeia de evidência.

Os três últimos arquivos são regenerados por:

```bash
python scripts/render_readme_assets.py
```

O script usa Pillow e as fontes DejaVu disponíveis no ambiente de CI. A
arte-base permanece versionada para que a composição final seja determinística
e não dependa de serviços externos.

Ao atualizar a versão ou a promessa do projeto, altere o script, regenere os
arquivos e inspecione visualmente o hero, o diagrama e o último frame do GIF.
