export type ConversationState = "agent" | "human" | "billing_gate";
export type ConversationStatus =
  | "agent"
  | "human"
  | "billing_gate"
  | "processing"
  | "failed";

export interface Conversation {
  id: string;
  contact_phone_number: string;
  contact_id?: string | null;
  contact_name?: string | null;
  human_only?: boolean;
  tags?: ContactTag[];
  state: ConversationState;
  status?: ConversationStatus;
  is_test: boolean;
  last_message_at: string | null;
  last_message_preview?: string | null;
  last_message_media_type?: string | null;
  last_message_sender_type?: SenderType | "system" | null;
  created_at: string;
  summary: string | null;
  summary_generated_at: string | null;
  end_customer_balance?: number | null;
  end_customer_cycle_total?: number | null;
  end_customer_cycle_consumed?: number | null;
  end_customer_billing_exempt: boolean;
  end_customer_billing_enabled: boolean;
  current_agent_name: string | null;
  assigned_user_id?: string | null;
  assigned_user_name?: string | null;
  urgent_since?: string | null;
  urgent_reason?: string | null;
  urgent_source?: "agent" | "keyword" | "manual" | null;
  urgent_agent_name?: string | null;
}

export interface ContactTag { id: string; name: string; color: string }
export interface OfficeContact {
  id: string; name: string; phone_number: string; human_only: boolean; tags: ContactTag[];
}

export interface UrgencyKeyword {
  id: string;
  keyword: string;
  created_at: string;
}

// "system": avisos automáticos sem IA (confirmação de pagamento, leitura de documento grande).
export type SenderType = "agent" | "human" | "contact" | "system";

export interface ResponseSource {
  document_id: string;
  filename: string;
  chunk_id: string;
  excerpt: string;
  page: number | null;
  page_end?: number | null;
  partial_pages?: number[];
}

export interface ResponseEvidence {
  status:
    | "referenced"
    | "no_documents"
    | "not_searched"
    | "empty"
    | "not_used"
    | "unavailable";
  sources: ResponseSource[];
  search_failed: boolean;
}

export interface MessageMedia {
  kind: "audio" | "document" | "image";
  status: "pending" | "processing" | "done" | "failed" | "skipped";
  error_code: string | null;
  text: string | null;
}

export interface Message {
  id: string;
  sender_type: SenderType;
  sender_user_name?: string | null;
  sender_user_id?: string | null;
  response_sources?: ResponseEvidence | null;
  content: string;
  media_url: string | null;
  media_type: string | null;
  media?: MessageMedia | null;
  delivery_status: "pending" | "sent" | "failed" | "cancelled" | null;
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
  openai_cost_estimate_usd: number;
  low_balance_tenants: { id: string; name: string; credit_balance: number }[];
  whatsapp_connected: { connected: number; total: number };
  knowledge_base_usage: { total_files: number; total_size_bytes: number };
  pending_zapi_requests: {
    tenant_id: string;
    tenant_name: string;
    requested_at: string;
  }[];
}

export interface TenantDashboard {
  credit_balance: number;
  whatsapp: { connected: boolean; display_phone_number: string | null };
  conversations: { total: number; waiting_human: number; urgent?: number };
  usage_last_30_days: { agent_messages: number; credits_consumed: number };
  knowledge_base: { ready: number; error: number };
  end_customer_earnings: { connected: boolean; total_brl: number | null };
  recent_conversations: {
    id: string;
    contact_phone_number: string;
    state: "agent" | "human";
    last_message_at: string | null;
  }[];
}

export interface Profile {
  tenant_name: string;
  email_contato: string;
  has_logo: boolean;
  has_letterhead: boolean;
  user_name: string;
  user_email: string;
}

export interface Agent {
  id: string;
  name: string;
  instructions: string;
  is_entry_point: boolean;
  created_at: string;
  updated_at: string;
}

export interface FollowUpTemplate {
  name: string;
  language: string;
  status: string;
  category: string;
  parameter_format: string;
  header_text: string | null;
  body_text: string;
  footer_text: string | null;
  param_names: string[];
  supported: boolean;
  unsupported_reason: string | null;
  rejected_reason: string | null;
}

export interface FollowUpTemplates {
  provider: "meta" | "zapi" | "omni";
  templates: FollowUpTemplate[];
}

export interface FollowUpBatch {
  id: string;
  created_at: string;
  created_by_name: string;
  provider: "meta" | "zapi" | "omni";
  template_name: string | null;
  text: string | null;
  total: number;
  sent: number;
  failed: number;
  skipped: number;
  pending: number;
}

export interface FollowUpRecipient {
  id: string;
  phone: string;
  contact_name: string;
  status: "pending" | "processing" | "sent" | "failed" | "skipped";
  error: string | null;
  sent_at: string | null;
  conversation_id: string | null;
}

export interface FollowUpBatchDetail extends FollowUpBatch {
  recipients: FollowUpRecipient[];
}
