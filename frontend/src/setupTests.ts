import '@testing-library/jest-dom';
import { vi } from 'vitest';

// Default mock for fetch
globalThis.fetch = vi.fn().mockImplementation((url: string) => {
  if (url.includes('/health')) {
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ status: 'healthy' }),
    } as Response);
  }
  if (url.includes('/admin/fault')) {
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({
        zoning: { department: 'zoning', mode: 'none', delay_ms: 0, message: 'Zoning operating normally' },
        fire: { department: 'fire', mode: 'none', delay_ms: 0, message: 'Fire operating normally' },
        water: { department: 'water', mode: 'none', delay_ms: 0, message: 'Water operating normally' },
        structural: { department: 'structural', mode: 'none', delay_ms: 0, message: 'Structural operating normally' },
      }),
    } as Response);
  }
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve({}),
  } as Response);
});

// Default mock for EventSource
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  listeners: Record<string, ((e: any) => void)[]> = {};
  readyState: number = 0;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(event: string, cb: (e: any) => void) {
    if (!this.listeners[event]) this.listeners[event] = [];
    this.listeners[event].push(cb);
  }

  removeEventListener(event: string, cb: (e: any) => void) {
    if (this.listeners[event]) {
      this.listeners[event] = this.listeners[event].filter((fn) => fn !== cb);
    }
  }

  emit(event: string, data: any) {
    if (this.listeners[event]) {
      this.listeners[event].forEach((cb) => cb({ data: typeof data === 'string' ? data : JSON.stringify(data) }));
    }
  }

  close() {
    this.readyState = 2;
  }
}

(globalThis as any).EventSource = MockEventSource;
