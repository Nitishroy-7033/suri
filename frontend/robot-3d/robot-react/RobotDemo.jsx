import { useRef, useState } from 'react';
import { SuriRobot, MOODS, GESTURES, ARM_POSES } from './SuriRobot';

// Example screen: every parameter wired to a control.
export default function RobotDemo() {
  const bot = useRef(null);
  const [p, setP] = useState({
    mood: 'idle', look: 'wander', antennaMotion: 1, antennaSpring: 0.55, blinkRate: 14, speed: 1,
    headMotion: true, earPulse: true, tipLight: true, talkRate: 1, mouthSize: 1, voice: true,
    armMode: 'auto', armPose: [0, 0, 0, 0],
  });
  const [text, setText] = useState("Hi! I'm Suri, your robot friend.");
  const set = (k) => (e) => setP((s) => ({ ...s, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.type === 'range' ? +e.target.value : e.target.value }));
  const range = (k, min, max, step) => (
    <label style={{ display: 'grid', gap: 4 }}>{k} <b>{p[k]}</b>
      <input type="range" min={min} max={max} step={step} value={p[k]} onChange={set(k)} />
    </label>
  );
  const toggle = (k) => <label style={{ display: 'flex', gap: 8 }}><input type="checkbox" checked={p[k]} onChange={set(k)} />{k}</label>;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', height: '100vh' }}>
      <aside style={{ padding: 16, overflow: 'auto', display: 'grid', gap: 12, alignContent: 'start', font: '13px system-ui' }}>
        <select value={p.mood} onChange={set('mood')}>{MOODS.map((m) => <option key={m}>{m}</option>)}</select>
        <select value={p.look} onChange={set('look')}>{['wander', 'cursor', 'center'].map((m) => <option key={m}>{m}</option>)}</select>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {GESTURES.map((g) => <button key={g} onClick={() => bot.current.play(g)}>{g}</button>)}
          <button onClick={() => bot.current.blink()}>blink</button>
          <button onClick={() => bot.current.wink()}>wink</button>
        </div>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} />
        <div style={{ display: 'flex', gap: 6 }}>
          <button onClick={() => bot.current.say(text)}>say</button>
          <button onClick={() => bot.current.stopSay()}>stop</button>
        </div>
        {range('antennaMotion', 0, 2, 0.05)}{range('antennaSpring', 0, 1, 0.05)}
        {range('blinkRate', 0, 40, 1)}{range('speed', 0.25, 2, 0.05)}
        {range('talkRate', 0.5, 1.8, 0.05)}{range('mouthSize', 0.4, 1.4, 0.05)}
        {toggle('headMotion')}{toggle('earPulse')}{toggle('tipLight')}{toggle('voice')}
        <select value={p.armMode} onChange={set('armMode')}><option>auto</option><option>manual</option></select>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {Object.entries(ARM_POSES).map(([n, pose]) => (
            <button key={n} onClick={() => setP((s) => ({ ...s, armMode: 'manual', armPose: pose }))}>{n}</button>
          ))}
        </div>
        {['left up', 'right up', 'left fwd', 'right fwd'].map((lbl, i) => (
          <label key={lbl} style={{ display: 'grid', gap: 4 }}>{lbl} <b>{p.armPose[i]}°</b>
            <input type="range" min={i < 2 ? -10 : -60} max={i < 2 ? 170 : 120} value={p.armPose[i]}
              onChange={(e) => setP((s) => { const a = [...s.armPose]; a[i] = +e.target.value; return { ...s, armMode: 'manual', armPose: a }; })} />
          </label>
        ))}
      </aside>
      <SuriRobot ref={bot} {...p} background="#e8ebee" />
    </div>
  );
}
