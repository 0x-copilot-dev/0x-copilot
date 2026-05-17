// Negative: bans the document.cookie member-expression even if `document`
// were somehow allowed (defense in depth via no-restricted-syntax).
declare const safe: { cookie: string };
export function violation(): string {
  // eslint-disable-next-line no-restricted-globals -- we want only the
  // member-expression rule to fire here; this isolates the test to that
  // single ban so a regression there is easy to spot.
  return document.cookie || safe.cookie;
}
