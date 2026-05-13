"use client";
// Phase 10B.7 — public Contact Us form.
//
// Posts {name, email, message} to POST /contact on the Assembly
// backend. Validates client-side first (so we don't waste a network
// round-trip on obviously-bad submissions), surfaces a loading
// state, shows a success "we'll get back to you" toast on 2xx, and
// shows the backend's detail string on any 4xx / 5xx so the user
// can act on the actual problem ("Too many requests…", "Couldn't
// reach the email service…", etc.).
//
// A hidden honeypot field (`company`) catches naive bot submissions.

import { useState } from "react";
import { API_BASE, ApiError } from "@/lib/api";

interface FormState {
  name: string;
  email: string;
  message: string;
  company: string; // honeypot
}

const INITIAL: FormState = {
  name: "",
  email: "",
  message: "",
  company: "",
};

const EMAIL_RE = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;

interface FieldErrors {
  name?: string;
  email?: string;
  message?: string;
}

function validate(s: FormState): FieldErrors {
  const e: FieldErrors = {};
  if (!s.name.trim()) e.name = "Name is required.";
  if (!EMAIL_RE.test(s.email.trim())) e.email = "Enter a valid email address.";
  if (s.message.trim().length < 10)
    e.message = "Tell us a bit more — at least 10 characters.";
  return e;
}

export function ContactForm() {
  const [state, setState] = useState<FormState>(INITIAL);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  function update<K extends keyof FormState>(k: K, v: FormState[K]) {
    setState((s) => ({ ...s, [k]: v }));
  }

  async function onSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    if (submitting) return; // prevent duplicate rapid submissions
    setServerError(null);
    setSuccess(null);
    const found = validate(state);
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    setSubmitting(true);
    try {
      const res = await fetch(`${API_BASE}/contact`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: state.name.trim(),
          email: state.email.trim(),
          message: state.message.trim(),
          company: state.company.trim() || null,
        }),
      });
      let body: unknown = null;
      try {
        body = await res.json();
      } catch {
        body = null;
      }
      if (res.status >= 200 && res.status < 300) {
        const detail = (body as { detail?: string })?.detail
          ?? "Thanks — we'll get back to you soon.";
        setSuccess(detail);
        setState(INITIAL);
      } else {
        const detail =
          typeof (body as { detail?: unknown })?.detail === "string"
            ? (body as { detail: string }).detail
            : "Couldn't send your message. Please try again or email us directly.";
        throw new ApiError(res.status, detail, undefined);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Unknown error";
      setServerError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={onSubmit}
      data-testid="contact-form"
      className="space-y-5 rounded-xl border border-border bg-surface/80 p-7 shadow-[0_0_40px_rgba(0,0,0,0.4)]"
      noValidate
    >
      <Field label="Name" error={errors.name}>
        <input
          name="name"
          value={state.name}
          onChange={(e) => update("name", e.target.value)}
          placeholder="Alex Founder"
          className={inputCls}
          autoComplete="name"
          data-testid="contact-name"
        />
      </Field>

      <Field label="Email" error={errors.email}>
        <input
          name="email"
          type="email"
          value={state.email}
          onChange={(e) => update("email", e.target.value)}
          placeholder="alex@yourcompany.com"
          className={inputCls}
          autoComplete="email"
          data-testid="contact-email"
        />
      </Field>

      <Field label="Message" error={errors.message}>
        <textarea
          name="message"
          value={state.message}
          onChange={(e) => update("message", e.target.value)}
          rows={5}
          placeholder="Tell us what you're working on, what you'd like to explore, or how Assembly might fit your team."
          className={`${inputCls} resize-none`}
          data-testid="contact-message"
        />
      </Field>

      {/* Honeypot — kept off-screen, never tabbable. Naive bots
          auto-fill any input they see; real users never touch it. */}
      <label
        aria-hidden="true"
        className="pointer-events-none absolute -left-[10000px] top-auto h-0 w-0 overflow-hidden"
      >
        Company
        <input
          name="company"
          tabIndex={-1}
          autoComplete="off"
          value={state.company}
          onChange={(e) => update("company", e.target.value)}
        />
      </label>

      {serverError ? (
        <div
          role="alert"
          data-testid="contact-error"
          className="rounded-md border border-danger/40 bg-surface px-4 py-3 text-sm text-danger"
        >
          {serverError}
        </div>
      ) : null}

      {success ? (
        <div
          role="status"
          data-testid="contact-success"
          className="rounded-md border border-accent-border bg-accent-soft px-4 py-3 text-sm text-accent"
        >
          {success}
        </div>
      ) : null}

      <button
        type="submit"
        disabled={submitting}
        data-testid="contact-submit"
        className="inline-flex w-full items-center justify-center rounded-md bg-accent px-5 py-3 text-base font-semibold text-background transition-shadow hover:shadow-accent-glow disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
      >
        {submitting ? "Sending…" : "Send message"}
      </button>
    </form>
  );
}

const inputCls =
  "w-full rounded-md border border-border bg-surface-elevated px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-accent-border focus:outline-none";

function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1.5 text-sm text-text-body">
      <span className="font-medium text-text-primary">{label}</span>
      {children}
      {error ? (
        <span className="text-xs text-danger" role="alert">
          {error}
        </span>
      ) : null}
    </label>
  );
}
