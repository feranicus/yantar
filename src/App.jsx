import { useCallback, useEffect, useRef, useState } from 'react';
import {
  TABS, CHIPS, SPEC_BASE, CHARACTER, STATS, LOGOS, TIMELINE,
  VALUES, LIFE, SPEC_WANT, STOPS, FLAGS_GO, FLAGS_NO, GEO, LINKS
} from './data.jsx';
import {
  Eyebrow, H1, H2, P, Chips, Spec, Cards, Pull, Timeline,
  Flags, Geo, Links, Press, Stats
} from './components/ui.jsx';
import AmberField from './components/AmberField.jsx';
import TabBar from './components/TabBar.jsx';
import Boot from './components/Boot.jsx';
import Match from './components/Match.jsx';

const reduced = typeof window !== 'undefined'
  && window.matchMedia
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function initialTab() {
  if (typeof window === 'undefined') return 1;
  const n = Number(new URLSearchParams(window.location.search).get('tab'));
  return n >= 1 && n <= 5 ? n : 1;
}

export default function App() {
  const [tab, setTab] = useState(initialTab);
  const [booted, setBooted] = useState(false);
  const scrollPos = useRef({});

  const go = useCallback((n) => {
    if (n < 1 || n > TABS.length) return;
    setTab((cur) => {
      if (n === cur) return cur;
      scrollPos.current[cur] = window.pageYOffset;
      window.requestAnimationFrame(() => window.scrollTo(0, scrollPos.current[n] || 0));
      if (navigator.vibrate) { try { navigator.vibrate(8); } catch { /* нет вибро */ } }
      return n;
    });
  }, []);

  /* свайп между вкладками */
  useEffect(() => {
    let x = 0, y = 0, on = false;
    const start = (e) => {
      if (e.touches.length !== 1) { on = false; return; }
      if (e.target.closest && e.target.closest('input[type=range]')) { on = false; return; }
      x = e.touches[0].clientX; y = e.touches[0].clientY; on = true;
    };
    const end = (e) => {
      if (!on) return;
      on = false;
      const dx = e.changedTouches[0].clientX - x;
      const dy = e.changedTouches[0].clientY - y;
      if (Math.abs(dx) > 70 && Math.abs(dx) > Math.abs(dy) * 1.8) go(tab + (dx < 0 ? 1 : -1));
    };
    document.addEventListener('touchstart', start, { passive: true });
    document.addEventListener('touchend', end, { passive: true });
    return () => {
      document.removeEventListener('touchstart', start);
      document.removeEventListener('touchend', end);
    };
  }, [tab, go]);

  /* стрелки на десктопе */
  useEffect(() => {
    const key = (e) => {
      if (e.target.tagName === 'INPUT') return;
      if (e.key === 'ArrowRight') go(tab + 1);
      if (e.key === 'ArrowLeft') go(tab - 1);
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [tab, go]);

  const title = TABS.find((t) => t.id === tab)?.label ?? '';
  useEffect(() => { document.title = `${title} · ЯНТАРЬ`; }, [title]);

  return (
    <>
      <div className="glow" />
      <AmberField reduced={reduced} />
      <div className="grain" />

      {!booted && <Boot reduced={reduced} onDone={() => setBooted(true)} />}

      <header className="topbar">
        <span className="mark"><i /></span>
        <span className="tb-t">{title}</span>
        <span className="tb-r">rev 45.0</span>
      </header>

      <main id="app">
        {tab === 1 && <Screen1 />}
        {tab === 2 && <Screen2 />}
        {tab === 3 && <Screen3 />}
        {tab === 4 && <Screen4 />}
        {tab === 5 && <Screen5 />}
      </main>

      <TabBar tab={tab} onTab={go} />
    </>
  );
}

function Screen1() {
  return (
    <section className="screen">
      <Eyebrow>Личное досье · Рига → Тель-Авив → Франкфурт</Eyebrow>
      <H1>Евгений<br /><em>Вайнштейн</em></H1>
      <P lede>
        <b>Principal Architect</b> — облака, кибербезопасность, ИИ, телеком.
        Предприниматель, 7 компаний. Отец двоих сыновей.
      </P>
      <P>
        Я всю жизнь строю системы: из идеи — архитектура, из архитектуры — работающий продукт.
        Это досье написано по тем же правилам. <b>Здесь нет витрины</b> — есть спецификация:
        кто я, во что верю, что предлагаю и чего не приму.
      </P>
      <Chips items={CHIPS} />

      <H2 kicker="01 · База">Основные параметры</H2>
      <Spec rows={SPEC_BASE} />

      <H2 kicker="02 · Личность">Кто я без резюме</H2>
      <P>Самостоятельный, образованный, целеустремлённый, прямой и надёжный. Человек действия.</P>
      <Cards items={CHARACTER} />

      <Pull by="Из личного текста">Мне нужна <em>партнёрша</em>, а не зрительница.</Pull>

      <P>
        Я готов работать, зарабатывать, защищать семью и решать сложные вопросы. Но не хочу видеть
        рядом человека, который в трудный момент садится в стороне с попкорном. Сегодня сильнее
        один, завтра другой — в настоящей семье поддерживают, а не оценивают отношения по уровню
        комфорта.
      </P>
    </section>
  );
}

function Screen2() {
  return (
    <section className="screen">
      <Eyebrow>Профессия · itzen.ai</Eyebrow>
      <H1>Чем я<br /><em>занимаюсь</em></H1>
      <P lede>
        Руководитель, предприниматель, программист. IT, телеком, искусственный интеллект
        и кибербезопасность.
      </P>

      <Stats items={STATS} reduced={reduced} />

      <P>
        Прошёл ведущие международные технологические компании и вырос из инженера в архитектора
        и директора программ. Могу с нуля собрать продукт, сервис или стартап: идея, архитектура,
        инфраструктура, софт, команда, запуск.
      </P>

      <div className="logos">
        {LOGOS.map((l) => <span className="lg" key={l}>{l}</span>)}
      </div>

      <H2 kicker="Траектория">Что за этим стоит</H2>
      <Timeline items={TIMELINE} />

      <Press
        source="Пресса · The Bell · 29.01.2023"
        quote="В обзоре шести виз для айтишников The Bell пригласил меня рассказать про немецкую Blue Card — как эксперта и волонтёра по релокации в Германию."
      >
        <a
          href="https://thebell.io/gde-zhdut-rossiyskikh-aytishnikov-otchety-tesla-i-microsoft-i-kak-iskat-metadannye"
          target="_blank"
          rel="noopener noreferrer"
        >
          Читать материал →
        </a>
      </Press>

      <Links items={LINKS.slice(0, 2)} />
    </section>
  );
}

function Screen3() {
  return (
    <section className="screen">
      <Eyebrow>Основа · конфуцианские семейные ценности</Eyebrow>
      <H1>На чём<br /><em>стою</em></H1>
      <P lede>Это не эстетика, а рабочая система координат. По ней принимаются решения.</P>

      <div className="cards">
        {VALUES.map(([t, d], i) => (
          <div className="card" key={t}>
            <span className="tag">{String(i + 1).padStart(2, '0')}</span>
            <h4>{t}</h4>
            <p>{d}</p>
          </div>
        ))}
      </div>

      <Pull by="Определение по умолчанию">
        Скромность — это <em>внутреннее достоинство</em>, а не отсутствие амбиций.
      </Pull>

      <P>
        Отсутствие потребности постоянно демонстрировать себя, собирать лайки и строить жизнь
        вокруг чужого одобрения. Мне не важны цвет кожи, национальность, происхождение или
        конфессия — важно, чтобы человек был по-настоящему хорошим: добрым, честным, разумным,
        надёжным и способным любить.
      </P>
      <P>
        По политическим и экономическим взглядам я человек правых и либертарианских убеждений:
        личная свобода, частная собственность, свободный рынок, персональная ответственность.
        Об этом честнее сказать сразу, чем выяснять через полгода.
      </P>

      <H2 kicker="Вне работы">Спорт, дорога, люди</H2>
      <Cards items={LIFE} />

      <P>
        Мне нравится видеть реальный результат: человек находит работу, переезжает, начинает новую
        жизнь и становится самостоятельнее. Знания имеют ценность, когда ими можно улучшать
        не только собственную жизнь.
      </P>

      <Press
        amber
        source="Семья · Рига → Израиль"
        quote="Моя мама, Эсфирь Вайнштейн, с 1992 по 2010 год была известным в Израиле экстрасенсом. К сожалению, её уже нет с нами."
      >
        Я вырос между инженерным расчётом и верой в то, что человека нужно чувствовать.
        Обе стороны во мне остались.
      </Press>
    </section>
  );
}

function Screen4() {
  return (
    <section className="screen">
      <Eyebrow>Запрос · серьёзные отношения, брак, семья</Eyebrow>
      <H1>Кого<br /><em>я ищу</em></H1>
      <P lede>Не бесконечные знакомства, не поверхностные романы и не связь без понятного будущего.</P>

      <Spec rows={SPEC_WANT} />

      <Pull by="Про внешность — честно">
        Не лягушка и не Шарлиз Терон. <em>Божественная середина</em> — вот это прямо моё.
      </Pull>

      <P>
        Меня раздражают обе крайности. «Внешность неважна, главное душа» — лукавство: влечение
        либо есть, либо его нет, и взрослые люди это знают. «Я красивая, этого достаточно» — тоже
        мимо: с красивой витриной и пустотой внутри жить невозможно. Мне нужны обе части сразу —
        интересный человек, на которого при этом хочется смотреть. Ухоженность и форма — это
        не тщеславие, а уважение к себе и ко мне. И работает в обе стороны: я тоже держу форму.
      </P>

      <H2 kicker="Границы">Не обсуждается</H2>
      <P>Почти всё выше — предпочтения, и почти всё можно обсудить. Эти три пункта — нет.</P>
      <Cards items={STOPS} stop />

      <Pull by="Стоп 01 · развёрнуто">Нарциссу нужна <em>публика</em>. Мне нужна семья.</Pull>

      <P>
        Нарциссизм и эгоцентризм несовместимы с семьёй по устройству, а не по характеру. Такой
        человек не спрашивает «как нам будет лучше» — он спрашивает «что я с этого получу». Любой
        конфликт становится спектаклем, любая ошибка партнёра — поводом для наказания, любая его
        удача — поводом для обиды. С этим можно недолго встречаться. Жить — нельзя.
      </P>
      <P>
        <b>Про работу — тоже прямо.</b> Я много раз видел один и тот же сценарий: женщина
        перестаёт работать, круг общения сужается до квартиры, энергии остаётся столько же,
        а приложить её некуда — и вся она уходит на мужчину. Начинается контроль, придирки,
        «ты поздно», «ты не так посмотрел». Это не про плохой характер, это про пустоту, которую
        нечем заполнить. Мне нужна женщина со своей профессией и своим смыслом дня — чтобы
        у нас обоих была жизнь, из которой мы возвращаемся друг к другу.
      </P>
      <P>
        Речь не о карьере любой ценой и не о равенстве зарплат. Декрет, пауза, учёба, смена
        профессии, переезд — нормально и обсуждаемо. Ненормально — позиция «я не работаю
        и не собираюсь, обеспечивай».
      </P>

      <H2 kicker="Фильтр">Да и нет</H2>
      <Flags go={FLAGS_GO} no={FLAGS_NO} />
    </section>
  );
}

function Screen5() {
  return (
    <section className="screen">
      <Eyebrow>Инструмент · расчёт в вашем браузере</Eyebrow>
      <H1>Проверка<br /><em>совместимости</em></H1>
      <P lede>
        Раз уж это спецификация — вот линтер. Три пункта из раздела «Границы» работают как
        жёсткий стоп.
      </P>

      <Match />

      <H2 kicker="Будущее">Где мы можем жить</H2>
      <Geo items={GEO} />
      <P>
        Решение о стране — совместное: работа обоих, дети, безопасность, климат, качество жизни.
        Идеально, когда у партнёрши тоже есть профессия, совместимая с переездами.
      </P>

      <H2 kicker="Финал">Если дочитали — напишите</H2>
      <P>
        Не нужно соответствовать таблице. Нужно быть взрослым честным человеком, который хочет
        строить, а не только смотреть. Остальное обсудим вживую — за прогулкой, а не в переписке.
        Рекомендации от друзей и коллег, которые знают меня много лет, готов предоставить.
      </P>
      <Links items={LINKS} />
      <div className="foot">
        Евгений Вайнштейн · личное досье · ревизия 45.0<br />
        Рига → Тель-Авив → Франкфурт · jev.best
      </div>
    </section>
  );
}
