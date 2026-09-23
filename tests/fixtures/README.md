Speech fixtures for the Phase 1 probe, generated with Windows' built-in SAPI
voice so the test suite needs neither a microphone nor a human:

```powershell
Add-Type -AssemblyName System.Speech
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SetOutputToWaveFile("hey_jarvis.wav", $fmt)
$s.Speak("hey jarvis")
```

| file | says | expected |
|---|---|---|
| `hey_jarvis.wav` | "hey jarvis" | wake fires (0.999) |
| `hey_jarvis_q.wav` | "hey jarvis, what is python" | wake fires, full turn |
| `distractor.wav` | "hey there, can you tell me the weather today" | no wake (0.000) |
| `silence_words.wav` | "the quick brown fox..." | no wake; used as follow-up speech |

A robotic SAPI voice is a *harder* test than a real one for the distractors
and a fair one for the wake word, since openWakeWord was trained largely on
synthetic speech.

## Hindi / Indian-English fixtures

SAPI has no Hindi voice, so these were generated with edge-tts instead
(`hi-IN-MadhurNeural`, `hi-IN-SwaraNeural`, `en-IN-PrabhatNeural`,
`en-IN-NeerjaNeural`) and downsampled to 16 kHz.

| file | says |
|---|---|
| `hi_jarvis_q.wav` | "जार्विस, पाइथन क्या है?" |
| `hi_jarvis_q2.wav` | "जार्विस, मुझे बताओ पाइथन क्या है" |
| `in_jarvis_q.wav` | "Jarvis, what is Python?" (Indian English) |
| `in_jarvis_q2.wav` | "Hey Jarvis, can you explain Python" (Indian English) |
| `in_distractor.wav` | "The service was very good today" (Indian English) |

Used by `tests/probe_language.py`, which drives a full turn via push-to-talk
so the wake word is not part of what is being measured.

**These score badly on the wake word** (0.002 - 0.355 versus 0.99+ for the
US voices). openWakeWord's "hey jarvis" model was trained largely on
US/UK-accented synthetic speech. Worth keeping as a standing check on any
wake-word change.
