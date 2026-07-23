import { useEffect, useState } from 'react';
import { BOOT } from '../data.jsx';

const COLOR = { dim: 'var(--ash)', ice: 'var(--ice)', amb: 'var(--amber)' };

export default function Boot({ reduced, onDone }) {
  const [n, setN] = useState(reduced ? BOOT.length : 0);
  const [off, setOff] = useState(false);

  useEffect(() => {
    if (reduced) { const t = setTimeout(() => setOff(true), 200); return () => clearTimeout(t); }
    if (n >= BOOT.length) { const t = setTimeout(() => setOff(true), 400); return () => clearTimeout(t); }
    const t = setTimeout(() => setN((v) => v + 1), 250);
    return () => clearTimeout(t);
  }, [n, reduced]);

  useEffect(() => {
    const guard = setTimeout(() => setOff(true), 3600);
    return () => clearTimeout(guard);
  }, []);

  useEffect(() => {
    if (!off) return undefined;
    const t = setTimeout(onDone, 500);
    return () => clearTimeout(t);
  }, [off, onDone]);

  return (
    <div id="boot" className={off ? 'off' : ''} onClick={() => setOff(true)}>
      <div className="bootbox">
        <div>
          {BOOT.slice(0, n).map(([lvl, text], i) => (
            <div key={text}>
              <span className="dim">[{String(i + 1).padStart(2, '0')}]</span>{' '}
              <span style={{ color: COLOR[lvl] }}>{text}</span>
            </div>
          ))}
        </div>
        <div className="bootbar"><i style={{ width: `${(n / BOOT.length) * 100}%` }} /></div>
      </div>
    </div>
  );
}
