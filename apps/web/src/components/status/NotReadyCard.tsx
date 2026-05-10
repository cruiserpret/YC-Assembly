export function NotReadyCard({
  currentStatus,
  guidance,
}: {
  currentStatus: string;
  guidance: string;
}) {
  return (
    <div className="space-y-3 rounded border border-ink-200 bg-ink-50 p-4 text-sm">
      <p className="font-medium text-ink-900">The report is not ready yet.</p>
      <p className="text-ink-600">
        Current status: <span className="font-mono text-xs">{currentStatus}</span>
      </p>
      <p className="text-ink-600">{guidance}</p>
    </div>
  );
}
