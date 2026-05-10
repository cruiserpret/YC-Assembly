"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";

import { ApiError, postSimulation } from "@/lib/api";
import { Brief, briefSchema } from "@/lib/schema";

const PRODUCT_TYPES = [
  { value: "ai_commerce_platform", label: "AI commerce platform" },
  { value: "b2b_saas", label: "B2B SaaS" },
  { value: "dev_tool", label: "Dev tool" },
  { value: "consumer_marketplace", label: "Consumer marketplace" },
  { value: "other", label: "Other" },
] as const;

const PRICING_MODELS = [
  { value: "subscription_monthly", label: "Subscription (monthly)" },
  { value: "subscription_annual", label: "Subscription (annual)" },
  { value: "one_time", label: "One-time" },
  { value: "usage_based", label: "Usage-based" },
  { value: "freemium", label: "Freemium" },
  { value: "performance_tier", label: "Performance tier" },
] as const;

export function IntakeForm() {
  const router = useRouter();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const {
    register,
    control,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<Brief>({
    resolver: zodResolver(briefSchema),
    defaultValues: {
      product_type: "ai_commerce_platform",
      product_name: "",
      description: "",
      price_structure: { model: "subscription_monthly", amount: "", notes: "" },
      target_society: { description: "", geography: "", income_level: "", known_segments: [] },
      competitors: [{ name: "", url: "", notes: "" }],
      product_url: "",
      additional_context: "",
      evidence_cutoff_date: "",
    },
  });

  const competitors = useFieldArray({ control, name: "competitors" });
  const segments = useFieldArray({ control, name: "target_society.known_segments" as never });

  const onSubmit = handleSubmit(async (raw) => {
    setSubmitError(null);
    try {
      // Strip empty optional strings — backend rejects empty strings on URL fields.
      const brief: Brief = {
        ...raw,
        product_url: raw.product_url || undefined,
        additional_context: raw.additional_context || undefined,
        evidence_cutoff_date: raw.evidence_cutoff_date || undefined,
        competitors: raw.competitors.map((c) => ({
          name: c.name,
          url: c.url || undefined,
          notes: c.notes || undefined,
        })),
      };
      const created = await postSimulation(brief);
      router.push(`/simulations/${created.id}/status`);
    } catch (e) {
      if (e instanceof ApiError) {
        setSubmitError(
          `The backend rejected the brief (${e.status}${e.kind ? ` — ${e.kind}` : ""}).`,
        );
      } else if (e instanceof Error) {
        setSubmitError(e.message);
      } else {
        setSubmitError("Submission failed. Please try again.");
      }
    }
  });

  return (
    <form onSubmit={onSubmit} className="space-y-8">
      {submitError && (
        <div role="alert" className="rounded border border-warn bg-warn-subtle p-3 text-sm text-warn">
          {submitError}
        </div>
      )}

      {/* Product */}
      <fieldset className="space-y-4">
        <legend className="font-serif text-lg">Product</legend>
        <Field label="Type" error={errors.product_type?.message}>
          <select className={inputClass} {...register("product_type")}>
            {PRODUCT_TYPES.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Name" error={errors.product_name?.message}>
          <input className={inputClass} {...register("product_name")} placeholder="Amboras" />
        </Field>
        <Field
          label="Description"
          hint="Explain what the product does, who it's for, and why it might be hard. ≥ 64 characters."
          error={errors.description?.message}
        >
          <textarea
            className={`${inputClass} min-h-[140px] font-serif`}
            rows={6}
            {...register("description")}
          />
        </Field>
        <Field label="Product URL (optional)" error={errors.product_url?.message}>
          <input className={inputClass} type="url" {...register("product_url")} placeholder="https://" />
        </Field>
      </fieldset>

      {/* Price */}
      <fieldset className="space-y-4">
        <legend className="font-serif text-lg">Price</legend>
        <Field label="Model" error={errors.price_structure?.model?.message}>
          <select className={inputClass} {...register("price_structure.model")}>
            {PRICING_MODELS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Amount (optional)" error={errors.price_structure?.amount?.message}>
          <input className={inputClass} {...register("price_structure.amount")} placeholder="$49/mo starter" />
        </Field>
        <Field label="Pricing notes (optional)" error={errors.price_structure?.notes?.message}>
          <input
            className={inputClass}
            {...register("price_structure.notes")}
            placeholder="performance tier later"
          />
        </Field>
      </fieldset>

      {/* Target society */}
      <fieldset className="space-y-4">
        <legend className="font-serif text-lg">Target society</legend>
        <Field
          label="Who is this for"
          hint="Who you imagine using this product. ≥ 16 characters."
          error={errors.target_society?.description?.message}
        >
          <textarea
            className={`${inputClass} min-h-[100px] font-serif`}
            rows={4}
            {...register("target_society.description")}
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Geography (optional)">
            <input className={inputClass} {...register("target_society.geography")} placeholder="US/Canada" />
          </Field>
          <Field label="Income level (optional)">
            <input className={inputClass} {...register("target_society.income_level")} />
          </Field>
        </div>
        <div>
          <label className="mb-2 block text-sm font-medium text-ink-800">
            Known segments (optional)
          </label>
          <div className="space-y-2">
            {segments.fields.map((field, i) => (
              <div key={field.id} className="flex gap-2">
                <input
                  className={inputClass}
                  {...register(`target_society.known_segments.${i}` as const)}
                  placeholder="mid-volume merchants"
                />
                <button
                  type="button"
                  onClick={() => segments.remove(i)}
                  className="rounded border border-ink-200 px-3 py-1 text-xs text-ink-600 hover:bg-ink-100"
                >
                  Remove
                </button>
              </div>
            ))}
            <button
              type="button"
              onClick={() => segments.append("")}
              className="rounded border border-ink-200 px-3 py-1 text-xs text-ink-600 hover:bg-ink-100"
            >
              Add segment
            </button>
          </div>
        </div>
      </fieldset>

      {/* Competitors */}
      <fieldset className="space-y-4">
        <legend className="font-serif text-lg">Competitors and current alternatives</legend>
        {errors.competitors?.message && (
          <p role="alert" className="text-sm text-warn">
            {errors.competitors.message as string}
          </p>
        )}
        <div className="space-y-3">
          {competitors.fields.map((field, i) => (
            <div key={field.id} className="space-y-2 rounded border border-ink-200 p-3">
              <Field label="Name" error={errors.competitors?.[i]?.name?.message}>
                <input
                  className={inputClass}
                  {...register(`competitors.${i}.name` as const)}
                  placeholder="Shopify Magic"
                />
              </Field>
              <Field label="URL (optional)" error={errors.competitors?.[i]?.url?.message}>
                <input
                  className={inputClass}
                  type="url"
                  {...register(`competitors.${i}.url` as const)}
                  placeholder="https://"
                />
              </Field>
              <Field label="Notes (optional)">
                <input
                  className={inputClass}
                  {...register(`competitors.${i}.notes` as const)}
                  placeholder="native to Shopify admin"
                />
              </Field>
              <button
                type="button"
                onClick={() => competitors.remove(i)}
                className="text-xs text-ink-400 hover:text-ink-800"
              >
                Remove this competitor
              </button>
            </div>
          ))}
        </div>
        <button
          type="button"
          onClick={() => competitors.append({ name: "", url: "", notes: "" })}
          className="rounded border border-ink-200 px-3 py-1 text-xs text-ink-600 hover:bg-ink-100"
        >
          Add competitor
        </button>
      </fieldset>

      {/* Optional extras */}
      <fieldset className="space-y-4">
        <legend className="font-serif text-lg">Optional</legend>
        <Field label="Additional context">
          <textarea
            className={`${inputClass} min-h-[80px] font-serif`}
            rows={3}
            {...register("additional_context")}
          />
        </Field>
        <Field
          label="Evidence cutoff date"
          hint="If set, the simulation will not embed or build edges from evidence captured after this date."
          error={errors.evidence_cutoff_date?.message}
        >
          <input className={inputClass} type="date" {...register("evidence_cutoff_date")} />
        </Field>
      </fieldset>

      <div className="flex items-center gap-4">
        <button
          type="submit"
          disabled={isSubmitting}
          className="inline-flex items-center rounded border border-ink-800 bg-ink-900 px-5 py-3 text-sm font-medium text-ink-50 hover:bg-ink-800 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isSubmitting ? "Submitting…" : "Run simulation"}
        </button>
        <p className="text-xs text-ink-400">
          Simulations typically take 25–45 minutes. You'll be redirected to the live status page.
        </p>
      </div>
    </form>
  );
}

const inputClass =
  "w-full rounded border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 focus:border-ink-800 focus:outline-none";

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <label className="block text-sm font-medium text-ink-800">{label}</label>
      {hint && <p className="text-xs text-ink-400">{hint}</p>}
      {children}
      {error && (
        <p role="alert" className="text-xs text-warn">
          {error}
        </p>
      )}
    </div>
  );
}
