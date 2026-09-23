"use client";

import { formatMessageTime, formatPhone } from "@/lib/format";
import type { Conversation } from "@/lib/types";

import { ConversationStatusIndicator } from "./ConversationStatusIndicator";

interface ConversationListProps {
  conversations: Conversation[];
  loaded: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  loadFailed?: boolean;
}

export function ConversationList({
  conversations,
  loaded,
  selectedId,
  onSelect,
  hasMore = false,
  loadingMore = false,
  onLoadMore,
  loadFailed = false,
}: ConversationListProps) {
  if (!loaded) {
    return <p className="px-5 py-6 text-sm text-muted">Carregando conversas…</p>;
  }

  if (loaded && loadFailed && conversations.length === 0) return null;

  if (loaded && conversations.length === 0) {
    return (
      <p className="px-5 py-6 text-sm leading-relaxed text-muted">
        Nenhuma conversa por aqui ainda. Quando um cliente escrever no WhatsApp
        do escritório, ela aparece nesta lista.
      </p>
    );
  }

  return (
    <ul className="flex-1 overflow-y-auto">
      {conversations.map((conversation) => {
        const isSelected = conversation.id === selectedId;
        const mediaType = conversation.last_message_media_type?.toLowerCase() ?? "";
        const direction =
          conversation.last_message_sender_type === "contact" ? "recebido" : "enviado";
        const mediaLabel =
          mediaType.startsWith("image/") || mediaType === "image"
            ? `Imagem ${direction === "recebido" ? "recebida" : "enviada"}`
            : mediaType.startsWith("audio/") || mediaType === "audio"
              ? `Áudio ${direction}`
              : mediaType.startsWith("video/") || mediaType === "video"
                ? `Vídeo ${direction === "recebido" ? "recebido" : "enviado"}`
                : mediaType === "document" ||
                    mediaType.includes("pdf") ||
                    mediaType.includes("word")
                  ? `Documento ${direction === "recebido" ? "recebido" : "enviado"}`
                  : "Anexo recebido";
        const preview = conversation.last_message_preview?.trim()
          || (conversation.last_message_media_type ? mediaLabel : "Sem mensagens ainda");
        return (
          <li key={conversation.id} className="border-b border-line">
            <button
              type="button"
              onClick={() => onSelect(conversation.id)}
              aria-current={isSelected ? "true" : undefined}
              className={`flex w-full flex-col gap-1 px-5 py-3.5 text-left transition-colors ${
                isSelected
                  ? "border-l-2 border-l-accent bg-surface"
                  : "border-l-2 border-l-transparent hover:bg-surface/60"
              }`}
            >
              <span className="flex items-baseline justify-between gap-2">
                <span className="truncate font-mono text-sm font-medium">
                  {conversation.is_test
                    ? "Conversa de teste"
                    : formatPhone(conversation.contact_phone_number)}
                </span>
                {conversation.last_message_at ? (
                  <time className="shrink-0 font-mono text-[11px] text-muted">
                    {formatMessageTime(conversation.last_message_at)}
                  </time>
                ) : null}
              </span>
              <span className="truncate text-xs text-muted" title={preview}>
                {preview}
              </span>
              <span className="flex items-center justify-between gap-2">
                <ConversationStatusIndicator conversation={conversation} />
              </span>
            </button>
          </li>
        );
      })}
      {hasMore && onLoadMore ? (
        <li className="p-4 text-center">
          <button
            type="button"
            onClick={onLoadMore}
            disabled={loadingMore}
            className="rounded-sm border border-line px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
          >
            {loadingMore ? "Carregando…" : "Carregar conversas anteriores"}
          </button>
        </li>
      ) : null}
    </ul>
  );
}
