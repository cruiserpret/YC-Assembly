import { IntakeForm } from "@/components/intake/IntakeForm";

export default function NewSimulationPage() {
  return (
    <section className="space-y-6">
      <div>
        <h1 className="font-serif text-3xl tracking-tight">New simulation</h1>
        <p className="mt-2 text-sm text-ink-600">
          Describe the product, the people you imagine using it, and the named alternatives they
          already reach for. The simulator runs end-to-end without further input.
        </p>
      </div>
      <IntakeForm />
    </section>
  );
}
