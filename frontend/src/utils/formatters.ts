export function formatDurationMs(ms: number): string {
  if (ms < 1) return '< 1ms';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

export function formatTimestamp(isoString: string): string {
  try {
    const d = new Date(isoString);
    const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const ms = String(d.getMilliseconds()).padStart(3, '0');
    return `${timeStr}.${ms}`;
  } catch {
    return isoString;
  }
}

export function getDepartmentLabel(span: string): string {
  switch (span) {
    case 'orchestrator':
      return 'Orchestrator Saga';
    case 'dept:zoning':
      return 'Zoning Department';
    case 'dept:fire':
      return 'Fire Department';
    case 'dept:water':
      return 'Water Department';
    case 'dept:structural':
      return 'Structural Department';
    case 'consensus':
      return 'Consensus Gate';
    default:
      return span.replace('dept:', '').toUpperCase();
  }
}

export function getStatusColor(status: string): { bg: string; text: string; border: string; barBg: string } {
  switch (status?.toUpperCase()) {
    case 'APPROVED':
      return {
        bg: 'bg-emerald-500/10',
        text: 'text-emerald-400',
        border: 'border-emerald-500/30',
        barBg: 'bg-emerald-500',
      };
    case 'REJECTED':
      return {
        bg: 'bg-amber-500/10',
        text: 'text-amber-400',
        border: 'border-amber-500/30',
        barBg: 'bg-amber-500',
      };
    case 'TIMEOUT':
      return {
        bg: 'bg-rose-500/10',
        text: 'text-rose-400',
        border: 'border-rose-500/30',
        barBg: 'bg-rose-500',
      };
    case 'ERROR':
      return {
        bg: 'bg-red-500/10',
        text: 'text-red-400',
        border: 'border-red-500/30',
        barBg: 'bg-red-500',
      };
    case 'PENDING':
    case 'RUNNING':
    case 'IN_PROGRESS':
    default:
      return {
        bg: 'bg-sky-500/10',
        text: 'text-sky-400',
        border: 'border-sky-500/30',
        barBg: 'bg-sky-500',
      };
  }
}
