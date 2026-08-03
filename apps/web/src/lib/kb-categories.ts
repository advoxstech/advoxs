// Categorias fixas da base de conhecimento (POP GVA Digital) — só
// organização/visual na árvore de /base-de-conhecimento, não afeta a busca
// do agente. Mesmos valores do CHECK constraint em
// apps/api/app/models/knowledge_base_file.py.
export const KB_CATEGORIES: { value: string; label: string }[] = [
  { value: "artigos_cientificos", label: "Artigos Científicos" },
  { value: "materias_jornalisticas", label: "Matérias Jornalísticas" },
  { value: "decisoes_tribunais", label: "Decisões de Tribunais" },
  { value: "livros_digitais", label: "Livros Digitais" },
  { value: "processos_judiciais", label: "Processos Judiciais" },
  { value: "modelos_contratos", label: "Modelos de Contratos" },
  { value: "pecas_processuais", label: "Peças Processuais" },
  { value: "nao_selecionaveis", label: "Não Selecionáveis" },
];

export const KB_CATEGORY_LABELS: Record<string, string> = Object.fromEntries(
  KB_CATEGORIES.map((c) => [c.value, c.label]),
);

export const UNCATEGORIZED_LABEL = "Sem categoria";

export function kbCategoryLabel(category: string | null): string {
  if (!category) return UNCATEGORIZED_LABEL;
  return KB_CATEGORY_LABELS[category] ?? category;
}
