// Negative: bans navigator.clipboard.write*.
export async function violation(): Promise<void> {
  // eslint-disable-next-line no-restricted-globals -- isolates the test
  // to the member-expression rule for navigator.clipboard.write*.
  await navigator.clipboard.writeText("anything");
}
