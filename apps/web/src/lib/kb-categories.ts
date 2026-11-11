// Árvore da base de conhecimento: pastas A–F e Z, iguais para todo agente.
// Só os nomes de Z.3 a Z.6 mudam, conforme a especialidade do agente. O valor
// de cada pasta é a `category` do arquivo — mesmos valores do CHECK constraint
// (migration 0053) e de apps/api/app/core/knowledge_tree.py.
//
// A pasta F ("Não selecionáveis") é a única que muda o atendimento: o agente
// não consulta os arquivos dela.

export type KbFolder = {
  /** `category` dos arquivos guardados diretamente nesta pasta. */
  value: string;
  label: string;
  /** Aviso fixo exibido na pasta. */
  note?: string;
  children?: KbFolder[];
};

export const UNREADABLE_CATEGORY = "nao_selecionaveis";

export type Specialty = {
  value: string;
  label: string;
  /** Nomes das pastas Z.3 a Z.6 (documentos do próprio escritório), quando a área define. */
  areaFolders?: [string, string, string, string];
};

export const SPECIALTIES: Specialty[] = [
  {
    value: "civel",
    label: "Cível",
    areaFolders: [
      "Petições e contratos do cliente",
      "Decisões e sentenças do processo",
      "Documentos de prova",
      "Comunicações com a outra parte",
    ],
  },
  { value: "familia_sucessoes", label: "Família e Sucessões" },
  { value: "trabalhista", label: "Trabalhista" },
  { value: "previdenciario", label: "Previdenciário" },
  {
    value: "consumidor",
    label: "Consumidor",
    areaFolders: [
      "Contratos da empresa",
      "Políticas internas",
      "Termos de uso",
      "Comunicações com o consumidor",
    ],
  },
  { value: "tributario", label: "Tributário" },
  {
    value: "empresarial",
    label: "Empresarial",
    areaFolders: [
      "Contratos sociais e alterações",
      "Acordos de sócios",
      "Atas e deliberações societárias",
      "Contratos empresariais",
    ],
  },
  { value: "penal", label: "Penal" },
  { value: "imobiliario", label: "Imobiliário" },
  { value: "saude", label: "Saúde" },
  { value: "bancario_financeiro", label: "Bancário e Financeiro" },
  { value: "digital_protecao_dados", label: "Digital e Proteção de Dados" },
  { value: "administrativo_publico", label: "Administrativo e Público" },
  {
    value: "condominial",
    label: "Condominial",
    areaFolders: [
      "Convenção condominial",
      "Regimento interno",
      "Atas",
      "Comunicados, circulares e não oficiais",
    ],
  },
  {
    value: "contratos",
    label: "Contratos",
    areaFolders: [
      "Contratos assinados",
      "Aditivos contratuais",
      "Propostas comerciais aceitas",
      "Comunicações formais entre as partes",
    ],
  },
  { value: "ambiental", label: "Ambiental" },
];

export function specialtyLabel(specialty: string | null | undefined): string | null {
  return SPECIALTIES.find((s) => s.value === specialty)?.label ?? null;
}

/** A árvore de pastas de um agente, na ordem em que aparece no painel. */
export function kbTree(specialty?: string | null): KbFolder[] {
  const areaFolders = SPECIALTIES.find((s) => s.value === specialty)?.areaFolders ?? [];
  return [
    { value: "livros_digitais", label: "A) Livros digitais" },
    { value: "artigos_cientificos", label: "B) Artigos científicos" },
    { value: "processos_judiciais", label: "C) Processos judiciais" },
    { value: "materias_jornalisticas", label: "D) Matérias jornalísticas" },
    {
      value: "modelos",
      label: "E) Modelos",
      children: [
        { value: "modelos_contratos", label: "Modelos de contratos" },
        { value: "pecas_processuais", label: "Peças processuais" },
      ],
    },
    {
      value: UNREADABLE_CATEGORY,
      label: "F) Não selecionáveis",
      note: "O agente não lê os arquivos desta pasta.",
    },
    {
      value: "constituicao",
      label: "Z) Constituição",
      children: [
        { value: "legislacao_especifica", label: "1) Legislação específica" },
        { value: "decisoes_tribunais", label: "2) Decisões de tribunais" },
        ...areaFolders.map((label, index) => ({
          value: `area_${index + 3}`,
          label: `${index + 3}) ${label}`,
        })),
      ],
    },
  ];
}

/** Pastas em lista, para os seletores ("Z) Constituição › 1) Legislação específica"). */
export function kbCategoryOptions(specialty?: string | null): { value: string; label: string }[] {
  return kbTree(specialty).flatMap((folder) => [
    { value: folder.value, label: folder.label },
    ...(folder.children ?? []).map((child) => ({
      value: child.value,
      label: `${folder.label} › ${child.label}`,
    })),
  ]);
}

// Pastas comuns a qualquer agente (sem as da área) — importação do Drive.
export const KB_CATEGORIES = kbCategoryOptions();

export const UNCATEGORIZED_LABEL = "Sem categoria";

export function kbCategoryLabel(category: string | null, specialty?: string | null): string {
  if (!category) return UNCATEGORIZED_LABEL;
  return kbCategoryOptions(specialty).find((c) => c.value === category)?.label ?? category;
}
