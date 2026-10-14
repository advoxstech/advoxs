# Timbrado por escritório

O administrador envia um PDF A4 vertical, de uma página e até 5 MB em **Perfil → Timbrado dos documentos**. O arquivo fica num volume privado, com nome derivado do ID do escritório. A API só entrega a prévia ao usuário autenticado no escritório; agentes recebem acesso de leitura ao mesmo volume. Substituir e remover não alteram documentos já enviados.

Sem arquivo cadastrado, a geração atual continua sem timbrado. O timbrado de teste da Advoxs não é cadastrado automaticamente em nenhum escritório.

## Formato de arte

A composição reserva 4,4 cm no topo, 3,2 cm na base, 2,8 cm à esquerda e 2,5 cm à direita. O design enviado deve manter a área restante livre. A mesma página de timbrado é aplicada em todas as páginas do documento. Não são aceitos PDFs protegidos por senha, com várias páginas, anotações ou ações interativas.

## Publicação

1. Publicar primeiro o `api_core` com o endpoint `POST /compile_latex_with_letterhead`. O endpoint antigo `POST /compile_latex` permanece disponível.
2. Publicar Advoxs web, API e agents juntos. O `docker-compose.yml` cria o volume `letterheads`, montado na API em escrita e nos agentes somente em leitura. Não há migration de banco.
3. Validar um PDF curto e outro longo com o timbrado do escritório de teste. Confirmar a geração sem timbrado em outro escritório e conferir a prévia no painel.

Não é necessária uma pasta do Google Drive. O serviço `api_core` continua exigindo sua chave de API; o PDF do timbrado é transmitido apenas na chamada de compilação autenticada. O arquivo de exemplo usado durante o desenvolvimento não fica no código nem na imagem Docker.
