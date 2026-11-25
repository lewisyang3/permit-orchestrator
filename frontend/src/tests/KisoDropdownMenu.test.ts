import { describe, it, expect } from 'vitest';
import { KISO_TAB_CONFIG } from '../components/KisoDropdownMenu';

describe('KISO dropdown tabs', () => {
  it('only offers tabs the workbench renders', () => {
    expect(KISO_TAB_CONFIG.map((t) => t.id)).toEqual(['review', 'trace']);
  });
});
