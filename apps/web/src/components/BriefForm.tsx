"use client";
// Phase 10B — founder-facing product brief form.
//
// Required: product_name, product_description, price_or_price_structure,
// launch_geography, target_customers (≥1), competitors_or_alternatives,
// launch_state.
// Optional: product_url, category_hint, optional_context, constraints,
// preferred_society_size, max_budget_usd.
//
// Phase 10B.5 — public-mode gating:
//   * In public mode (default), hide mode toggle, raw LLM-call counts,
//     and raw cost USD. Show only "Live simulation", "~24 personas",
//     and "Estimated time" instead.
//   * Local/dev (NEXT_PUBLIC_ASSEMBLY_PUBLIC_MODE=false) keeps the
//     legacy mode picker + cost estimate visible.
//   * Structured pricing fields (bundle / subscription / accessory)
//     live under Optional advanced fields and concatenate into the
//     single price_or_price_structure string at submit time so the
//     backend schema stays unchanged.
//
// Does NOT expose: raw DB ids, prompt internals, persona-forcing fields.

import { useMemo, useState } from "react";
import type { CreateRunResponse, FounderBriefIn, RunMode } from "@/lib/types";
import { createAssemblyRun } from "@/lib/api";
import { PUBLIC_MODE } from "@/lib/debug";

export interface BriefFormProps {
  defaultMode?: RunMode;
  onCreated?: (response: CreateRunResponse) => void;
}

interface FormState {
  product_name: string;
  product_description: string;
  price_or_price_structure: string;
  bundle_price: string;
  subscription_price: string;
  accessory_price: string;
  launch_geography: string;
  target_customers_text: string;
  competitors_text: string;
  launch_state: FounderBriefIn["launch_state"];
  product_url: string;
  category_hint: string;
  optional_context: string;
  constraints_text: string;
  preferred_society_size: string;
  max_budget_usd: string;
}

const INITIAL: FormState = {
  product_name: "",
  product_description: "",
  price_or_price_structure: "",
  bundle_price: "",
  subscription_price: "",
  accessory_price: "",
  launch_geography: "",
  target_customers_text: "",
  competitors_text: "",
  launch_state: "unlaunched",
  product_url: "",
  category_hint: "",
  optional_context: "",
  constraints_text: "",
  preferred_society_size: "",
  max_budget_usd: "",
};

function splitList(s: string): string[] {
  return s
    .split(/[,\n]+/)
    .map((x) => x.trim())
    .filter(Boolean);
}

interface FormErrors {
  [k: string]: string | undefined;
}

function validate(state: FormState): FormErrors {
  const e: FormErrors = {};
  if (!state.product_name.trim()) e.product_name = "Product name is required.";
  if (!state.product_description.trim() || state.product_description.length < 30)
    e.product_description = "Describe the product in at least 30 characters.";
  if (!state.price_or_price_structure.trim())
    e.price_or_price_structure = "Price or price structure is required.";
  if (!state.launch_geography.trim())
    e.launch_geography = "Launch geography is required.";
  if (splitList(state.target_customers_text).length < 1)
    e.target_customers_text =
      "List at least one target customer (comma- or newline-separated).";
  if (splitList(state.competitors_text).length < 1)
    e.competitors_text =
      "List at least one competitor or alternative (comma- or newline-separated).";
  if (state.preferred_society_size.trim()) {
    const n = Number(state.preferred_society_size);
    if (!Number.isInteger(n) || n < 21 || n > 30)
      e.preferred_society_size =
        "Society size must be an integer between 21 and 30 (or leave blank).";
  }
  if (state.max_budget_usd.trim()) {
    const n = Number(state.max_budget_usd);
    if (!(n >= 1) || n > 100)
      e.max_budget_usd = "Budget must be between $1 and $100.";
  }
  return e;
}

/**
 * Phase 10B.5 — fold the optional structured pricing fields back
 * into a single price_or_price_structure string so the backend
 * schema (which currently accepts only the flat field) sees a
 * clean multi-tier description. The backend's price-hierarchy
 * parser already understands "Optional subscription: $X/month"
 * and "Accessory: $X" lines.
 */
function buildPriceText(state: FormState): string {
  const parts: string[] = [state.price_or_price_structure.trim()];
  if (state.bundle_price.trim()) {
    parts.push(`Bundle: ${state.bundle_price.trim()}`);
  }
  if (state.subscription_price.trim()) {
    parts.push(`Optional subscription: ${state.subscription_price.trim()}`);
  }
  if (state.accessory_price.trim()) {
    parts.push(`Accessory: ${state.accessory_price.trim()}`);
  }
  return parts.filter(Boolean).join(". ");
}

function buildBrief(state: FormState): FounderBriefIn {
  const brief: FounderBriefIn = {
    product_name: state.product_name.trim(),
    product_description: state.product_description.trim(),
    price_or_price_structure: buildPriceText(state),
    launch_geography: state.launch_geography.trim(),
    target_customers: splitList(state.target_customers_text),
    competitors_or_alternatives: splitList(state.competitors_text),
    launch_state: state.launch_state,
    report_depth: "standard",
  };
  if (state.product_url.trim()) brief.product_url = state.product_url.trim();
  if (state.category_hint.trim())
    brief.category_hint = state.category_hint.trim();
  if (state.optional_context.trim())
    brief.optional_context = state.optional_context.trim();
  const constraints = splitList(state.constraints_text);
  if (constraints.length) brief.constraints = constraints;
  if (state.preferred_society_size.trim())
    brief.preferred_society_size = Number(state.preferred_society_size);
  if (state.max_budget_usd.trim())
    brief.max_budget_usd = Number(state.max_budget_usd);
  return brief;
}

export function BriefForm({
  defaultMode = "live_founder_brief",
  onCreated,
}: BriefFormProps) {
  const [state, setState] = useState<FormState>(INITIAL);
  const [mode, setMode] = useState<RunMode>(defaultMode);
  const [errors, setErrors] = useState<FormErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const estimate = useMemo(() => {
    const personas = state.preferred_society_size.trim()
      ? Number(state.preferred_society_size)
      : 24;
    const calls = personas * 7;
    const dollars = (calls * 0.018).toFixed(2);
    const minutes = Math.max(8, Math.round((calls * 6) / 60));
    return { calls, dollars, minutes, personas };
  }, [state.preferred_society_size]);

  function update<K extends keyof FormState>(k: K, v: FormState[K]) {
    setState((s) => ({ ...s, [k]: v }));
  }

  async function onSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    setSubmitError(null);
    const found = validate(state);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    setSubmitting(true);
    try {
      const brief = buildBrief(state);
      const resp = await createAssemblyRun({ mode, brief });
      onCreated?.(resp);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Unknown error";
      setSubmitError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={onSubmit}
      data-testid="brief-form"
      className="space-y-6 rounded-md border border-border bg-surface p-6"
    >
      <header className="space-y-2">
        <h2 className="text-2xl font-semibold text-text-primary">
          Submit a product brief
        </h2>
        <p className="text-sm text-text-body">
          Assembly dynamically builds a synthetic society from live evidence
          for this brief — you don&apos;t pick the personas or cohorts, the
          system finds them. The simulated society then reacts and debates,
          and we report who&apos;s receptive, who resists, why, and what to
          test next.
        </p>
      </header>

      {/* Mode toggle. Public mode: shown as a clean read-only "Live
          simulation" badge. Dev mode: legacy picker so we can still
          test fixture_demo locally. */}
      {PUBLIC_MODE ? (
        <div
          className="flex items-center gap-3 text-xs text-text-muted"
          data-testid="mode-public-display"
        >
          <span>Mode:</span>
          <span className="rounded border border-accent-border bg-accent-soft px-3 py-1 font-mono text-accent">
            Live simulation
          </span>
        </div>
      ) : (
        <div className="flex items-center gap-3 text-xs text-text-muted">
          <span>Mode:</span>
          <button
            type="button"
            onClick={() => setMode("live_founder_brief")}
            className={`rounded border px-3 py-1 ${mode === "live_founder_brief" ? "border-accent-border bg-accent-soft text-accent" : "border-border text-text-muted"}`}
            data-testid="mode-live"
          >
            live_founder_brief
          </button>
          <button
            type="button"
            onClick={() => setMode("fixture_demo")}
            className={`rounded border px-3 py-1 ${mode === "fixture_demo" ? "border-accent-border bg-accent-soft text-accent" : "border-border text-text-muted"}`}
            data-testid="mode-fixture"
          >
            fixture_demo (dev)
          </button>
        </div>
      )}

      <Field
        label="Product name"
        error={errors.product_name}
      >
        <input
          required
          name="product_name"
          value={state.product_name}
          onChange={(e) => update("product_name", e.target.value)}
          placeholder="e.g. AquaSnap"
          className={inputCls}
        />
      </Field>

      <Field
        label="Product description"
        helper="Describe what it is, who it&rsquo;s for, what it does, and what it does not do. Avoid marketing copy; include concrete behavior."
        error={errors.product_description}
      >
        <textarea
          required
          name="product_description"
          value={state.product_description}
          onChange={(e) => update("product_description", e.target.value)}
          rows={9}
          placeholder="What it is, who it's for, and what it does. Avoid marketing copy — describe behavior."
          className={`${inputCls} min-h-[12rem] resize-y leading-relaxed`}
        />
      </Field>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field
          label="Primary price"
          helper="Put the main product price here. Add subscriptions, bundles, or refills in the optional pricing fields."
          error={errors.price_or_price_structure}
        >
          <input
            required
            name="price_or_price_structure"
            value={state.price_or_price_structure}
            onChange={(e) => update("price_or_price_structure", e.target.value)}
            placeholder="$149 one-time for starter kit"
            className={inputCls}
          />
        </Field>
        <Field
          label="Launch geography"
          helper="Use a specific market if possible, e.g. Austin, Texas metro area."
          error={errors.launch_geography}
        >
          <input
            required
            name="launch_geography"
            value={state.launch_geography}
            onChange={(e) => update("launch_geography", e.target.value)}
            placeholder="Austin, Texas metro area"
            className={inputCls}
          />
        </Field>
      </div>

      <Field
        label="Target customers (comma- or newline-separated)"
        helper="Describe real customer groups, not everyone. Example: busy parents, renters, nurses, college students."
        error={errors.target_customers_text}
      >
        <textarea
          required
          name="target_customers"
          value={state.target_customers_text}
          onChange={(e) => update("target_customers_text", e.target.value)}
          rows={5}
          placeholder="busy parents, college students, urban renters"
          className={`${inputCls} min-h-[7rem] resize-y leading-relaxed`}
        />
      </Field>

      <Field
        label="Competitors or alternatives (comma- or newline-separated)"
        helper="List direct competitors, substitutes, or current alternatives the target audience already relies on today."
        error={errors.competitors_text}
      >
        <textarea
          required
          name="competitors_or_alternatives"
          value={state.competitors_text}
          onChange={(e) => update("competitors_text", e.target.value)}
          rows={5}
          placeholder="Hidrate Spark, AnyList, manual whiteboard"
          className={`${inputCls} min-h-[7rem] resize-y leading-relaxed`}
        />
      </Field>

      <Field label="Launch state">
        <select
          name="launch_state"
          value={state.launch_state}
          onChange={(e) =>
            update(
              "launch_state",
              e.target.value as FounderBriefIn["launch_state"],
            )
          }
          className={inputCls}
        >
          <option value="unlaunched">unlaunched</option>
          <option value="launched">launched</option>
        </select>
      </Field>

      <details className="rounded-md border border-border bg-surface-elevated p-4 transition-colors hover:border-border/80">
        <summary className="cursor-pointer text-sm font-medium text-text-primary">
          Optional advanced fields
        </summary>
        <div className="mt-4 space-y-4">
          {/* Phase 10B.5 — structured optional pricing. These fields
              fold into price_or_price_structure on submit so the
              backend's price-hierarchy parser can split them
              cleanly into primary / bundle / accessory. */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="Bundle price (optional)"
              helper="Multi-pack discount, if any. e.g. $139 for 2-pack."
            >
              <input
                name="bundle_price"
                value={state.bundle_price}
                onChange={(e) => update("bundle_price", e.target.value)}
                placeholder="$139 for 2-pack"
                className={inputCls}
                data-testid="bundle-price-input"
              />
            </Field>
            <Field
              label="Subscription price (optional)"
              helper="Recurring tier, if any. e.g. $7.99/month for Plus."
            >
              <input
                name="subscription_price"
                value={state.subscription_price}
                onChange={(e) => update("subscription_price", e.target.value)}
                placeholder="$7.99/month for Plus plan"
                className={inputCls}
                data-testid="subscription-price-input"
              />
            </Field>
          </div>
          <Field
            label="Accessory / refill price (optional)"
            helper="Consumable / replacement / add-on price, if any. e.g. $19.99 for 12 NFC tags."
          >
            <input
              name="accessory_price"
              value={state.accessory_price}
              onChange={(e) => update("accessory_price", e.target.value)}
              placeholder="$19.99 for 12 NFC tags"
              className={inputCls}
              data-testid="accessory-price-input"
            />
          </Field>
          <Field label="Product URL (optional)">
            <input
              name="product_url"
              value={state.product_url}
              onChange={(e) => update("product_url", e.target.value)}
              placeholder="https://yourproduct.example.com"
              className={inputCls}
            />
          </Field>
          <Field label="Category hint (optional)">
            <input
              name="category_hint"
              value={state.category_hint}
              onChange={(e) => update("category_hint", e.target.value)}
              placeholder="hydration accessory / cycling rear light / …"
              className={inputCls}
            />
          </Field>
          <Field
            label="Optional context (optional)"
            helper="Constraints, known concerns, prior pilots, or things the simulation should know."
          >
            <textarea
              name="optional_context"
              value={state.optional_context}
              onChange={(e) => update("optional_context", e.target.value)}
              rows={5}
              placeholder="e.g. constraints already known, prior pilots, privacy guarantees"
              className={`${inputCls} min-h-[7rem] resize-y leading-relaxed`}
            />
          </Field>
          <Field label="Constraints (optional, comma-separated)">
            <input
              name="constraints"
              value={state.constraints_text}
              onChange={(e) => update("constraints_text", e.target.value)}
              placeholder="no children's market, no Asia, no B2B"
              className={inputCls}
            />
          </Field>
          {/* Phase 10B.5+: society size is always visible (founders
              want to control it); max_budget_usd stays dev-only since
              it exposes the raw $-cap. */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="Target debate agents (21-30, optional)"
              helper="How many full LLM personas to target for the live debate. Defaults to 24. The final society may be slightly smaller if some persona candidates fail quality compression. Every simulation also includes a 100-voter influence overlay."
              error={errors.preferred_society_size}
            >
              <input
                name="preferred_society_size"
                value={state.preferred_society_size}
                onChange={(e) =>
                  update("preferred_society_size", e.target.value)
                }
                placeholder="24"
                inputMode="numeric"
                className={inputCls}
                data-testid="society-size-input"
              />
              <p
                data-testid="voter-overlay-note"
                className="mt-2 text-[11px] leading-relaxed text-text-muted"
              >
                100 voters always run after the debate to estimate how
                the arguments spread through a larger audience sample.
              </p>
            </Field>
            {!PUBLIC_MODE ? (
              <Field
                label="Max budget USD (optional)"
                error={errors.max_budget_usd}
              >
                <input
                  name="max_budget_usd"
                  value={state.max_budget_usd}
                  onChange={(e) => update("max_budget_usd", e.target.value)}
                  placeholder="12.00"
                  inputMode="decimal"
                  className={inputCls}
                />
              </Field>
            ) : null}
          </div>
        </div>
      </details>

      {/* Phase 10B.5 — public mode hides raw LLM-call counts and
          $-cost. Founder-friendly summary only. */}
      <div
        className="flex items-center justify-between rounded-md border border-border bg-surface-elevated px-4 py-3 text-sm text-text-body"
        data-testid="run-estimate"
      >
        {PUBLIC_MODE ? (
          <span>
            Synthetic society:{" "}
            <span className="font-mono text-accent">
              {estimate.personas} personas
            </span>{" "}
            · Estimated time:{" "}
            <span className="font-mono text-accent">
              ~12&ndash;20 minutes
            </span>
          </span>
        ) : (
          <span>
            Estimated run:{" "}
            <span className="font-mono text-accent">
              {estimate.personas} personas
            </span>{" "}
            · ~{estimate.calls} LLM calls · ~${estimate.dollars} ·{" "}
            <span className="font-mono">~{estimate.minutes} min</span> wall time
          </span>
        )}
      </div>

      {submitError ? (
        <div
          role="alert"
          className="rounded-md border border-danger/40 bg-surface px-4 py-3 text-sm text-danger"
        >
          {submitError}
        </div>
      ) : null}

      <button
        type="submit"
        disabled={submitting}
        data-testid="brief-submit"
        className="inline-flex w-full items-center justify-center rounded-md bg-accent px-5 py-3 text-base font-semibold text-background transition-shadow hover:shadow-accent-glow disabled:opacity-60 disabled:cursor-not-allowed sm:w-auto"
      >
        {submitting ? "Submitting…" : "Run simulation"}
      </button>
    </form>
  );
}

const inputCls =
  "w-full rounded-md border border-border bg-surface-elevated px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-accent-border focus:outline-none";

function Field({
  label,
  helper,
  error,
  children,
}: {
  label: string;
  helper?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1.5 text-sm text-text-body">
      <span className="font-medium text-text-primary">{label}</span>
      {helper ? (
        <span
          className="block text-xs font-normal text-text-muted"
          dangerouslySetInnerHTML={{ __html: helper }}
        />
      ) : null}
      {children}
      {error ? (
        <span className="text-xs text-danger" role="alert">
          {error}
        </span>
      ) : null}
    </label>
  );
}
