// Motion layer: Lenis smooth scroll driven by GSAP's ticker, ScrollTrigger reveals,
// and React Bits islands (SplitText hero, CountUp facts, SpotlightCard bento).
// Only runs when <html> has .motion (set in <head> unless the user prefers reduced motion).
import "lenis/dist/lenis.css";
import Lenis from "lenis";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { SplitText } from "gsap/SplitText";
import { createRoot } from "react-dom/client";
import SplitTextRB from "./components/SplitText.jsx";
import CountUp from "./components/CountUp.jsx";
import SpotlightCard from "./components/SpotlightCard.jsx";
import "./site.js";

const root = document.documentElement;
window.__motionReady = true;

// Spotlight is a hover effect, so it's harmless under reduced motion (the component damps itself).
document.querySelectorAll(".cell").forEach((cell) => {
  const fx = cell.appendChild(document.createElement("div"));
  fx.className = "fx-root";
  fx.setAttribute("aria-hidden", "true");
  createRoot(fx).render(
    <SpotlightCard className="cell-fx" spotlightColor="#30d158" intensity={0.14} spotlightSize={320} borderGlow={0.9}
      style={{ "--spotlight-card-surface": "transparent", "--spotlight-card-border": "transparent", "--spotlight-card-shadow": "none" }} />
  );
});

if (root.classList.contains("motion")) {
  gsap.registerPlugin(ScrollTrigger, SplitText);

  const lenis = new Lenis({ autoRaf: false, anchors: true }) // anchors honour scroll-padding-top;
  lenis.on("scroll", ScrollTrigger.update);
  gsap.ticker.add((t) => lenis.raf(t * 1000));
  gsap.ticker.lagSmoothing(0);

  // Hero: headline words rise in (React Bits SplitText), then the rest follows.
  const h1 = document.querySelector(".hero h1");
  createRoot(h1).render(
    <SplitTextRB
      tag="span"
      text={h1.textContent}
      splitType="words"
      delay={70}
      duration={0.9}
      ease="power3.out"
      from={{ opacity: 0, y: 28 }}
      to={{ opacity: 1, y: 0 }}
      threshold={0}
      rootMargin="0px"
      textAlign="left"
    />
  );
  gsap.to(".hero-in", { opacity: 1, y: 0, duration: 0.9, ease: "power3.out", stagger: 0.08, delay: 0.35 });

  // Section headings: lines slide up out of a mask, once.
  document.querySelectorAll("h2.reveal").forEach((h2) => {
    h2.classList.remove("reveal");
    SplitText.create(h2, {
      type: "lines",
      mask: "lines",
      autoSplit: true,
      onSplit: (self) =>
        gsap.from(self.lines, {
          yPercent: 105,
          duration: 0.9,
          ease: "expo.out",
          stagger: 0.08,
          scrollTrigger: { trigger: h2, start: "top 88%", once: true },
        }),
    });
  });

  // Everything else: batched fade-up, staggered within each batch.
  ScrollTrigger.batch(".reveal", {
    start: "top 90%",
    once: true,
    onEnter: (els) => gsap.to(els, { opacity: 1, y: 0, duration: 0.8, ease: "power3.out", stagger: 0.07 }),
  });

  // App window un-tilts as it scrolls into view (scrubbed, so Lenis makes it glide).
  gsap.fromTo(
    ".tilt",
    { rotateX: 18, scale: 0.92, opacity: 0.5 },
    { rotateX: 0, scale: 1, opacity: 1, ease: "none", transformOrigin: "50% 100%",
      scrollTrigger: { trigger: ".tilt", start: "top bottom", end: "top 30%", scrub: true } }
  );

  // Facts: numbers count up when they arrive (React Bits CountUp).
  document.querySelectorAll(".facts-row b").forEach((b) => {
    const n = Number(b.textContent);
    if (n > 0) createRoot(b).render(<CountUp to={n} duration={1.4} />);
  });
}
