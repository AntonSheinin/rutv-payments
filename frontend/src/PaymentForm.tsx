import { CreditCard, ShieldCheck, Wallet } from "lucide-react";
import { money } from "./api";
import type { CheckoutResult, Options } from "./types";
import { CloseCheckout } from "./CloseCheckout";
import { SubscriptionCancellationNotice } from "./ui";
import { useCheckout } from "./useCheckout";

export function CreatePayment({
  options,
  requestPending,
  onAbandonError,
  onSaved,
}: {
  options: Options;
  requestPending: boolean;
  onAbandonError: (message: string) => void;
  onSaved: (item: CheckoutResult) => void;
}) {
  const {
    form,
    setForm,
    unlimited,
    setUnlimited,
    busy,
    error,
    ready,
    checkoutPayment,
    formRef,
    cardContainer,
    cardRef,
    startCheckout,
    closed,
  } = useCheckout(options, onSaved, onAbandonError);

  return (
    <div className="create-grid">
      <form
        className="panel create-form"
        ref={formRef}
        onSubmit={(e) => {
          e.preventDefault();
          if (form.is_subscription) void startCheckout();
          else cardRef.current?.click();
        }}
      >
        <div className="panel-title">
          <h2>Payment details</h2>
          <CreditCard size={18} />
        </div>
        <fieldset className="form-body" disabled={busy || requestPending}>
          <div className="form-row">
            <label>
              First name
              <input
                value={form.first_name}
                onChange={(e) =>
                  setForm({ ...form, first_name: e.target.value })
                }
                autoComplete="given-name"
                maxLength={140}
                required
              />
            </label>
            <label>
              Last name
              <input
                value={form.last_name}
                onChange={(e) =>
                  setForm({ ...form, last_name: e.target.value })
                }
                autoComplete="family-name"
                maxLength={140}
                required
              />
            </label>
          </div>
          <label>
            Agreement number
            <input
              value={form.agreement_number}
              onChange={(e) =>
                setForm({ ...form, agreement_number: e.target.value })
              }
              maxLength={255}
              required
            />
          </label>
          <div className="form-row">
            <label>
              {form.is_subscription
                ? "Amount per billing interval"
                : "Payment amount"}
              <input
                inputMode="decimal"
                type="number"
                min="0.01"
                step="0.01"
                max={options.maximum_amount}
                value={form.price_per_period}
                onChange={(e) =>
                  setForm({ ...form, price_per_period: e.target.value })
                }
                placeholder="0.00"
                required
              />
            </label>
            <label>
              Currency
              <select
                value={form.currency}
                onChange={(e) => setForm({ ...form, currency: e.target.value })}
              >
                {options.currencies.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="form-row">
            <label>
              Every
              <input
                type="number"
                min="1"
                max={
                  form.is_subscription
                    ? options.period_limits[form.period_unit]
                    : 36500
                }
                value={form.period_count}
                onChange={(e) =>
                  setForm({ ...form, period_count: Number(e.target.value) })
                }
                required
              />
            </label>
            <label>
              Payment period
              <select
                value={form.period_unit}
                onChange={(e) =>
                  setForm({
                    ...form,
                    period_unit: e.target.value,
                    period_count: 1,
                  })
                }
              >
                {["DAY", "WEEK", "MONTH", "YEAR"].map((u) => (
                  <option key={u} value={u}>
                    {u.toLowerCase()}(s)
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="subscription-toggle">
            <div>
              <strong>Subscription</strong>
              <small>Automatically bill at the selected interval</small>
            </div>
            <input
              aria-label="Is subscription"
              type="checkbox"
              role="switch"
              checked={form.is_subscription}
              onChange={(e) => {
                setForm({
                  ...form,
                  is_subscription: e.target.checked,
                  payment_cycles: 1,
                });
                setUnlimited(false);
              }}
            />
          </label>
          {form.is_subscription && (
            <div className="cycles">
              <label>
                Payment cycles
                <input
                  type="number"
                  min="1"
                  max="999"
                  disabled={unlimited}
                  value={form.payment_cycles ?? 1}
                  onChange={(e) =>
                    setForm({ ...form, payment_cycles: Number(e.target.value) })
                  }
                  required={!unlimited}
                />
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={unlimited}
                  onChange={(e) => setUnlimited(e.target.checked)}
                />
                Continue until canceled
              </label>
              <SubscriptionCancellationNotice />
            </div>
          )}
          <label>
            Comments <span className="muted">(internal only)</span>
            <textarea
              rows={4}
              value={form.comments}
              onChange={(e) => setForm({ ...form, comments: e.target.value })}
              maxLength={4000}
              placeholder="Add context for your teamâ€¦"
            />
          </label>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {form.is_subscription ? (
            <button
              className="primary"
              disabled={busy || requestPending || !ready}
            >
              Submit
            </button>
          ) : (
            <div className="checkout-submit">
              <strong>Submit payment</strong>
              <div ref={cardContainer} aria-busy={busy} />
              <small>
                {ready
                  ? "Submit opens PayPal's secure card form."
                  : "Preparing PayPal checkout?"}
              </small>
            </div>
          )}
        </fieldset>
        {checkoutPayment?.can_close && (
          <div className="checkout-controls">
            <CloseCheckout paymentId={checkoutPayment.id} updated={closed} />
          </div>
        )}
      </form>
      <aside className="panel payment-preview">
        <span className="eyebrow">PAYMENT SUMMARY</span>
        <div className="preview-icon">
          <Wallet size={27} />
        </div>
        <h2>{money(form.price_per_period || "0", form.currency)}</h2>
        <p>
          {form.is_subscription
            ? `Every ${form.period_count} ${form.period_unit.toLowerCase()}(s)`
            : `One-time payment for ${form.period_count} ${form.period_unit.toLowerCase()}(s)`}
        </p>
        <hr />
        <dl>
          <dt>Payment type</dt>
          <dd>{form.is_subscription ? "Subscription" : "One-time"}</dd>
          <dt>Duration</dt>
          <dd>
            {form.is_subscription
              ? unlimited
                ? "Until canceled"
                : `${form.payment_cycles} billing cycle(s)`
              : "One charge"}
          </dd>
          <dt>Client name</dt>
          <dd>{`${form.first_name} ${form.last_name}`.trim() || "â€”"}</dd>
        </dl>
        <div className="safe-note">
          <ShieldCheck size={19} />
          <p>
            Checkout happens on PayPal. Card details never pass through this
            service.
          </p>
        </div>
        <small>Review the terms above. Submit opens PayPal directly.</small>
      </aside>
    </div>
  );
}
