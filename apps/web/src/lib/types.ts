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

export interface CreditPackage {
  id: string;
  name: string;
  price_brl: number;
  credits_granted: number;
}

export interface AdminDashboard {
  tenants_total: number;
  tenants_by_status: { active: number; suspended: number };
  new_tenants_last_30_days: { day: string; count: number }[];
  revenue_brl_last_30_days: number;
  credits_summary: { sold: number; consumed: number };
  messages_processed: number;
  agent_executions: number;
  tokens_consumed: number;
  low_balance_tenants: { id: string; name: string; credit_balance: number }[];
  whatsapp_connected: { connected: number; total: number };
  knowledge_base_usage: { total_files: number; total_size_bytes: number };
}
