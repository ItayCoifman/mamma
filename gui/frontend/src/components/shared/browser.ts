/** Lightweight browser-family detection for UX hints.
 *
 * We only need to distinguish *Chromium-based* browsers (Chrome, Edge, Brave,
 * Opera — they share one GPU/WebCodecs video stack) from the rest, because the
 * embedded Rerun web viewer's H.264 camera backdrops can render black on some
 * Chromium + Linux GPU setups while Firefox and the native viewer are fine.
 */
export function isChromiumBased(): boolean {
  if (typeof navigator === 'undefined') return false;

  // Prefer UA Client Hints — present on Chromium, absent on Firefox/Safari,
  // and harder to spoof than the UA string.
  const uaData = (navigator as unknown as {
    userAgentData?: { brands?: Array<{ brand: string }> };
  }).userAgentData;
  if (uaData?.brands?.length) {
    return uaData.brands.some((b) => {
      const name = b.brand.toLowerCase();
      return name.includes('chromium') || name.includes('google chrome');
    });
  }

  // Fallback: Chromium UAs contain "Chrome"/"CriOS"; Firefox never does.
  const ua = navigator.userAgent || '';
  return /Chrome|Chromium|CriOS/.test(ua) && !/Firefox|FxiOS/.test(ua);
}
