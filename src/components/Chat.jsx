import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

const GREETING = {
  role: 'assistant',
  content: 'Привет! Я Кассандра — ИИ-ассистент Евгения. Спросите меня о нём: работа, ценности, '
    + 'семья, планы на будущее. Что хотите узнать?'
};

export default function Chat() {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState([GREETING]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [msgs, open, busy]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    const next = [...msgs, { role: 'user', content: text }];
    setMsgs(next);
    setInput('');
    setBusy(true);
    try {
      const payload = next
        .filter((m) => m !== GREETING)
        .map(({ role, content }) => ({ role, content }));
      const r = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: payload })
      });
      const d = await r.json();
      setMsgs((m) => [...m, {
        role: 'assistant',
        content: d.reply || 'Напишите Евгению напрямую: Telegram @feranicus.'
      }]);
    } catch {
      setMsgs((m) => [...m, {
        role: 'assistant',
        content: 'Связь прервалась. Напишите Евгению напрямую: Telegram @feranicus или '
          + 'WhatsApp +49 157 8554 1545.'
      }]);
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  };

  return (
    <>
      <button className="chat-fab" type="button" aria-label="Спросить ИИ о Евгении" onClick={() => setOpen(true)}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.4 8.8 8.8 0 0 1-3.8-.85L3 21l1.9-4.3A8.3 8.3 0 0 1 4 11.5 8.4 8.4 0 0 1 12.5 3 8.4 8.4 0 0 1 21 11.5z" />
        </svg>
        <span>Спросить ИИ</span>
      </button>

      {open && createPortal(
        <div className="chat" role="dialog" aria-modal="true" aria-label="Чат с ИИ о Евгении">
          <div className="chat-hd">
            <span className="chat-ti"><i />Кассандра · ИИ о Евгении</span>
            <button className="chat-x" type="button" aria-label="Закрыть" onClick={() => setOpen(false)}>×</button>
          </div>
          <div className="chat-body">
            {msgs.map((m, i) => (
              <div key={i} className={m.role === 'user' ? 'cm me' : 'cm ai'}>{m.content}</div>
            ))}
            {busy && <div className="cm ai typing"><i /><i /><i /></div>}
            <div ref={endRef} />
          </div>
          <div className="chat-in">
            <textarea
              rows="1"
              value={input}
              placeholder="Спросите о Евгении…"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
            />
            <button type="button" aria-label="Отправить" disabled={busy || !input.trim()} onClick={send}>→</button>
          </div>
        </div>,
        document.body
      )}
    </>
  );
}
