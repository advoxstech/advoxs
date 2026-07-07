export type TenantStatus = "active" | "suspended";

export interface Tenant {
  id: string;
  name: string;
  cnpj: string | null;
  email_contato: string;
  credit_balance: number;
  status: TenantStatus;
  created_at: string;
  updated_at: string;
}

export type UserRole = "admin";

export interface User {
  id: string;
  tenant_id: string;
  name: string;
  email: string;
  role: UserRole;
  created_at: string;
}

export type WhatsappNumberStatus = "connected" | "disconnected";

export interface WhatsappNumber {
  id: string;
  tenant_id: string;
  phone_number_id: string;
  waba_id: string;
  display_phone_number: string;
  status: WhatsappNumberStatus;
  connected_at: string;
}

export type KnowledgeBaseFileStatus = "processing" | "ready" | "error";

export interface KnowledgeBaseFile {
  id: string;
  tenant_id: string;
  filename: string;
  size_bytes: number;
  mime_type: string;
  status: KnowledgeBaseFileStatus;
  error_message: string | null;
  uploaded_at: string;
}

export type ConversationState = "agent" | "human";

export interface Conversation {
  id: string;
  tenant_id: string;
  contact_phone_number: string;
  state: ConversationState;
  last_message_at: string;
  created_at: string;
}

export type MessageSenderType = "agent" | "human" | "contact";

export interface Message {
  id: string;
  conversation_id: string;
  tenant_id: string;
  sender_type: MessageSenderType;
  content: string;
  media_url: string | null;
  media_type: string | null;
  tokens_used: number | null;
  credits_consumed: number | null;
  created_at: string;
}

export interface CreditPackage {
  id: string;
  name: string;
  price_brl: number;
  credits_granted: number;
  active: boolean;
}

export type CreditTransactionType = "purchase" | "consumption" | "refund" | "bonus";

export interface CreditTransaction {
  id: string;
  tenant_id: string;
  type: CreditTransactionType;
  amount_credits: number;
  related_message_id: string | null;
  credit_package_id: string | null;
  stripe_payment_id: string | null;
  description: string;
  created_at: string;
}
