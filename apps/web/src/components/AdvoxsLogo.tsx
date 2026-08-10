/**
 * Marca oficial da Advoxs (ícone em X + wordmark) — reconstruída como SVG a
 * partir do arquivo de logo (sem ativo de imagem original disponível no
 * repo). Todo o ícone herda a cor do texto via `currentColor`, exceto o
 * traço superior-direito, que é sempre a cor de destaque — reproduz a
 * variante de fundo escuro/claro sem precisar de dois arquivos.
 */
export function AdvoxsLogo({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-[0.32em] ${className}`}>
      <svg viewBox="0 0 40 40" aria-hidden className="h-[0.8em] w-[0.8em] flex-none">
        <line
          x1="9"
          y1="9"
          x2="16"
          y2="16"
          stroke="currentColor"
          strokeWidth="4.5"
          strokeLinecap="round"
        />
        <line
          x1="24"
          y1="24"
          x2="31"
          y2="31"
          stroke="currentColor"
          strokeWidth="4.5"
          strokeLinecap="round"
        />
        <line
          x1="9"
          y1="31"
          x2="16"
          y2="24"
          stroke="currentColor"
          strokeWidth="4.5"
          strokeLinecap="round"
        />
        <line
          x1="24"
          y1="16"
          x2="31"
          y2="9"
          className="text-auth-accent"
          stroke="currentColor"
          strokeWidth="4.5"
          strokeLinecap="round"
        />
      </svg>
      <span className="font-sans font-extrabold">Advoxs</span>
    </span>
  );
}
