import os
import tempfile
from urllib.parse import urlparse

import requests
from langchain.tools import tool
from langgraph.types import Command
from loguru import logger

from clients.document_generation import DocumentGenerationError, generate_pdf
from clients.retrieval import retrieval_escritorio, retrieval_usuario
from services.document_storage import build_public_url, save_pdf

ENDPOINT_URL = "http://localhost:8000/documents/users/insert"  # ajuste a URL base
API_KEY = "LASKDJFLK234LWAK"  # ajuste conforme necessário
CONVERSATION_ID = "1"  # ajuste conforme necessário

# Custo fixo em créditos por documento gerado (contrato/multa/etc), somado ao
# custo normal de tokens do turno — calibrado como um valor pequeno inicial,
# mesma lógica das outras pendências de precificação do projeto.
DOCUMENT_GENERATION_CREDIT_COST = 20


async def _gerar_e_entregar(tipo: str, filename: str, text_payload: str) -> str | Command:
    """Encadeia geração do PDF (draft LLM -> LaTeX -> compile) + storage local
    + montagem do link público — usado pelas 6 tools de documento abaixo.
    Devolve uma string de erro (vira ToolMessage normal) em caso de falha, ou
    um Command atualizando `generated_documents` no estado do grafo em caso
    de sucesso (ver services/call_agent.py e api/routes.py, que leem esse
    campo pra efetivamente enviar o PDF pelo WhatsApp/Z-API do tenant)."""
    try:
        pdf_bytes = await generate_pdf(tipo, text_payload)
        doc_id = save_pdf(pdf_bytes)
        link = build_public_url(doc_id)
    except DocumentGenerationError as exc:
        return str(exc)

    return Command(
        update={
            "generated_documents": [
                {
                    "link": link,
                    "filename": filename,
                    "credit_cost": DOCUMENT_GENERATION_CREDIT_COST,
                }
            ]
        }
    )


@tool("enviar_documento")
def enviar_documento(url: str, conversation_id: str) -> str:
    """
    Baixa um documento a partir de uma URL e envia para o endpoint de inserção.

    Args:
        documento: URL do documento a ser enviado.
        conversation_id: ID da conversa atual.

    Returns:
        Mensagem indicando sucesso ou falha na inserção do documento.
    """
    logger.info("Enviando documento | url={}", url)

    # 1. Baixar o documento
    try:
        logger.info("Baixando documento | url={}", url)
        download_response = requests.get(url, timeout=30)
        download_response.raise_for_status()
    except requests.exceptions.MissingSchema:
        logger.error("URL inválida | url={}", url)
        return "Falha ao enviar documento: URL inválida. Verifique se a URL está correta."
    except requests.exceptions.ConnectionError:
        logger.error("Erro de conexão ao baixar documento | url={}", url)
        return "Falha ao enviar documento: não foi possível conectar à URL fornecida."
    except requests.exceptions.Timeout:
        logger.error("Timeout ao baixar documento | url={}", url)
        return "Falha ao enviar documento: tempo limite excedido ao baixar o arquivo."
    except requests.exceptions.HTTPError as e:
        logger.error("Erro HTTP ao baixar documento | error={}", e)
        return (
            "Falha ao enviar documento: erro ao baixar o arquivo "
            f"(HTTP {download_response.status_code})."
        )
    except Exception as e:
        logger.error("Erro inesperado ao baixar documento | error={}", e)
        return "Falha ao enviar documento: erro inesperado ao baixar o arquivo."

    # 2. Inferir nome e extensão do arquivo
    parsed_url = urlparse(url)
    filename = os.path.basename(parsed_url.path) or "documento"
    if "." not in filename:
        content_type = download_response.headers.get("Content-Type", "")
        ext_map = {
            "application/pdf": ".pdf",
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "text/plain": ".txt",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        }
        filename += ext_map.get(content_type.split(";")[0].strip(), ".bin")

    # 3. Salvar em arquivo temporário e enviar ao endpoint
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as tmp_file:
            tmp_file.write(download_response.content)
            tmp_path = tmp_file.name

        logger.info("Arquivo temporário criado | path={}", tmp_path)

        with open(tmp_path, "rb") as f:
            files = {
                "file": (
                    filename,
                    f,
                    download_response.headers.get("Content-Type", "application/octet-stream"),
                )
            }
            data = {"convesation_id": conversation_id}  # typo mantido igual ao endpoint
            headers = {"Authorization": f"{API_KEY}"}  # ajuste conforme seu esquema de auth

            logger.info("Enviando para o endpoint | url={}", ENDPOINT_URL)
            insert_response = requests.post(
                ENDPOINT_URL,
                files=files,
                data=data,
                headers=headers,
                timeout=100,
            )

    except Exception as e:
        logger.error("Erro ao enviar documento ao endpoint | error={}", e)
        return "Falha ao enviar documento: erro ao comunicar com o servidor de inserção."
    finally:
        # Limpar arquivo temporário
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
            logger.info("Arquivo temporário removido | path={}", tmp_path)

    # 4. Tratar resposta
    logger.info("Resposta do endpoint | status={}", insert_response.status_code)

    if insert_response.status_code in (200, 201):
        try:
            body = insert_response.json()
            msg = body.get("message") or body.get("detail") or "Documento inserido com sucesso."
            logger.info("Documento inserido com sucesso | message={}", msg)
            return f"Documento inserido com sucesso. Resposta do servidor: {msg}"
        except Exception:
            return "Documento inserido com sucesso."

    elif insert_response.status_code == 401:
        logger.error("Não autorizado ao inserir documento")
        return "Falha ao inserir documento: acesso não autorizado. Verifique a API key."

    elif insert_response.status_code == 422:
        logger.error("Erro de validação | response={}", insert_response.text)
        return (
            "Falha ao inserir documento: dados inválidos enviados ao servidor (erro de validação)."
        )

    elif insert_response.status_code >= 500:
        logger.error("Erro interno do servidor | status={}", insert_response.status_code)
        return (
            "Falha ao inserir documento: erro interno no servidor "
            f"(HTTP {insert_response.status_code}). Tente novamente mais tarde."
        )

    else:
        logger.error(
            "Resposta inesperada | status={} | response={}",
            insert_response.status_code,
            insert_response.text,
        )
        return (
            "Falha ao inserir documento: resposta inesperada do servidor "
            f"(HTTP {insert_response.status_code})."
        )


@tool("buscar_base_conhecimento_agente")
async def buscar_base_conhecimento_agente(
    query: str,
    conversation_id: str,
    knowledge_base_file_ids: list[str] | None = None,
) -> str:
    """Busca na base de conhecimento anexada a este agente.

    Use quando a pergunta envolver documentos, materiais, modelos ou
    orientações que você tenha na sua própria base de conhecimento — cada
    agente só tem acesso aos arquivos que foram anexados especificamente a
    ele, nunca à base de outro agente.

    Args:
        query: Pergunta ou tema a ser pesquisado.
        conversation_id: preenchido automaticamente pelo sistema.
        knowledge_base_file_ids: preenchido automaticamente pelo sistema.
    """
    if not knowledge_base_file_ids:
        return "Este agente não tem nenhuma base de conhecimento anexada."
    return await retrieval_escritorio(conversation_id, query, doc_ids=knowledge_base_file_ids)


@tool("bucar_base_conhecimento_usuario")
async def bucar_base_conhecimento_usuario(query: str, conversation_id: str) -> str:
    """Busca na base de documentos pessoais enviados pelo próprio usuário.

    Use esta ferramenta apenas quando o usuário indicar explicitamente que quer
    verificar algo nos documentos que ele mesmo enviou — por exemplo: "você tem
    meu contrato?", "busca no que te mandei", "verifica nos meus documentos",
    "analisa o arquivo que te enviei". Não use por padrão; espere uma indicação
    clara de que a busca deve ser feita na base pessoal do usuário.

    Args:
        query: Trecho ou tema a ser localizado nos documentos do usuário.
        conversation_id: ID da conversa/usuário.
    """
    return await retrieval_usuario(conversation_id, query)


@tool("transfer_to_agent")
def transfer_to_agent(agent_id: str, valid_agent_ids: list[str] | None = None) -> str:
    """
    Transfere a conversa para outro agente do escritório.

    Args:
        agent_id: id do agente de destino — escolha entre os agentes
            disponíveis no seu contexto, nunca invente um id.
        valid_agent_ids: preenchido automaticamente pelo sistema.
    """
    if agent_id not in (valid_agent_ids or []):
        return (
            "Transferência recusada: agent_id inválido — escolha um dos agentes "
            "disponíveis no seu contexto."
        )
    return Command(
        update={
            "current_agent_id": agent_id,
            "receptive_message_specialist": True,
        }
    )


@tool("fazer_contrato")
async def fazer_contrato(
    tipo_contrato: str,
    objetivo_contrato: str,
    dados_contratante: str,
    dados_contratado: str,
    servico_ou_objeto: str,
    valor_acordado: str,
    forma_pagamento: str,
    prazo_contrato: str,
    multa_ou_penalidade: str,
    regras_importantes: str,
    foro_contrato: str,
    informacoes_especificas: str = "",
    observacoes: str = "",
    conversation_id: str = "",
) -> str | Command:
    """Gera um contrato em PDF (a partir dos dados coletados do cliente) e
    envia ao contato pelo WhatsApp. Só chame depois de coletar e confirmar
    TODOS os dados com o cliente e ele confirmar que quer que o contrato seja
    feito — nunca com dados incompletos ou assumidos.

    Args:
        tipo_contrato: tipo do contrato (ex: prestação de serviço, locação).
        objetivo_contrato: objetivo/finalidade do contrato.
        dados_contratante: nome/qualificação completa do contratante.
        dados_contratado: nome/qualificação completa do contratado.
        servico_ou_objeto: descrição do serviço ou objeto do contrato.
        valor_acordado: valor acordado entre as partes.
        forma_pagamento: forma de pagamento acordada.
        prazo_contrato: prazo/vigência do contrato.
        multa_ou_penalidade: multa ou penalidade por descumprimento.
        regras_importantes: regras/cláusulas importantes a incluir.
        foro_contrato: foro/comarca eleito pra dirimir conflitos.
        informacoes_especificas: informações adicionais específicas deste contrato.
        observacoes: observações gerais (opcional).
        conversation_id: preenchido automaticamente pelo sistema.
    """
    logger.info("Ferramenta fazer_contrato chamada | conversation_id={}", conversation_id)
    campos = {
        "Tipo de contrato": tipo_contrato,
        "Objetivo do contrato": objetivo_contrato,
        "Dados do contratante": dados_contratante,
        "Dados do contratado": dados_contratado,
        "Serviço ou objeto": servico_ou_objeto,
        "Valor acordado": valor_acordado,
        "Forma de pagamento": forma_pagamento,
        "Prazo do contrato": prazo_contrato,
        "Multa ou penalidade": multa_ou_penalidade,
        "Regras importantes": regras_importantes,
        "Foro do contrato": foro_contrato,
        "Informações específicas": informacoes_especificas,
        "Observações": observacoes,
    }
    text_payload = "\n".join(f"{campo}: {valor}" for campo, valor in campos.items())
    return await _gerar_e_entregar("contrato", "Contrato.pdf", text_payload)


@tool("fazer_multa")
async def fazer_multa(
    nome_condominio: str,
    unidade: str,
    nome_morador: str,
    tipo_infracao: str,
    descricao_ocorrencia: str,
    data_ocorrencia: str,
    hora_ocorrencia: str,
    local_ocorrencia: str,
    houve_advertencia_previa: bool,
    nome_responsavel: str,
    cidade_data_emissao: str,
    valor_multa: str,
    percentual_taxa_condominial: str,
    forma_cobranca: str,
    data_vencimento: str,
    prazo_recurso: str,
    destino_recurso: str,
    artigo_infringido: str = "",
    data_advertencia: str = "",
    descricao_advertencia: str = "",
    observacoes: str = "",
    conversation_id: str = "",
) -> str | Command:
    """Gera uma notificação de multa condominial em PDF e envia ao contato
    pelo WhatsApp. Só chame depois de coletar e confirmar TODOS os dados com
    o cliente e ele confirmar que quer que a multa seja feita — nunca com
    dados incompletos ou assumidos.

    Args:
        nome_condominio: nome do condomínio.
        unidade: unidade/apartamento do morador multado.
        nome_morador: nome do morador multado.
        tipo_infracao: tipo da infração cometida.
        descricao_ocorrencia: descrição detalhada da ocorrência.
        data_ocorrencia: data em que ocorreu a infração.
        hora_ocorrencia: hora em que ocorreu a infração.
        local_ocorrencia: local do condomínio onde ocorreu a infração.
        houve_advertencia_previa: se já houve advertência prévia pelo mesmo motivo.
        nome_responsavel: nome do responsável pela emissão (síndico/administradora).
        cidade_data_emissao: cidade e data de emissão do documento.
        valor_multa: valor da multa aplicada.
        percentual_taxa_condominial: percentual da taxa condominial usado como base do cálculo.
        forma_cobranca: forma de cobrança da multa.
        data_vencimento: data de vencimento da multa.
        prazo_recurso: prazo para o morador recorrer.
        destino_recurso: para onde/quem o recurso deve ser enviado.
        artigo_infringido: artigo da convenção/regimento infringido (opcional).
        data_advertencia: data da advertência prévia, se houve (opcional).
        descricao_advertencia: descrição da advertência prévia, se houve (opcional).
        observacoes: observações gerais (opcional).
        conversation_id: preenchido automaticamente pelo sistema.
    """
    logger.info("Ferramenta fazer_multa chamada | conversation_id={}", conversation_id)
    campos = {
        "Condomínio": nome_condominio,
        "Unidade": unidade,
        "Morador": nome_morador,
        "Tipo de infração": tipo_infracao,
        "Descrição da ocorrência": descricao_ocorrencia,
        "Data da ocorrência": data_ocorrencia,
        "Hora da ocorrência": hora_ocorrencia,
        "Local da ocorrência": local_ocorrencia,
        "Houve advertência prévia": houve_advertencia_previa,
        "Data da advertência prévia": data_advertencia,
        "Descrição da advertência prévia": descricao_advertencia,
        "Artigo infringido": artigo_infringido,
        "Valor da multa": valor_multa,
        "Percentual da taxa condominial": percentual_taxa_condominial,
        "Forma de cobrança": forma_cobranca,
        "Data de vencimento": data_vencimento,
        "Prazo para recurso": prazo_recurso,
        "Destino do recurso": destino_recurso,
        "Responsável pela emissão": nome_responsavel,
        "Cidade e data de emissão": cidade_data_emissao,
        "Observações": observacoes,
    }
    text_payload = "\n".join(f"{campo}: {valor}" for campo, valor in campos.items())
    return await _gerar_e_entregar("multa", "Multa.pdf", text_payload)


@tool("fazer_advertencia")
async def fazer_advertencia(
    nome_condominio: str,
    unidade: str,
    nome_morador: str,
    tipo_infracao: str,
    descricao_ocorrencia: str,
    data_ocorrencia: str,
    hora_ocorrencia: str,
    nome_responsavel: str,
    cidade_data_emissao: str,
    artigo_infringido: str = "",
    observacoes: str = "",
    conversation_id: str = "",
) -> str | Command:
    """Gera uma advertência condominial em PDF e envia ao contato pelo
    WhatsApp. Só chame depois de coletar e confirmar TODOS os dados com o
    cliente e ele confirmar que quer que a advertência seja feita — nunca
    com dados incompletos ou assumidos.

    Args:
        nome_condominio: nome do condomínio.
        unidade: unidade/apartamento do morador advertido.
        nome_morador: nome do morador advertido.
        tipo_infracao: tipo da infração cometida.
        descricao_ocorrencia: descrição detalhada da ocorrência.
        data_ocorrencia: data em que ocorreu a infração.
        hora_ocorrencia: hora em que ocorreu a infração.
        nome_responsavel: nome do responsável pela emissão (síndico/administradora).
        cidade_data_emissao: cidade e data de emissão do documento.
        artigo_infringido: artigo da convenção/regimento infringido (opcional).
        observacoes: observações gerais (opcional).
        conversation_id: preenchido automaticamente pelo sistema.
    """
    logger.info("Ferramenta fazer_advertencia chamada | conversation_id={}", conversation_id)
    campos = {
        "Condomínio": nome_condominio,
        "Unidade": unidade,
        "Morador": nome_morador,
        "Tipo de infração": tipo_infracao,
        "Descrição da ocorrência": descricao_ocorrencia,
        "Data da ocorrência": data_ocorrencia,
        "Hora da ocorrência": hora_ocorrencia,
        "Artigo infringido": artigo_infringido,
        "Responsável pela emissão": nome_responsavel,
        "Cidade e data de emissão": cidade_data_emissao,
        "Observações": observacoes,
    }
    text_payload = "\n".join(f"{campo}: {valor}" for campo, valor in campos.items())
    return await _gerar_e_entregar("advertencia", "Advertência.pdf", text_payload)


@tool("fazer_oficio")
async def fazer_oficio(
    numero_oficio: str,
    destinatario: str,
    assunto: str,
    objetivo_oficio: str,
    descricao_detalhada: str,
    nome_responsavel: str,
    cargo_responsavel: str,
    cidade_data: str,
    observacoes: str = "",
    conversation_id: str = "",
) -> str | Command:
    """Gera um ofício em PDF e envia ao contato pelo WhatsApp. Só chame
    depois de coletar e confirmar TODOS os dados com o cliente e ele
    confirmar que quer que o ofício seja feito — nunca com dados
    incompletos ou assumidos.

    Args:
        numero_oficio: número de identificação do ofício.
        destinatario: destinatário do ofício.
        assunto: assunto do ofício.
        objetivo_oficio: objetivo do ofício.
        descricao_detalhada: descrição detalhada do pedido/comunicação.
        nome_responsavel: nome do responsável pela emissão.
        cargo_responsavel: cargo do responsável pela emissão.
        cidade_data: cidade e data de emissão do documento.
        observacoes: observações gerais (opcional).
        conversation_id: preenchido automaticamente pelo sistema.
    """
    logger.info("Ferramenta fazer_oficio chamada | conversation_id={}", conversation_id)
    campos = {
        "Número do ofício": numero_oficio,
        "Destinatário": destinatario,
        "Assunto": assunto,
        "Objetivo do ofício": objetivo_oficio,
        "Descrição detalhada": descricao_detalhada,
        "Responsável pela emissão": nome_responsavel,
        "Cargo do responsável": cargo_responsavel,
        "Cidade e data": cidade_data,
        "Observações": observacoes,
    }
    text_payload = "\n".join(f"{campo}: {valor}" for campo, valor in campos.items())
    return await _gerar_e_entregar("oficio", "Ofício.pdf", text_payload)


@tool("enviar_edital_convocacao")
async def enviar_edital_convocacao(
    nome_condominio: str,
    cnpj_condominio: str,
    endereco_condominio: str,
    tipo_assembleia: str,
    data_assembleia: str,
    local_plataforma: str,
    horario_primeira_convocacao: str,
    horario_segunda_convocacao: str,
    ordem_dia: str,
    nome_responsavel: str,
    cargo_responsavel: str,
    cidade_data_emissao: str,
    informacoes_complementares: str = "",
    conversation_id: str = "",
) -> str | Command:
    """Gera um edital de convocação de assembleia condominial em PDF e envia
    ao contato pelo WhatsApp. Só chame depois de coletar e confirmar TODOS
    os dados com o cliente e ele confirmar que quer que o edital seja
    enviado — nunca com dados incompletos ou assumidos.

    Args:
        nome_condominio: nome do condomínio.
        cnpj_condominio: CNPJ do condomínio.
        endereco_condominio: endereço do condomínio.
        tipo_assembleia: tipo da assembleia (ordinária/extraordinária).
        data_assembleia: data da assembleia.
        local_plataforma: local físico ou plataforma da assembleia.
        horario_primeira_convocacao: horário da primeira convocação.
        horario_segunda_convocacao: horário da segunda convocação.
        ordem_dia: pauta/ordem do dia da assembleia.
        nome_responsavel: nome do responsável pela convocação (síndico/administradora).
        cargo_responsavel: cargo do responsável pela convocação.
        cidade_data_emissao: cidade e data de emissão do edital.
        informacoes_complementares: informações complementares (opcional).
        conversation_id: preenchido automaticamente pelo sistema.
    """
    logger.info("Ferramenta enviar_edital_convocacao chamada | conversation_id={}", conversation_id)
    campos = {
        "Condomínio": nome_condominio,
        "CNPJ": cnpj_condominio,
        "Endereço": endereco_condominio,
        "Tipo de assembleia": tipo_assembleia,
        "Data da assembleia": data_assembleia,
        "Local/Plataforma": local_plataforma,
        "Horário da 1ª convocação": horario_primeira_convocacao,
        "Horário da 2ª convocação": horario_segunda_convocacao,
        "Ordem do dia": ordem_dia,
        "Responsável": nome_responsavel,
        "Cargo do responsável": cargo_responsavel,
        "Cidade e data de emissão": cidade_data_emissao,
        "Informações complementares": informacoes_complementares,
    }
    text_payload = "\n".join(f"{campo}: {valor}" for campo, valor in campos.items())
    return await _gerar_e_entregar("edital_convocacao", "Edital de Convocação.pdf", text_payload)


@tool("enviar_aviso")
async def enviar_aviso(
    titulo_comunicado: str,
    data: str,
    local_setor: str,
    assunto: str,
    descricao_comunicado: str,
    impactos_alteracoes: str,
    orientacoes: str,
    prazo_periodo: str,
    responsavel_comunicado: str,
    tom_comunicado: str,
    observacoes: str = "",
    conversation_id: str = "",
) -> str | Command:
    """Gera um aviso/comunicado condominial em PDF e envia ao contato pelo
    WhatsApp. Só chame depois de coletar e confirmar TODOS os dados com o
    cliente e ele confirmar que quer que o aviso seja enviado — nunca com
    dados incompletos ou assumidos.

    Args:
        titulo_comunicado: título do comunicado.
        data: data do comunicado.
        local_setor: local/setor afetado.
        assunto: assunto do comunicado.
        descricao_comunicado: descrição detalhada do comunicado.
        impactos_alteracoes: impactos ou alterações causadas.
        orientacoes: orientações para os moradores.
        prazo_periodo: prazo ou período de vigência.
        responsavel_comunicado: responsável pelo comunicado.
        tom_comunicado: tom do comunicado (ex: formal, urgente, informativo).
        observacoes: observações gerais (opcional).
        conversation_id: preenchido automaticamente pelo sistema.
    """
    logger.info("Ferramenta enviar_aviso chamada | conversation_id={}", conversation_id)
    campos = {
        "Título": titulo_comunicado,
        "Data": data,
        "Local/Setor": local_setor,
        "Assunto": assunto,
        "Descrição": descricao_comunicado,
        "Impactos/Alterações": impactos_alteracoes,
        "Orientações": orientacoes,
        "Prazo/Período": prazo_periodo,
        "Responsável": responsavel_comunicado,
        "Tom": tom_comunicado,
        "Observações": observacoes,
    }
    text_payload = "\n".join(f"{campo}: {valor}" for campo, valor in campos.items())
    return await _gerar_e_entregar("aviso", "Aviso.pdf", text_payload)


tools = [
    buscar_base_conhecimento_agente,
    bucar_base_conhecimento_usuario,
    transfer_to_agent,
    fazer_contrato,
    fazer_multa,
    fazer_advertencia,
    fazer_oficio,
    enviar_edital_convocacao,
    enviar_aviso,
]

DOCUMENT_TOOLS = {
    "fazer_contrato",
    "fazer_multa",
    "fazer_advertencia",
    "fazer_oficio",
    "enviar_edital_convocacao",
    "enviar_aviso",
}
