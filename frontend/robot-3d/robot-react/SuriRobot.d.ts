import type { ForwardRefExoticComponent, RefAttributes, CSSProperties } from 'react';

export type Mood = 'idle' | 'listening' | 'thinking' | 'talking' | 'happy' | 'love' | 'alert' | 'confused' | 'angry' | 'dizzy' | 'sleepy' | 'sad';
export type Gesture = 'nod' | 'shake' | 'surprise' | 'spin' | 'scan' | 'celebrate' | 'sneeze' | 'boot' | 'wave';
export type ArmPoseName = 'rest' | 'bothUp' | 'point' | 'pointUp' | 'reachOut' | 'tPose' | 'victory' | 'hi' | 'shrug' | 'hug' | 'chin' | 'hips';
/** [leftUp, rightUp, leftForward, rightForward] in degrees */
export type ArmPose = [number, number, number, number];

export interface SuriRobotProps {
  mood?: Mood;
  look?: 'wander' | 'cursor' | 'center';
  antennaMotion?: number;   // 0–2
  antennaSpring?: number;   // 0 stiff – 1 floppy
  blinkRate?: number;       // blinks / min
  speed?: number;           // 0.25–2
  headMotion?: boolean;
  earPulse?: boolean;
  tipLight?: boolean;
  talkRate?: number;        // 0.5–1.8
  mouthSize?: number;       // 0.4–1.4
  voice?: boolean;          // speak say() text with the browser voice
  armMode?: 'auto' | 'manual';
  armPose?: ArmPose;
  colors?: { shell?: string; trim?: string; screen?: string; glow?: string };
  background?: string | null; // null = transparent
  orbit?: boolean;
  shadow?: boolean;
  boot?: boolean;           // play boot animation on mount
  className?: string;
  style?: CSSProperties;
  onReady?: (engine: RobotEngine) => void;
  onSpeakingChange?: (speaking: boolean) => void;
}

export interface SuriRobotHandle {
  play(g: Gesture): void;
  flash(m: Mood, ms?: number): void;
  blink(): void;
  wink(): void;
  say(text: string): void;
  stopSay(): void;
  setSpeaking(on: boolean, bands?: () => ArrayLike<number> | null): void;
  setLevel(level: number): void;
  setArmPose(p: ArmPoseName | ArmPose): void;
  readonly engine: RobotEngine | null;
}

export interface RobotEngine extends Omit<SuriRobotHandle, 'engine'> {
  setOptions(p: Partial<SuriRobotProps>): void;
  setMood(m: Mood): void;
  dispose(): void;
  object: any; scene: any; camera: any; renderer: any;
}

export const SuriRobot: ForwardRefExoticComponent<SuriRobotProps & RefAttributes<SuriRobotHandle>>;
export default SuriRobot;
export const MOODS: Mood[];
export const GESTURES: Gesture[];
export const ARM_POSES: Record<ArmPoseName, ArmPose>;
export function createRobot(container: HTMLElement, options?: Partial<SuriRobotProps>): RobotEngine;
