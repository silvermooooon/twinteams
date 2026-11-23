/** Display-only formatting for technical provider errors (DESIGN.md §6).
 *  Preserves the exact underlying error inside expandable technical details. */
export type HumanizedError = {
  summary: string;
  details: string | null;
};

export function humanizeTechnicalError(raw: string): HumanizedError {
  const trimmed = raw.trim();
  if (!trimmed) return { summary: '', details: null };

  const httpMatch = trimmed.match(/Provider\s+HTTP\s+(\d{3})/i);
  const hasJson = /\{[\s\S]*["']error["'][\s\S]*\}/.test(trimmed) || (trimmed.includes('{') && trimmed.includes('}'));
  const isLongTechnical = trimmed.length > 160 && (hasJson || /HTTP\s+\d{3}|traceback|exception|status\s+\d{3}/i.test(trimmed));

  if (httpMatch || hasJson || isLongTechnical) {
    const code = httpMatch ? Number(httpMatch[1]) : 0;
    if (code === 401 || code === 403 || /unsupported_country_region|forbidden|unauthorized|invalid_api_key/i.test(trimmed)) {
      return {
        summary: 'The model provider declined this request. Check OpenAI configuration or your provider account.',
        details: trimmed,
      };
    }
    if (code === 429 || /rate_limit|quota/i.test(trimmed)) {
      return {
        summary: 'The model provider rate limit or quota was reached. Try again later or check OpenAI configuration.',
        details: trimmed,
      };
    }
    if (code >= 500 || /timeout|timed out|connection/i.test(trimmed)) {
      return {
        summary: 'The model provider could not be reached right now. Try again shortly.',
        details: trimmed,
      };
    }
    return {
      summary: 'The model provider could not complete this request. Check OpenAI configuration for details.',
      details: trimmed,
    };
  }

  return { summary: trimmed, details: null };
}
