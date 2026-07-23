import { useMemo, useState } from 'react';
import { MATCH_FIELDS, MATCH_INIT, evaluate } from '../data.jsx';

const MARK = { ok: '[ ok ]', bad: '[fail]', warn: '[chk ]' };

function buzz(ms) {
  if (navigator.vibrate) { try { navigator.vibrate(ms); } catch { /* не поддерживается */ } }
}

export default function Match() {
  const [S, setS] = useState(MATCH_INIT);
  const res = useMemo(() => evaluate(S), [S]);

  const pick = (key, v) => { setS((p) => ({ ...p, [key]: v })); buzz(6); };

  return (
    <>
      <div className="f">
        <label htmlFor="age">Возраст <span className="val">{S.age}</span></label>
        <input
          id="age" type="range" min="22" max="58" value={S.age}
          onChange={(e) => setS((p) => ({ ...p, age: Number(e.target.value) }))}
        />
      </div>

      <div className="f">
        <label htmlFor="hgt">Рост, см <span className="val">{S.hgt}</span></label>
        <input
          id="hgt" type="range" min="150" max="192" value={S.hgt}
          onChange={(e) => setS((p) => ({ ...p, hgt: Number(e.target.value) }))}
        />
      </div>

      {MATCH_FIELDS.map((f) => (
        <div className="f" key={f.key}>
          <label>{f.label}</label>
          <div className="opts" role="group" aria-label={f.label}>
            {f.opts.map(([v, text]) => (
              <button
                key={v}
                type="button"
                className={S[f.key] === v ? 'opt sel' : 'opt'}
                aria-pressed={S[f.key] === v}
                onClick={() => pick(f.key, v)}
              >
                {text}
              </button>
            ))}
          </div>
        </div>
      ))}

      <div className="term">
        <div className="tlines">
          <div className="dim">$ ./match --spec jev.v45</div>
          {res.rows.map((r) => (
            <div key={r.k}>
              <span className={r.lvl}>{MARK[r.lvl]}</span>{' '}
              <span className="dim">{r.k}:</span> {r.v}
            </div>
          ))}
        </div>
        <div className="score">{res.pct}%</div>
        <div className="sbar"><i style={{ width: `${res.pct}%` }} /></div>
        <div className="verdict">{res.verdict}</div>
        <div className="disc">
          Расчёт идёт в вашем браузере — ничего не отправляется и не сохраняется. Модуль ничего
          не знает о том, как вы смеётесь, о чём думаете ночью и как ведёте себя, когда всё
          пошло не по плану. Именно это и решает.
        </div>
      </div>
    </>
  );
}
