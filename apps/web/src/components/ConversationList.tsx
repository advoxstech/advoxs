"use client";

import { formatMessageTime, formatPhone } from "@/lib/format";
import type { Conversation } from "@/lib/types";

interface ConversationListProps {
  conversations: Conversation[];
  loaded: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function ConversationList({
  conversations,
  loaded,
  selectedId,
  onSelect,
}: ConversationListProps) {
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
        const isManual = conversation.state === "human";
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
                  {formatPhone(conversation.contact_phone_number)}
                </span>
                {conversation.last_message_at ? (
                  <time className="shrink-0 font-mono text-[11px] text-muted">
                    {formatMessageTime(conversation.last_message_at)}
                  </time>
                ) : null}
              </span>
              <span
                className={`flex items-center gap-1.5 text-xs ${
                  isManual ? "text-brass" : "text-muted"
                }`}
              >
                <span
                  aria-hidden
                  className={`h-1.5 w-1.5 rounded-full ${
                    isManual ? "bg-brass" : "bg-accent"
                  }`}
                />
                {isManual ? "atendimento manual" : "agente respondendo"}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
