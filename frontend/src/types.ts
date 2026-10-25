/** Deliberate public API contracts. Money and timestamps stay strings at this boundary. */
export type Currency = "ILS" | "USD" | "EUR";
export type CheckoutStatus = "success" | "failed" | null;
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface RecordIdentity {
  id: string;
  created_at: string;
  updated_at: string;
}

export interface PaymentRecord extends RecordIdentity {
  first_name: string | null;
  last_name: string | null;
  provider_id: string | null;
  plan_id: string | null;
  checkout_status: CheckoutStatus;
  browser_state: string | null;
  error: string | null;
  debug_id: string | null;
  status: string;
  price_per_period: string;
  currency: string;
  period_unit: string | null;
  period_count: number | null;
  payment_cycles: number | null;
  is_subscription: boolean;
  client_name: string | null;
  agreement_number: string | null;
  comments: string;
  actor: string | null;
  environment: string | null;
  origin: string;
}

export interface Transaction extends RecordIdentity {
  provider_id: string;
  api_family: string;
  kind: string;
  status: string;
  provider_status: string;
  reservation_active: boolean;
  payment_id: string | null;
  subscription_id: string | null;
  original_id: string | null;
  client_name: string | null;
  agreement_number: string | null;
  amount: string;
  currency: string;
  fee: string | null;
  net: string | null;
  payer_name: string | null;
  payer_email: string | null;
  effective_at: string;
  origin: string;
  last_synced_at: string;
  can_refund: boolean;
  refundable_amount: string | null;
  refunded_amount: string | null;
  refund_deadline: string | null;
}

export interface Subscription extends RecordIdentity {
  provider_id: string;
  payment_id: string | null;
  client_name: string | null;
  agreement_number: string | null;
  status: string;
  amount: string | null;
  currency: string | null;
  payer_name: string | null;
  payer_email: string | null;
  next_billing_at: string | null;
  outstanding: string | null;
  metadata_json: Record<string, unknown>;
  origin: string;
  last_synced_at: string | null;
  checkout_status: CheckoutStatus;
  charged_payments: number;
  total_charged: string | null;
  remaining_payments: number | null;
  payments_unlimited: boolean;
}

export interface Coverage extends RecordIdentity {
  scope: string;
  last_success: string | null;
  checkpoint: Record<string, unknown>;
}

export interface Payment extends PaymentRecord {
  amount: string;
  collection_id: string | null;
  can_close: boolean;
  can_refund: boolean;
  effective_at: string;
  fee: string | null;
  net: string | null;
  payer_name: string | null;
  payer_email: string | null;
  last_synced_at: string | null;
  refundable_amount: string | null;
  refunded_amount: string | null;
  refund_deadline: string | null;
}

export interface PaymentDetail extends Payment {
  transactions: Page<Transaction>;
}

export interface SubscriptionDetail extends Subscription {
  transactions: Page<Transaction>;
}

export interface CheckoutTerms {
  first_name: string | null;
  last_name: string | null;
  client_name: string | null;
  agreement_number: string | null;
  price_per_period: string;
  currency: Currency;
  period_unit: string | null;
  period_count: number | null;
  payment_cycles: number | null;
  is_subscription: boolean;
  comments: string;
}

export interface CheckoutResult {
  id: string;
  provider_id: string | null;
  status: string;
  checkout_status: CheckoutStatus;
  browser_state: string | null;
  error: string | null;
  debug_id: string | null;
  terms: CheckoutTerms;
  proof: string | null;
  can_close: boolean;
}

export interface RefundResult extends Transaction {
  error: string | null;
}

export interface CancellationResult {
  id: string;
  status: "completed" | "failed";
  error: string | null;
}

export interface SyncResult {
  processed: number;
  excluded: number;
  next_page: number | null;
  coverage_note: string;
}

export interface CurrencyTotals {
  currency: Currency;
  collected: string;
  refunds: string;
  reversals: string;
  restored: string;
  net_before_fees: string;
  net_after_fees: string | null;
  known_fees: string;
  fees_complete: boolean;
  completed_count: number;
  pending_count: number;
  failed_count: number;
}

export interface TrendPoint {
  date: string;
  currency: Currency;
  kind: string;
  amount: string;
}

export interface Stats {
  currencies: CurrencyTotals[];
  trends: TrendPoint[];
  current_subscriptions: Record<string, number>;
  balance: BalanceSnapshot | null;
  coverage: Coverage[];
  scope: string;
  timezone: string;
  environment: string;
  filters: Record<string, unknown>;
}

export interface BalanceSnapshot {
  balances: { currency: Currency; total: string }[];
  as_of: string;
  last_synced_at: string;
}

export interface User {
  name: string;
  csrf: string;
  options: Options;
}
export interface Options {
  currencies: string[];
  period_limits: Record<string, number>;
  environment: string;
  paypal_configured: boolean;
  paypal_client_id: string;
  maximum_amount: string;
}
export interface PaymentTerms {
  first_name: string;
  last_name: string;
  agreement_number: string;
  price_per_period: string;
  currency: string;
  period_unit: string;
  period_count: number;
  is_subscription: boolean;
  payment_cycles: number | null;
  comments: string;
}

export type RecordRow = Payment | Subscription | Transaction;
export type WorkspaceRecord =
  Payment | Subscription | PaymentDetail | SubscriptionDetail;
export type ResourceView = "payments" | "subscriptions";

export interface SyncInput {
  scope:
    "subscriptions" | "history" | "subscription" | "payment" | "transaction";
  resource_id?: string;
  page?: number;
  start_date?: string;
  end_date?: string;
  timezone?: string;
}
