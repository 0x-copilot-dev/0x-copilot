/* The hero: a real recorded session, played at 2x.
 *
 * Genuine throughout — a real prompt, a real run against a real provider key, a
 * real streamed answer. Filmed at 1x and played back at 2x here, which is why
 * the typing reads at a natural speed.
 *
 * Two sources: H.264 mp4 first (universal, incl. Safari/iOS), VP9 webm as a
 * lighter fallback for Chromium/Firefox. This is the site's one hydrated
 * island — only because playbackRate has no HTML attribute.
 */
import { useEffect, useRef } from "react";

export function Demo({ rate = 2 }: { readonly rate?: number }) {
  const ref = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    v.playbackRate = rate;
  }, [rate]);

  return (
    <figure style={{ margin: 0 }}>
      <div className="shot shot--video">
        <video
          ref={ref}
          poster="./media/poster.png"
          autoPlay
          muted
          loop
          playsInline
          preload="metadata"
          aria-label="A recorded 0xCopilot session: a prompt is typed, the run streams its answer, and the work lands on the Todos and Projects surfaces."
        >
          <source src="./media/demo.mp4" type="video/mp4" />
          <source src="./media/demo.webm" type="video/webm" />
        </video>
      </div>
      <figcaption className="shot__cap">
        <b>Recorded from the running app.</b>
        <span>Real prompt, real run, real answer. Played at {rate}×.</span>
      </figcaption>
    </figure>
  );
}
