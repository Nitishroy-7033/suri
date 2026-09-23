# SuriRobot — React integration

Animated 3D robot (three.js). Every parameter from the playground is a live React prop; gestures and speech are ref methods.

## Files
- `robotEngine.js` — the whole robot: model, animation, lights, camera. No React; edit this to change shape/behaviour.
- `SuriRobot.jsx` — React wrapper (props → `setOptions`, ref → actions).
- `SuriRobot.d.ts` — types (TypeScript projects).
- `RobotDemo.jsx` — example screen with a control for every prop.

## Install
1. `npm install three` (built against three r184; any recent version works).
2. Copy the `robot-react` folder into `src/components/robot/`.
3. Use it — the parent must have a height:

```jsx
import { useRef } from 'react';
import { SuriRobot } from './components/robot/SuriRobot';

export default function Home() {
  const bot = useRef(null);
  return (
    <div style={{ height: '100vh' }}>
      <SuriRobot ref={bot} mood="happy" antennaSpring={0.8} background={null} />
      <button onClick={() => bot.current.play('wave')}>Wave</button>
    </div>
  );
}
```

Works with Vite, CRA and Next.js. In Next.js App Router, add `'use client'` at the top of `SuriRobot.jsx` (or load it with `dynamic(() => import(...), { ssr: false })`).

## Props (all live)
| prop | values | default |
|---|---|---|
| mood | idle, listening, thinking, talking, happy, love, alert, confused, angry, dizzy, sleepy, sad | idle |
| look | wander, cursor, center | wander |
| antennaMotion | 0–2 | 1 |
| antennaSpring | 0 (stiff) – 1 (floppy) | 0.55 |
| blinkRate | blinks / min, 0–40 | 14 |
| speed | 0.25–2 | 1 |
| headMotion, earPulse, tipLight | boolean | true |
| talkRate | 0.5–1.8 | 1 |
| mouthSize | 0.4–1.4 | 1 |
| voice | speak `say()` text aloud | true |
| armMode | auto (mood-driven), manual | auto |
| armPose | [leftUp, rightUp, leftFwd, rightFwd] degrees | [0,0,0,0] |
| colors | { shell, trim, screen, glow } hex | white / grey / black / #1e88ff |
| background | hex or null (transparent) | null |
| orbit | drag to rotate | true |
| shadow | ground shadow | true |
| boot | boot animation on mount | true |
| onReady(engine), onSpeakingChange(bool) | callbacks | |

## Ref methods
`play(gesture)` — nod, shake, surprise, spin, scan, celebrate, sneeze, boot, wave
`flash(mood, ms)` — show a mood briefly, then return
`blink()`, `wink()`, `say(text)`, `stopSay()`
`setArmPose(name | [4 degrees])` — rest, bothUp, point, pointUp, reachOut, tPose, victory, hi, shrug, hug, chin, hips
`setSpeaking(on, bandsFn)` — lip-sync to real audio; `bandsFn()` returns 0–1 levels (e.g. 8 FFT bands)
`setLevel(0..1)` — mic level; antenna and ears react while `mood="listening"`

### Lip-sync to your own audio (voice AI)
```js
const analyser = audioCtx.createAnalyser(); analyser.fftSize = 256;
ttsOutputNode.connect(analyser);
const data = new Uint8Array(analyser.frequencyBinCount);
const bands = () => { analyser.getByteFrequencyData(data); const n = 8, per = 8, out = [];
  for (let b = 0; b < n; b++) { let s = 0; for (let k = 0; k < per; k++) s += data[b * per + k]; out.push(Math.min(1, s / per / 190)); }
  return out; };
bot.current.setSpeaking(true, bands);   // when audio starts
bot.current.setSpeaking(false);         // when it ends
```

## Changing things
- Shape/size: the "base", "body", "arms", "head" sections in `robotEngine.js` (meters, y-up).
- Mood behaviour: the `MOODS` table (antenna style, eye openness, smile, head tilt, colour).
- Arm poses per mood: `ARMS` table; gestures: `gesture()` switch.
- New gesture: add its duration to `GEST`, a `case` in `gesture()`, and the name to `GESTURES`.
