export type ConversationState = "agent" | "human";

export interface Conversation {
  id: string;
  contact_phone_number: string;
  state: ConversationState;
  last_message_at: string | null;
  created_at: string;
}

export type SenderType = "agent" | "human" | "contact";

export interface Message {
  id: string;
  sender_type: SenderType;
  content: string;
  media_url: string | null;
  media_type: string | null;
  created_at: string;
}
