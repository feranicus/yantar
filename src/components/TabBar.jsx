import { TABS } from '../data.jsx';

const ICONS = {
  1: <path d="M12 3l9 9-9 9-9-9z" />,
  2: <><rect x="3" y="4" width="18" height="6" rx="1" /><rect x="3" y="14" width="18" height="6" rx="1" /><path d="M7 7h.01M7 17h.01" /></>,
  3: <path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z" />,
  4: <path d="M3 5h18l-7 8v6l-4 2v-8z" />,
  5: <><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" /></>
};

export default function TabBar({ tab, onTab }) {
  return (
    <nav className="tabbar" aria-label="Разделы">
      {TABS.map((t) => (
        <button
          key={t.id}
          type="button"
          className={tab === t.id ? 'tb on' : 'tb'}
          aria-current={tab === t.id ? 'page' : undefined}
          onClick={() => onTab(t.id)}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">{ICONS[t.id]}</svg>
          {t.label}
        </button>
      ))}
    </nav>
  );
}
