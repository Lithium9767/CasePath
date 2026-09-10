import { useEffect, useState } from 'react';
import App from './App';
import './entrance.css';

const SEEN = 'casepath.intro.seen.v1';

function shouldShow() {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return false;
  try {
    return new URLSearchParams(window.location.search).get('intro') === '1'
      || !sessionStorage.getItem(SEEN);
  } catch {
    return true;
  }
}

// 纯前端品牌入场，不伪装模型加载或图谱查询；同一标签页默认只播放一次。
export default function Entrance() {
  const [visible, setVisible] = useState(shouldShow);
  useEffect(() => {
    if (!visible) return;
    const finish = () => setVisible(false);
    const timer = window.setTimeout(finish, 2800);
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') finish(); };
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const change = () => { if (motion.matches) finish(); };
    window.addEventListener('keydown', key);
    motion.addEventListener('change', change);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('keydown', key);
      motion.removeEventListener('change', change);
    };
  }, [visible]);

  useEffect(() => {
    if (!visible) {
      try { sessionStorage.setItem(SEEN, '1'); } catch { /* 禁用存储仍可进入应用。 */ }
    }
  }, [visible]);

  if (!visible) return <div className="cp-app-reveal"><App /></div>;
  return (
    <section className="cp-entrance" aria-label="CasePath 欢迎页">
      <div className="cp-intro-grid" aria-hidden="true" />
      <div className="cp-intro-top"><span className="cp-monogram">C<span>╱</span>P</span><span>LEGAL INTELLIGENCE</span></div>
      <button className="cp-intro-skip" onClick={() => setVisible(false)} autoFocus>
        跳过动画 <span aria-hidden="true">↗</span>
      </button>
      <svg className="cp-constellation" viewBox="0 0 1200 620" fill="none" aria-hidden="true">
        <defs>
          <linearGradient id="cp-line" x1="180" y1="310" x2="1030" y2="310" gradientUnits="userSpaceOnUse">
            <stop stopColor="#5178ff" stopOpacity="0.1"/><stop offset=".5" stopColor="#8ddce8"/><stop offset="1" stopColor="#5178ff" stopOpacity=".1"/>
          </linearGradient>
          <radialGradient id="cp-glow"><stop stopColor="#5280ff" stopOpacity=".25"/><stop offset="1" stopColor="#5280ff" stopOpacity="0"/></radialGradient>
        </defs>
        <ellipse cx="600" cy="310" rx="490" ry="295" fill="url(#cp-glow)"/>
        <g className="cp-orbits" stroke="#6e91d6" strokeOpacity=".16">
          <ellipse cx="600" cy="310" rx="440" ry="195" transform="rotate(-18 600 310)"/>
          <ellipse cx="600" cy="310" rx="340" ry="265" transform="rotate(24 600 310)"/>
          <circle cx="600" cy="310" r="220" strokeDasharray="2 12"/>
        </g>
        <g className="cp-paths" stroke="url(#cp-line)" strokeWidth="1.5">
          <path d="M110 390L255 240L395 320L510 140L740 180L850 320L1030 230L1120 380"/>
          <path d="M180 150L255 240L260 465L395 320L600 470L850 320L960 470L1030 230"/>
          <path d="M110 390L260 465L600 470L740 180L1030 230M180 150L510 140"/>
        </g>
        <g className="cp-nodes" fill="#99dcea" stroke="#111d39" strokeWidth="5">
          {[[110,390],[180,150],[255,240],[260,465],[395,320],[510,140],[600,470],[740,180],[850,320],[960,470],[1030,230],[1120,380]].map(([cx,cy], i) =>
            <circle key={i} cx={cx} cy={cy} r={i % 3 === 0 ? 7 : 5} style={{ animationDelay: `${i * 65 + 100}ms` }}/>) }
        </g>
        <g className="cp-node-labels" fill="#8fa7cb" fontSize="12" fontFamily="sans-serif" letterSpacing="3">
          <text x="220" y="217">RULE</text><text x="715" y="153">CONDITION</text><text x="975" y="207">CASE</text>
        </g>
      </svg>
      <div className="cp-intro-center">
        <div className="cp-eyebrow">从事实出发 · 让依据可见</div>
        <h1>Case<span>Path</span><i aria-hidden="true">.</i></h1>
        <div className="cp-title-line" aria-hidden="true" />
        <p>每一个问题，都有一条值得追寻的路径。</p>
        <div className="cp-intro-tags"><span>规则</span><b>—</b><span>条件</span><b>—</b><span>案例</span><b>—</b><span>证据</span></div>
      </div>
      <div className="cp-intro-bottom"><span>CASEPATH / 交互式法律信息检索</span><span>EXPLORE WITH EVIDENCE</span></div>
      <div className="cp-intro-progress" aria-hidden="true" />
    </section>
  );
}
