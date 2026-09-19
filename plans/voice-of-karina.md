# Voice of Karina replacement plan

## Outcome and scope

Replace the existing menu and script workflows with one conversational skill for Codex and Claude. The skill gathers missing intent, chooses mimic, design, or a saved voice, analyzes supplied recordings, generates only requested messages locally, checks results, and can apply finished audio to notifications when requested. The implementation is a Python 3.12 package driven by a private JSON transport; users are not asked to operate a CLI.

The user explicitly authorized implementation through verification, deleting the old implementation, preserving the Karina photo, generating fresh Karina samples, updating both READMEs, and renaming `t1seo/karina-voice-notification` to `t1seo/voice-of-karina`. Subsequent steering also requires a Buy Me a Coffee badge/link in both READMEs, reusing the actual badge asset and destination verified from `t1seo/icon-fairy`; do not invent a donation account or URL. The ideation document is existing user work and is preserved. No further product interview or approval gate is required.

Local MLX is the shipped backend. Optional paid APIs are compared in documentation and are never called automatically. This replacement does not implement every researched model. API access, unsupported platforms, speaker identification, quality improvement, or arbitrary speaking-speed control must not be advertised as implemented without evidence.

## Grounding and gap review

Existing entry points include `src/pipeline.py`, `src/quickstart.py`, `src/generate_samples.py`, `scripts/install_notifications.py`, `hooks/notification_player.py`, and two `.claude/skills` directories. Dependencies currently use Pixi. There is no existing automated test suite that needs to preserve the removed UX. `assets/karina.jpg` and three legacy sample WAVs are tracked. `docs/voice-skill-ideation.md` records the agreed product direction and known cache/reference-selection flaws.

A read-only planning review identified six acceptance risks addressed below: actual M1 16 GiB execution, speech detection versus named-person identity, resumable JSON state contracts, cache identity and saved-reference reuse, fresh samples versus genuinely playable GitHub media, and notification integration surviving the replacement.

Runtime research from the coordinator establishes `mlx-audio==0.5.4`: Base `generate` uses `lang_code="Korean"`; VoiceDesign `generate_voice_design` uses `language="Korean"`. Do not conflate these parameters. This MLX path has no confirmed public serializable clone-prompt API: persist reference WAV and exact reference transcript rather than claiming embedding reuse. Requested speed changes cannot be reported as applied by an unsupported parameter.

## Ownership and shared contracts

All workers are in the same worktree. No worker reverts another worker's changes. Ownership is exclusive; send a request to the owner when another module needs alteration. Keep production Python modules below 250 lines by separating responsibilities, use Pydantic v2 boundary parsing, typed errors, and test meaningful behavior.

| Lane | Exclusive ownership | Responsibility |
| --- | --- | --- |
| Coordinator | Sample artifacts/provenance and evidence | Integration coordination, dependency installation, real model runs, old-file deletion, GitHub rename, final checks, evidence reconciliation. Code fixes remain with each owning worker. |
| Core / Workflow | `pyproject.toml`, `uv.lock`, `.gitignore`, `ruff.toml`, `.pre-commit-config.yaml`, `src/voice_of_karina/contracts.py`, `src/voice_of_karina/__init__.py`, `tests/conftest.py`, `workflow.py`, `store.py`, `profiles.py`, `transport.py`, `__main__.py`, and matching `tests/test_workflow*`, `test_store*`, `test_transport*`, `test_profiles*` | Publish foundation contracts first, then request dispatch, job lifecycle, atomic persistence, locks, events, bounded retries, resume, output ownership, profile reuse. |
| Audio | `src/voice_of_karina/audio.py`, `sources.py`, `analysis.py`, `transcription.py`, `quality.py`, and matching `tests/test_audio*`, `test_sources*`, `test_analysis*`, `test_quality*` | Source download/decode/cache, speech candidates, honest noise indicators, reference cropping, transcription, output validation. |
| Backend | `src/voice_of_karina/backends/` and `tests/test_backends*` | Actual MLX clone and VoiceDesign adapters, device/capability diagnostics, sequential model lifecycle, one load per message batch, artifact metadata and model errors. |
| Skill / integration | `.agents/skills/voice-of-karina/`, `.claude/skills/voice-of-karina`, `src/voice_of_karina/notifications.py`, `notification_player.py`, matching notification tests, `README.md`, `README.ko.md`, `docs/model-comparison.md`, `docs/samples.html` | Conversational contract, agent invocation references, canonical skill and Claude mirror, optional notification install/player, README narrative and sample gallery. Coordinator supplies verified sample metadata/URLs before publication. |

These interfaces are the initial freeze point. The coordinator may refine names once before dispatch and must send the same contract to every lane; avoid independent interpretations.

- `Message`: stable `id`, nonempty `text`.
- `Request`: discriminated `action` (`analyze`, `generate`, `status`, `resume`, `voices`, `install`); `mode` (`mimic`, `design`, `reuse`) where relevant; `sources: list[str]`; `messages: list[Message]`; optional `voice_description`, `voice_id`, `profile_name`, `candidate_id`, `job_id`; optional selected source/timestamps; `language` defaults to Korean; bounded total `max_attempts`, default 2. URL and local-file inputs are validated at the boundary. Empty message lists are invalid for generation. Selection/resume may fill missing data but must not silently replace an existing request.
- `SourceTime` is carried in `Request.source_time`: zero-based `source_index: int >= 0`, finite `start_seconds: float >= 0`, and finite `end_seconds: float > start_seconds`; the index must address this request's sources. Supplying both `candidate_id` and `source_time` is invalid. A timestamp resolves target selection but still undergoes speech/quality analysis. `max_attempts` is 1–3 inclusive and counts total generations, including the first; default 2 persists across resume.
- `QualityOptions` is carried in `Request.quality` with defaults: `min_duration_seconds=0.25`, `max_duration_seconds=120.0`, `max_clipping_ratio=0.02`, `min_rms_dbfs=-50.0`, `max_text_error_rate=0.35`. These are internal engineering policy, not routine questions for users. Decode failure, nonfinite samples, empty/silent output, or a missing required artifact is always failure. Requested text is compared using character error rate after removing whitespace and punctuation; retain spoken letters/digits rather than silently deleting disagreements. ASR is required for shipping sample QA; an unavailable or uncertain transcript returns an explicit unverified/needs-input quality state, not a claimed text match. Per-attempt model allocation and downloads have bounded timeouts. Quality options are range-validated and persisted.
- `Candidate`: stable `id`, `source_id`, local `audio_path`, start/end seconds, optional exact `transcript`, measurements, warnings, and `needs_confirmation`. Measurements name their actual heuristic (for example clipping ratio or spectral flatness); they are not a reliable identity, music, or diarization detector.
- `AnalysisResult`: sources, ordered candidates, recommendation, warnings, and optional input request. Each candidate is traceable to one original source; recordings of different people are not concatenated automatically.
- `Reference`: persisted WAV path and content digest, transcript, provenance (source and timestamps or designed origin), preparation settings, and model metadata.
- `GeneratedAudio`: `message_id`, path, sample rate, duration, model identifier/revision, elapsed time. Model adapters return files and measurements; they do not decide job success.
- `QualityResult`: artifact validity, audibility/duration/clipping checks, transcript comparison where available, warnings, and pass/retry/needs-input decision. Missing ASR is explicit, not a fabricated pass.
- `JobResult`: `job_id`, status, current step, input request when blocked, per-message attempts and results, profile metadata, events, and errors. `completed` requires every requested message to have a validated matching artifact.
- Audio lane exports `analyze_sources(request: Request, job_dir: Path) -> AnalysisResult`, `prepare_reference(candidate: Candidate, job_dir: Path) -> Reference`, and `validate_output(audio: GeneratedAudio, expected_text: str, options: QualityOptions | None = None) -> QualityResult`; backend lane exports `synthesize(request: Request, reference: Reference | None, output_dir: Path) -> list[GeneratedAudio]`. `None` quality options use the documented defaults. Dependency injection is supported for tests without shipping a fake production backend. Workflow owns attempts, state changes, and reference/profile persistence.

## Execution waves

1. **Foundation gate:** Core publishes contracts and package/test scaffolding first while coordinator records the photo SHA-256 and checks repository access and machine resources. Lock dependencies and confirm a real MLX invocation/model availability early; model downloads can continue while independent implementation runs.
2. **Parallel implementation:** Workflow, Audio, Backend, and Skill / integration lanes implement their owned modules and focused tests against the frozen contract. Backend and Audio agree on the on-disk WAV format. The skill lane writes docs against actual field names and includes a pending verified-samples insertion point.
3. **Integration:** coordinator connects modules, runs meaningful contract/failure tests and strict quality gates, then drives real mimic and design through JSON transport. Save real profiles and exercise reuse/resume. Workers repair their own integration failures.
4. **Replacement and publication:** remove old tracked workflows once replacement paths run; write generated sample provenance and listening assets; finish both READMEs; perform authorized GitHub repository rename and update origin/links; preserve the workspace directory to avoid breaking active worker paths.
5. **Final independent review:** run required post-implementation reviewers, fix findings, repeat only affected checks, verify rendered README playback and preserved photo, and reconcile all evidence. No extra user approval gate is introduced.

Critical path: foundation contract → source/reference + backend implementation → real mimic/design → new samples and README publication → final behavior review.

## Tasks and acceptance

### 1. Foundation and local execution feasibility

- Add Python 3.12, uv, Pydantic v2, pytest, Ruff, and strict type checking. Pin the researched `mlx-audio` version; use platform markers and explicit unsupported-device responses. Declare source, audio, ASR, model, and developer dependencies intentionally rather than importing a legacy environment.
- Capture `assets/karina.jpg` SHA-256 before any deletion or publication. Preserve the original image bytes throughout.
- Check real Base and VoiceDesign model download/inference availability on the M1 with sequential model loading. Record initialization and generation separately; no unsupported speedup claim.
- References: `assets/karina.jpg`; existing `pixi.toml`; `docs/voice-skill-ideation.md`; `https://github.com/Blaizzy/mlx-audio/blob/main/docs/models/tts/qwen3-tts.md`.
- Acceptance: `uv sync`, `uv run pytest`, `uv run ruff check .`, and configured strict type checker execute against the new package; actual inference feasibility is resolved before final deletion.
- QA happy: parse a valid one-message request and obtain typed fields. QA failure: empty text, invalid mode, invalid time interval, or unsupported device produce a bounded structured error without model allocation. Evidence: `evidence/foundation.json`.
- Commit: no unsolicited commit; deliver reviewed working tree changes.

### 2. Source acquisition and reference selection

- Accept one or more YouTube URLs and local audio; preserve originals, key caches by source identity/content, cap downloads and timeouts, and never reuse a previous unrelated source merely because a filename exists.
- Detect speech windows and rank complete short utterances using actual measured features and ASR signals available. Preserve the best raw recording by default. Do not run Demucs, denoising, EQ, or compression indiscriminately. Optional mild processing must identify what it changed and preserve the raw candidate.
- Return candidate clips with original URL and timestamps. Uncertain target-speaker identity requires candidate/timestamp selection; name recognition is not inferred from loudness or an ASR transcript. If no usable speech exists, return a useful request for another clip.
- Transcribe only selected/candidate segments as needed, retain the native-quality reference separately from any ASR downsampling, and match reference audio/text exactly.
- References: `docs/voice-skill-ideation.md` sections 3, 6, and 8; shared contracts above.
- Acceptance: sources A and B produce separate fingerprints; selected crop contains the requested time interval; silence and invalid sources yield `needs_input` or a typed failure; no model is told a heuristic proves celebrity identity.
- QA happy: analyze the authorized Karina YouTube URL and show traceable playable candidates; select an actual Karina utterance using available recording evidence. QA failure: use a silent WAV and an unavailable URL; no fabricated transcript or candidate. Evidence: `evidence/source-analysis.json` and candidate WAVs.
- Commit: no unsolicited commit.

### 3. Actual clone, design, and saved-voice generation

- Implement one local MLX adapter with explicit mode capabilities. Clone invokes the Base model with the reference WAV/text and correct `lang_code`; design invokes VoiceDesign with the description and correct `language`. Model identifiers/revisions are recorded.
- Load a model once per batch, generate only requested messages, free large models between ASR/design/clone stages, and record per-message time and output metadata. Do not keep parallel large inference jobs on the M1.
- Save a designed reference WAV with its exact spoken text so later requests can clone that selected voice. Save mimic references similarly. Cache persistent WAV/transcript data; claim embedding reuse only if actually implemented and tested.
- Do not pass invented or unsupported speaking-rate/style arguments. A natural-language request for an unsupported capability produces an honest limitation or an explicitly implemented audio transformation.
- References: official Qwen VoiceDesign-then-clone flow and coordinator's verified `mlx-audio==0.5.4` call signatures.
- Acceptance: real Korean clone and design WAVs are nonempty, decodable, audible, and correspond to requested text; reused voices use the stored identical reference digest and skip download/analysis.
- QA happy: generate `작업이 끝났어요. 확인해 주세요.` and `확인이 필요해요. 잠깐 봐 주세요.` with Karina reference; generate `잠시 쉬어 가셔도 괜찮아요.` from a warm calm Korean female voice description; then create one new message from each saved profile. QA failure: model download or synthesis failure returns an actionable error, never a successful empty result. Evidence: `evidence/real-generation.json`, WAVs, and profile records.
- Commit: no unsolicited commit.

### 4. Durable workflow, quality loop, and transport

- Use job-local files with atomic replace, stable IDs, immutable original request, append-only events, and a same-job lock. Persist each stage before moving to the next. Stdout contains exactly one JSON result per invocation; logs and third-party progress output go to stderr.
- State graph: `created → analyzing/preparing → needs_input | generating → validating → completed`; recoverable interruptions retain a resumable stage; exhausted retries become `failed` or `needs_input` with existing good results preserved. Status is read-only. Resume never resets persisted attempt budgets.
- Validate generated artifact decode, length, finite samples, audibility, clipping, and the supported ASR/text comparison. Korean comparison normalizes spacing/punctuation without discarding meaningful text. ASR uncertainty is distinguished from obvious silence/mismatch. Bounded retry regenerates only failed messages.
- Stable cache keys include source content, crop, preparation, reference transcript, model revision, generation settings, and exact requested text. Files outside the job/profile store are not deleted through a caller-controlled identifier.
- References: contracts; `docs/voice-skill-ideation.md` sections 6 and 8.
- Acceptance: duplicate/status/resume calls cannot lose valid results, overrun retry budget, create conflicting jobs, or mark partial results complete; reused profiles bypass source preparation.
- QA happy: drive analyze → choose candidate → generate → save voice → reuse through `uv run python -m voice_of_karina` using the documented JSON transport. QA failure: inject stop immediately after a saved output, resume twice, attempt concurrent resume, corrupt one WAV, and make ASR return a clear wrong sentence; only affected output retries and final state remains truthful. Evidence: `evidence/workflow.json` plus pytest output.
- Commit: no unsolicited commit.

### 5. Conversational skill and optional notifications

- Canonical skill is `.agents/skills/voice-of-karina/SKILL.md`; Claude receives the same content through a tested repository-relative symlink or generated mirror with an equality check. Remove menu instructions and old generate/setup skill entry points after the new skill works.
- Conversation order: infer supplied intent; ask mimic versus design only if unclear; for mimic ask for one or more links only when absent; for design ask for a useful voice description only when absent; obtain requested messages; analyze; ask for candidate/timestamp only when target is ambiguous; generate; report files and next available action. Already provided data is never requested twice.
- The skill must expose useful progress, quality warnings, failed stages, saved-voice reuse, and targeted retries in natural language. It must not invent a chosen source speaker or describe a CLI menu to the user.
- Notification application happens only when requested. Validate a finished WAV, back up modified settings, preserve unrelated Claude/Codex configuration, use paths with correct escaping, install idempotently, and verify playback dispatch. Keep application separate from generation success.
- References: `.agents` discovery conventions, current tool config documentation verified by the coordinator, and original install behavior as functional reference only.
- Acceptance: Codex and Claude discovery paths resolve the same skill; no stale Pixi/script invocation remains in active instructions; one-link, many-link, design, reuse, ambiguous-speaker, and failed-generation dialogues each have concrete execution recipes.
- QA happy: execute documented JSON examples from both skill locations and use temporary Claude/Codex homes to install and dispatch a WAV. QA failure: malformed settings and missing WAV leave existing config intact; generation-only requests do not alter notification settings. Evidence: `evidence/skill-notifications.json`.
- Commit: no unsolicited commit.

### 6. Legacy removal, samples, README, and repository identity

- Delete legacy root `src/*.py`, old `hooks/`, old `scripts/`, `.claude/skills/generate-voice`, `.claude/skills/setup-notifications`, `pixi.toml`, `pixi.lock`, `notification_lines.json`, and obsolete output placeholders after the replacement is verified. Remove obsolete dependencies/settings; do not delete `docs/voice-skill-ideation.md` or the Karina image. Replace old sample assets only after fresh usable samples exist.
- Produce fresh Karina sample audio from `https://www.youtube.com/watch?v=r96zEiIHVf4` through the new system. Record source timestamps, exact transcript, selected reference digest, model/revision, request text, generation date, and quality results in a sample provenance file. Never relabel old WAVs as newly generated.
- Create listening media from the newly generated WAVs; if GitHub README needs video attachments, publish fresh waveform/photo MP4s and use their actual attachment URLs. GitHub commonly strips raw `<audio>`/`<video>` tags: verify real rendered behavior. A download link or linked HTML gallery is a useful fallback but is not evidence of in-README playback. Do not call that requirement complete unless playback in the actual README works.
- Rewrite English and Korean READMEs around natural-language requests, installation, supported local hardware, mimic/design/reuse dialogues, reference quality, actual model limitations, and fresh playable samples. Preserve visible `assets/karina.jpg`; keep API comparison optional and explicit.
- Add Buy Me a Coffee to both READMEs using the actual badge/image markup and donation destination from the verified `t1seo/icon-fairy` repository. The skill / integration lane owns this change. Check that both rendered badges display and both links retain the source repository's actual destination; preserve the photo and playable sample placement.
- Rename the remote repository using authenticated owner/admin access, update origin to `https://github.com/t1seo/voice-of-karina.git` (or the same existing SSH transport), and validate the resulting owner/name. Update active links, package branding, skill names, and examples. Keep local workspace path stable for active agents.
- References: existing READMEs, `assets/karina.jpg`, authorized source URL, GitHub rendered README behavior.
- Acceptance: `git ls-files` shows no old execution path; image SHA-256 is unchanged; README sample media are newly generated and play on GitHub; both READMEs show the verified Buy Me a Coffee badge and destination from `t1seo/icon-fairy`; `gh repo view --json nameWithOwner` reports `t1seo/voice-of-karina`; active installation links use the new name. Historical ideation references may remain clearly historical.
- QA happy: follow the README as a first-time agent, then play the new samples in the rendered README and compare the photo hash. QA failure: broken media URL, old sample hash presented as new, or plain download-only link fails sample completion and is repaired before the claim. Evidence: `evidence/release-verification.json` and rendered-page evidence.
- Commit: no unsolicited commit or push; any necessary publication uses the user-authorized rename/sample delivery scope and must be recorded.

## Final verification and completion

- Run focused regression tests plus the full new suite, Ruff, strict type diagnostics, package/install checks, and byte-identical photo verification. Repeat affected checks after fixes, not every expensive model run indiscriminately.
- Conduct real transport-level mimic, design, reuse, interrupted/resumed, ambiguous-input, wrong-text, and failed-install scenarios. Automated fakes cover deterministic failures; real model artifacts prove user-facing generation separately.
- Record `C001`: real clone/design and saved voices; `C002`: workflow/error/resume/cache behavior; `C003`: skill/README/photo/rename/notification integration. Each criterion stays pending until its supporting evidence exists.
- Apply the required post-implementation review skill for independent goal, code-quality, security, hands-on behavior, and context/scope review. Fix substantiated findings before completion. Review must not introduce unrelated external messaging or new approval requirements.
- Final response reports implemented behavior, real artifacts and measured results, verification, and any genuine limitation. Never claim naturalness or celebrity similarity was proven by signal statistics alone. Do not report complete if fresh sample creation or actual README playback remains missing.
