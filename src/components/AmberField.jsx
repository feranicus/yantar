import { useEffect, useRef } from 'react';

/* Янтарное поле: частицы, застывшие в смоле. Останавливается,
   когда вкладка неактивна, и не запускается при prefers-reduced-motion. */
export default function AmberField({ reduced }) {
  const ref = useRef(null);

  useEffect(() => {
    if (reduced) return undefined;
    const cv = ref.current;
    if (!cv) return undefined;
    const ctx = cv.getContext('2d');
    let W = 0, H = 0, P = [], raf = null, live = true, rt = null;

    const size = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = window.innerWidth; H = window.innerHeight;
      cv.width = W * dpr; cv.height = H * dpr;
      cv.style.width = W + 'px'; cv.style.height = H + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const n = Math.max(18, Math.min(46, Math.round((W * H) / 26000)));
      P = Array.from({ length: n }, () => ({
        x: Math.random() * W, y: Math.random() * H,
        r: Math.random() * 1.9 + 0.5,
        vx: (Math.random() - 0.5) * 0.1,
        vy: -(Math.random() * 0.15 + 0.03),
        a: Math.random() * 0.5 + 0.15,
        p: Math.random() * 6.28
      }));
    };

    const draw = () => {
      if (!live) { raf = null; return; }
      ctx.clearRect(0, 0, W, H);
      for (let i = 0; i < P.length; i++) {
        const a = P[i];
        for (let j = i + 1; j < P.length; j++) {
          const b = P[j], dx = a.x - b.x, dy = a.y - b.y, d = dx * dx + dy * dy;
          if (d < 13000) {
            ctx.strokeStyle = `rgba(255,179,0,${0.08 * (1 - d / 13000)})`;
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
          }
        }
      }
      for (const p of P) {
        p.p += 0.011;
        p.x += p.vx + Math.sin(p.p) * 0.1;
        p.y += p.vy;
        if (p.y < -12) { p.y = H + 12; p.x = Math.random() * W; }
        if (p.x < -12) p.x = W + 12;
        if (p.x > W + 12) p.x = -12;
        const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.r * 7);
        g.addColorStop(0, `rgba(255,209,102,${p.a})`);
        g.addColorStop(1, 'rgba(255,179,0,0)');
        ctx.fillStyle = g;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r * 7, 0, 6.2832); ctx.fill();
      }
      raf = requestAnimationFrame(draw);
    };

    const onResize = () => { clearTimeout(rt); rt = setTimeout(size, 200); };
    const onVis = () => {
      live = !document.hidden;
      if (live && !raf) raf = requestAnimationFrame(draw);
    };

    size();
    raf = requestAnimationFrame(draw);
    window.addEventListener('resize', onResize);
    document.addEventListener('visibilitychange', onVis);

    return () => {
      live = false;
      if (raf) cancelAnimationFrame(raf);
      clearTimeout(rt);
      window.removeEventListener('resize', onResize);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [reduced]);

  return <canvas id="bgfx" ref={ref} aria-hidden="true" />;
}
