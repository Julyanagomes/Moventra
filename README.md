# Moventra CRM — pacote de implantação

Plataforma SaaS multiempresa para gestão fitness. A identidade visual usa grafite/preto, verde-limão e os elementos visuais fornecidos para a marca Moventra CRM.

## Rodar localmente (Windows PowerShell)

1. Instale Python 3.11 ou 3.12.
2. Abra o PowerShell na pasta do projeto.
3. Execute:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe app.py
```

Configure `SECRET_KEY` no `.env` com uma chave longa e aleatória. Para teste local, pode-se deixar o SQLite padrão. A aplicação abre em `http://127.0.0.1:5000`.

## MySQL remoto

1. Crie um banco MySQL 8 e um usuário dedicado no seu provedor.
2. No `.env`, defina `DATABASE_URL` no formato:

```text
mysql+pymysql://USUARIO:SENHA@HOST:3306/NOME_DO_BANCO?charset=utf8mb4
```

Use uma senha URL-encoded se ela contiver caracteres especiais. Nunca coloque o `.env` no Git ou compartilhe as credenciais.

## Publicação (exemplo com serviço que suporte Python/Gunicorn)

- Envie o código para o provedor de hospedagem ou repositório privado.
- Configure as variáveis de ambiente do `.env` no painel do provedor, sem enviar o arquivo `.env`.
- Comando de inicialização: `gunicorn app:app --bind 0.0.0.0:$PORT` (ajuste conforme o provedor).
- Configure domínio, HTTPS, armazenamento de logs e backups automáticos do MySQL.
- Configure `APP_BASE_URL` para a URL HTTPS pública real.
- Não habilite `FLASK_DEBUG` em produção.

## Mercado Pago

Defina `MP_ACCESS_TOKEN` com as credenciais da conta de vendedor. Configure a URL pública `https://SEU-DOMINIO/webhook/mercadopago` como webhook conforme a implementação e o painel do Mercado Pago. Teste primeiro com credenciais de teste; valide eventos e status antes de liberar clientes pagantes.

## Segurança e lançamento

- A senha temporária do aluno deve ser individual e entregue por canal privado; o aluno deve trocá-la no primeiro acesso.
- Não use credenciais padrão compartilhadas em produção.
- Antes de vender, teste isolamento entre empresas (multi-tenant), permissões, webhook idempotente, expiração/cancelamento de assinatura, recuperação de senha, CSRF, limites dos planos, backups e restauração.
- O arquivo está preparado para configuração e implantação; **não está publicado em um servidor remoto**. Hospedagem, domínio, credenciais de produção e webhook precisam ser configurados pelo responsável.
