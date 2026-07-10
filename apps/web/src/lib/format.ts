/** Formata um número E.164 brasileiro ("5511999998888") para leitura. */
export function formatPhone(raw: string): string {
  const digits = raw.replace(/\D/g, "");
  const br = digits.match(/^55(\d{2})(\d{4,5})(\d{4})$/);
  if (br) {
    return `+55 ${br[1]} ${br[2]}-${br[3]}`;
  }
  return raw;
}

/** Hora para mensagens de hoje; data + hora para as demais. */
export function formatMessageTime(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  const time = date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  if (sameDay) {
    return time;
  }
  const day = date.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  return `${day} ${time}`;
}

/** Data e hora completas — usado no carimbo "resumo gerado em". */
export function formatFullDateTime(iso: string): string {
  return new Date(iso).toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
