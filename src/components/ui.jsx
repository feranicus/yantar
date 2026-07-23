import { useEffect, useRef, useState } from 'react';

export function Eyebrow({ children }) {
  return <p className="eyebrow">{children}</p>;
}

export function H1({ children }) {
  return <h1 className="h1">{children}</h1>;
}

export function H2({ kicker, children }) {
  return <h2 className="h2">{kicker && <s>{kicker}</s>}{children}</h2>;
}

export function P({ children, lede }) {
  return <p className={lede ? 'lede' : 'p'}>{children}</p>;
}

export function Chips({ items }) {
  return (
    <div className="chips">
      {items.map(([k, v]) => <span className="chip" key={k}><s>{k}</s>{v}</span>)}
    </div>
  );
}

export function Spec({ rows }) {
  return (
    <div className="spec">
      {rows.map(([k, v]) => (
        <div className="sr" key={k}>
          <div className="k">{k}</div>
          <div className="v">{v}</div>
        </div>
      ))}
    </div>
  );
}

export function Cards({ items, stop }) {
  return (
    <div className="cards">
      {items.map(([tag, title, body]) => (
        <div className={stop ? 'card stop' : 'card'} key={title}>
          <span className="tag">{tag}</span>
          <h4>{title}</h4>
          <p>{body}</p>
        </div>
      ))}
    </div>
  );
}

export function Pull({ by, children }) {
  return (
    <div className="pull">
      <p>{children}</p>
      <div className="by">{by}</div>
    </div>
  );
}

export function Timeline({ items }) {
  return (
    <div className="tl">
      {items.map(([when, what, note]) => (
        <div className="ti" key={what}>
          <div className="w">{when}</div>
          <div className="t">{what}</div>
          <div className="n">{note}</div>
        </div>
      ))}
    </div>
  );
}

export function Flags({ go, no }) {
  return (
    <div className="flags">
      <div className="fl go">
        <h4>Что ценю</h4>
        <ul>{go.map((x) => <li key={x}>{x}</li>)}</ul>
      </div>
      <div className="fl no">
        <h4>Что не приму</h4>
        <ul>{no.map((x) => <li key={x}>{x}</li>)}</ul>
      </div>
    </div>
  );
}

export function Geo({ items }) {
  return (
    <div className="geo">
      {items.map(([c, s, d]) => (
        <div className="gc" key={c}>
          <div className="c">{c}</div>
          <div className="s">{s}</div>
          <div className="d">{d}</div>
        </div>
      ))}
    </div>
  );
}

export function Links({ items }) {
  return (
    <div className="links">
      {items.map(([tag, label, href]) => (
        <a className="lnk" key={href} href={href} target="_blank" rel="noopener noreferrer">
          <s>{tag}</s>{label}
        </a>
      ))}
    </div>
  );
}

export function Press({ source, quote, children, amber }) {
  return (
    <div className={amber ? 'press amb' : 'press'}>
      <div className="s">{source}</div>
      <p className="q">{quote}</p>
      <p className="m">{children}</p>
    </div>
  );
}

/* Счётчик: запускается, когда блок появляется в зоне видимости. */
export function Stats({ items, reduced }) {
  const ref = useRef(null);
  const [run, setRun] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || run) return undefined;
    if (!('IntersectionObserver' in window)) { setRun(true); return undefined; }
    const io = new IntersectionObserver((es) => {
      if (es[0].isIntersecting) { setRun(true); io.disconnect(); }
    }, { threshold: 0.4 });
    io.observe(el);
    return () => io.disconnect();
  }, [run]);

  return (
    <div className="stats" ref={ref}>
      {items.map(([to, suf, label]) => (
        <div className="st" key={label}>
          <div className="n"><Count to={to} suf={suf} run={run} reduced={reduced} /></div>
          <div className="l">{label}</div>
        </div>
      ))}
    </div>
  );
}

function Count({ to, suf, run, reduced }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!run) return undefined;
    if (reduced) { setN(to); return undefined; }
    let raf = 0, t0 = null;
    const step = (t) => {
      if (t0 === null) t0 = t;
      const p = Math.min((t - t0) / 900, 1);
      setN(Math.round(to * (1 - Math.pow(1 - p, 3))));
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [run, to, reduced]);
  return <>{n}{n === to ? suf : ''}</>;
}
