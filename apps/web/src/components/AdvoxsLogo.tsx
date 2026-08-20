/**
 * Ícone em X da Advoxs, sozinho — sem o wordmark (ver AdvoxsLogo abaixo pra
 * versão com o nome ao lado). Herda a cor do texto via `currentColor`,
 * exceto o traço superior-direito, que é sempre a cor de destaque —
 * reproduz a variante de fundo escuro/claro sem precisar de dois arquivos.
 * Decorativo por padrão (`aria-hidden`); quem usa sozinho como identidade
 * visual (ex: monograma da barra lateral) deve envolver num elemento com
 * `aria-label="Advoxs"`.
 */
export function AdvoxsIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" aria-hidden className={className}>
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
  );
}

/**
 * Marca oficial da Advoxs (ícone em X + wordmark) — reconstruída como SVG a
 * partir do arquivo de logo (sem ativo de imagem original disponível no
 * repo).
 */
export function AdvoxsLogo({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-[0.32em] ${className}`}>
      <AdvoxsIcon className="h-[0.8em] w-[0.8em] flex-none" />
      <span className="font-sans font-extrabold">Advoxs</span>
    </span>
  );
}
