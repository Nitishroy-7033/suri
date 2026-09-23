import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { createRobot } from './robotEngine';

/**
 * <SuriRobot /> — animated 3D robot. Every prop is live: change it and the robot updates
 * without remounting. Imperative actions (gestures, speech) go through the ref.
 */
export const SuriRobot = forwardRef(function SuriRobot(
  { className, style, onReady, onSpeakingChange, ...params },
  ref,
) {
  const host = useRef(null);
  const bot = useRef(null);
  const latest = useRef(params);
  latest.current = params;
  const speakCb = useRef(onSpeakingChange);
  speakCb.current = onSpeakingChange;

  useEffect(() => {
    const r = createRobot(host.current, { ...latest.current, onSpeakingChange: (v) => speakCb.current?.(v) });
    bot.current = r;
    onReady?.(r);
    return () => { r.dispose(); bot.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sig = JSON.stringify(params);
  useEffect(() => { bot.current?.setOptions(latest.current); }, [sig]);

  useImperativeHandle(ref, () => ({
    play: (g) => bot.current?.play(g),
    flash: (m, ms) => bot.current?.flash(m, ms),
    blink: () => bot.current?.blink(),
    wink: () => bot.current?.wink(),
    say: (t) => bot.current?.say(t),
    stopSay: () => bot.current?.stopSay(),
    setSpeaking: (on, fn) => bot.current?.setSpeaking(on, fn),
    setLevel: (l) => bot.current?.setLevel(l),
    setArmPose: (p) => bot.current?.setArmPose(p),
    get engine() { return bot.current; },
  }), []);

  return <div ref={host} className={className} style={{ position: 'relative', width: '100%', height: '100%', ...style }} />;
});

export default SuriRobot;
export { MOODS, GESTURES, ARM_POSES, DEFAULTS } from './robotEngine';
