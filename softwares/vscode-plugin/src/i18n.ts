// Shared catalogue has no VS Code dependency, so parsers remain usable in tests.
const i18n: {
  t(message: string, ...values: unknown[]): string;
  setLanguage(value: unknown): void;
  getLanguage(): 'en' | 'zh-CN';
} = require('../media/i18n.js');
export const { t, setLanguage, getLanguage } = i18n;
