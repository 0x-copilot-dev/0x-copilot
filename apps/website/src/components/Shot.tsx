/* A framed screenshot of the real app, plus its caption.
 *
 * Every image on this site is a real capture of the running product — no
 * mockups, no renders. The caption exists to say so and to name what you're
 * looking at, because "this is real" is the whole argument the page makes.
 */
export function Shot({
  src,
  alt,
  cap,
  note,
}: {
  readonly src: string;
  readonly alt: string;
  readonly cap: string;
  readonly note?: string;
}) {
  return (
    <figure style={{ margin: 0 }}>
      <div className="shot">
        <img src={src} alt={alt} loading="lazy" decoding="async" />
      </div>
      <figcaption className="shot__cap">
        <b>{cap}</b>
        {note ? <span>{note}</span> : null}
      </figcaption>
    </figure>
  );
}
