/* The hero: a real recorded session, played at 2x.
 *
 * The recording is genuine — a real prompt, a real run against a real provider
 * key, a real streamed answer. It is filmed at 1x and played back at 2x here
 * (`playbackRate`), which is why the typing reads at a natural speed.
 *
 * This is the one interactive island on the site; everything else is static
 * HTML. It hydrates on load only because playbackRate cannot be set from
 * markup — there is no attribute for it.
 */
import { useEffect, useRef } from "react";

export function Demo({ rate = 2 }: { readonly rate?: number }) {
  const ref = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    v.playbackRate = rate;
    // Skip the browser's blank first frame before the app paints.
    const onMeta = () => {
      if (v.currentTime < 0.4) v.currentTime = 0.4;
    };
    v.addEventListener("loadedmetadata", onMeta);
    return () => v.removeEventListener("loadedmetadata", onMeta);
  }, [rate]);

  return (
    <figure style={{ margin: 0 }}>
      <div className="shot">
        <video
          ref={ref}
          src="/media/demo.webm"
          poster="/media/poster.png"
          autoPlay
          muted
          loop
          playsInline
          preload="metadata"
          aria-label="A recorded 0xCopilot session: a prompt is typed, the run streams its answer, and the work lands on the Todos and Projects surfaces."
        />
      </div>
      <figcaption className="shot__cap">
        <b>Recorded from the running app.</b>
        <span>Real prompt, real run, real answer. Played at {rate}×.</span>
      </figcaption>
    </figure>
  );
}
