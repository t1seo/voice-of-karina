# Local-first model choices

Reviewed against primary sources on 2026-09-20. These are capability comparisons, not a controlled listening benchmark or a claim that one model is best for Korean.

| Model / route | Relevant capability | Role in this repository |
|---|---|---|
| Qwen3-TTS Base with MLX | Reference audio plus matching transcript for voice cloning; Korean support | Implemented mimic and saved-voice backend: `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit` |
| Qwen3-TTS VoiceDesign with MLX | Description-driven original voice creation; Korean support | Implemented original-voice backend: `mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-4bit` |
| Chatterbox Multilingual V3 | 0.5B multilingual voice cloning, including Korean; upstream documents CPU, CUDA, and MPS routes | Comparison candidate, not installed or benchmarked here |
| Fun-CosyVoice3 | Multilingual zero-shot cloning including Korean, with instruction controls | Comparison candidate, not integrated or benchmarked here |
| ElevenLabs API | Voice cloning and description-based Voice Design | Hosted comparison candidate; no credentials, audio upload, or automatic fallback is implemented |

Qwen separates Base, CustomVoice, and VoiceDesign capabilities. This project does not use CustomVoice presets or pretend that Base accepts every style-control parameter. Designed voices can be reused from their saved audio reference, following the Qwen authors' design-then-clone approach. [Qwen3-TTS documentation](https://github.com/QwenLM/Qwen3-TTS#voice-design-then-clone), [MLX Qwen3-TTS adapter documentation](https://github.com/Blaizzy/mlx-audio/blob/main/docs/models/tts/qwen3-tts.md).

MLX is the chosen local execution route for Apple Silicon. The adapter pins `mlx-audio==0.5.4` and records model identifiers and revisions in generated artifacts. A smaller quantized Base model is a practical memory choice; this is not evidence that it sounds better than the old 1.7B model. The structural savings come from generating only requested messages, loading once per batch, releasing models between stages, and reusing saved reference audio.

For an alternative Korean clone, compare Chatterbox's **Multilingual** checkpoint, not its English-only Turbo or Nano checkpoints. Upstream V3 improvements are publisher claims, not measurements made with this project's voices or hardware. [Chatterbox official repository](https://github.com/resemble-ai/chatterbox).

CosyVoice3 documents Korean and cross-language cloning. Its installation and inference path would need a separate adapter and a real same-machine test before this project could advertise it as supported. [CosyVoice official repository](https://github.com/QwenAudio/CosyVoice).

ElevenLabs can provide a hosted comparison for clone and original-voice quality. It needs an account and network processing, and its voice creation features have their own plan and verification requirements. No current price is hard-coded here; compare the applicable terms and price when choosing it. [ElevenLabs voice capabilities](https://elevenlabs.io/docs/overview/capabilities/voices).

## A fair comparison

Use the same clean reference, its exact transcript, language, and requested text. Test a noisy reference separately. Record cold download/load time separately from generation time, and record device, memory, model revision, audio duration, transcription, and retry count. Listen blind for naturalness, pronunciation, speaker resemblance, and background artifacts. Signal measurements cannot replace those listening judgments.

Fresh published samples include provenance and quality evidence. They demonstrate the implemented path's output; they do not establish superiority over another model or guarantee celebrity identity.
