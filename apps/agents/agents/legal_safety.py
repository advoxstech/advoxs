"""Runtime policy shared by every tenant; office prompts cannot weaken it.

This is a conservative evidence gate, not a legal opinion validator. Source
quality and the reference scenarios still require the office's legal review.
"""

import re
import unicodedata

LEGAL_SAFETY_RULE = """
REGRAS OBRIGATÓRIAS DE PRECISÃO E ATENDIMENTO (prevalecem sobre o prompt do agente):
- Identifique-se como assistente digital; nunca se passe por advogado humano.
- Diferencie fatos extraídos de documentos, informação jurídica geral e análise
  individual que depende do advogado. Uma cláusula ou política comercial não
  comprova a validade jurídica da prática e não prevalece sobre a legislação.
- Antes de afirmar obrigação, proibição, prazo legal ou citar lei/julgado, busque
  a base e confira o trecho pertinente, sua aplicabilidade e suas limitações.
  Ausência de resultado não autoriza inventar fundamento nem dar parecer
  conclusivo de memória. Se a fonte faltar, for insuficiente ou conflitante,
  explique brevemente a limitação e use solicitar_atendimento_humano para revisão.
- Não prometa resultado, indenização ou prazo de resposta da equipe.
- No arrependimento de compra a distância, confirme recebimento e data do pedido
  antes de concluir o enquadramento no art. 49 do CDC. Não subordine o direito
  à política da loja, à conveniência comercial ou ao estado de revenda. Não
  transfira ao consumidor o frete de devolução quando o art. 49 for aplicável.
  Referências para conferência: CDC art. 49 (Planalto) e STJ REsp 1.340.604/RJ.
- Seja breve: normalmente até 100 palavras, uma orientação e uma pergunta
  essencial por vez. Amplie se o cliente pedir ou a segurança exigir. Não envie
  checklists extensos nem peça dados de terceiros sem necessidade demonstrada.
- Não sugira ZIP. Peça PDF, DOCX ou TXT para documentos; não repita senha recebida.
- Pedido de humano não é transferência entre agentes digitais nem urgência
  automática. Use solicitar_atendimento_humano, permaneça no agente atual e
  informe que o pedido foi registrado. Não diga que e-mail foi entregue ou que
  um advogado assumiu. Só o botão Assumir atendimento pausa a IA. Enquanto
  aguarda, ofereça apoio factual breve, sem insistir em aconselhamento jurídico
  ou gerar novos documentos. Não cancele a pendência por conta própria.
"""


def normalized(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )


def legal_review_reason(
    answer: str,
    evidence: dict,
    legal_reference_ids: list[str] | None = None,
    context: str = "",
) -> str | None:
    """Block specific normative conclusions without an attributed source.

    Documentary extraction (dates/values) and factual intake remain available.
    Even attributed sources are not proof that an interpretation is correct.
    """
    text = normalized(answer)
    normative = re.search(
        r"\b(?:art(?:igo)?\.?\s*\d+|cdc\b|clt\b|lei\s+(?:n[ºo.]?\s*)?\d+"
        r"|prazo legal|legalmente obrigad[oa]|tem direito a|e obrigatori[oa]"
        r"|(?:deve|devera|precisa)\s+(?:pagar|reembolsar|restituir|indenizar)"
        r"|(?:nao pode|e proibido)\s+(?:cobrar|recusar|negar))",
        text,
    )
    legal_sources = [
        source
        for source in evidence.get("sources", [])
        if source.get("document_id") in (legal_reference_ids or [])
    ]
    withdrawal = "arrependimento" in normalized(context)
    if withdrawal and re.search(
        r"(?:frete|reembolso|estorno)[^.\n]{0,90}"
        r"(?:depende da politica|conforme a politica|condicoes comerciais|estado de revenda)",
        text,
    ):
        return "Arrependimento: possível condicionamento indevido do frete ou reembolso"
    if normative and (not legal_sources or evidence.get("search_failed")):
        return "Conclusão jurídica sem fonte suficiente; necessária revisão do advogado"
    return None


REVIEW_ANSWER = (
    "Não tenho uma fonte suficiente para confirmar essa orientação jurídica com segurança. "
    "Registrei um pedido de revisão pela equipe do escritório. "
    "Enquanto aguarda, posso ajudar a organizar os fatos e os documentos já enviados."
)
