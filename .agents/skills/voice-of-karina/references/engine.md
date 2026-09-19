# Private engine transport

The skill's `scripts/run-engine.sh` resolves symlinks and finds its repository.
Invoke its absolute path from the loaded skill location. `uv run --project ...
--extra local` bootstraps the pinned local dependencies when first used. `uv` and
`ffmpeg` must already be available. The underlying command is
`uv run --project PROJECT_ROOT --extra local python -m voice_of_karina`.

The launcher uses `PROJECT_ROOT/.voice-of-karina` for saved jobs and profiles from
every current directory. A user-specified `VOICE_OF_KARINA_HOME` overrides this
location. Preserve that environment setting when resuming a job.

Write JSON using the agent's structured file tool, then pass `--request /absolute/path/request.json`.
Alternatively send exactly one JSON object on stdin. The response is one JSON
object on stdout; progress and model logs go to stderr. Never build shell source
from the message, URL, or video transcript. Resolve local audio paths to absolute
paths before writing the request.

Message IDs use ASCII letters, digits, `_`, or `-`, beginning with a letter or
digit. Keep them unique within a request. One request accepts up to 8 sources and
32 nonempty messages. Default language is Korean. Use the exact field names below;
unknown fields are rejected rather than silently ignored.

For packaged presets, exact reference registration, paired previews, and saving a
reviewed voice recipe, read [curation.md](curation.md). Those actions use this same
transport; users do not need to operate a separate CLI.

## Request recipes

### Analyze one or more references

```json
{"action":"analyze","sources":["https://www.youtube.com/watch?v=r96zEiIHVf4"],"language":"Korean"}
```

An explicit interval can be passed as `"source_time":{"source_index":0,
"start_seconds":12.5,"end_seconds":22.0}`. The zero-based index selects one of
the supplied sources. The interval still undergoes speech-quality checks.
Use real returned candidate IDs; the examples below are placeholders.

Reliable multi-segment transcripts may produce shorter candidates bounded by
measured pauses inside the original interval. These native-rate crops are
transcribed independently and prioritized only when the wording matches.
`pause_bounded: true` records this verification, not speaker identity or absence
of noise. `utterance_count` describes the analysis segmentation. The displayed
list is capped at four candidates and retains at least one original fallback.
Failed refinement keeps the original candidates.
The extra verification batch is limited to four clips, and speaker-selection
requirements are preserved.

Previously saved voices are unchanged. To use an improved reference, run a new
analysis and save the chosen candidate under a new profile. Resuming an existing
job preserves its reference and attempt budget.

### Generate from a selected candidate

This is the quick one-candidate path. For a reusable new voice with multiple
suitable references, prefer the bounded comparison in [curation.md](curation.md).

```json
{"action":"generate","mode":"mimic","analysis_job_id":"RETURNED_ANALYSIS_JOB_ID","candidate_id":"RETURNED_CANDIDATE_ID","messages":[{"id":"done","text":"작업이 끝났어요. 확인해 주세요."}],"profile_name":"카리나 참고 목소리","language":"Korean","max_attempts":2,"settings":{}}
```

For a user-selected interval, a mimic generation can instead include `sources`
and `source_time`. Never provide both a timestamp and a candidate ID.

### Design an original voice

```json
{"action":"generate","mode":"design","voice_description":"따뜻하고 차분한 한국어 여성 목소리. 또렷하고 편안한 말투.","messages":[{"id":"rest","text":"잠시 쉬어 가셔도 괜찮아요."}],"profile_name":"차분한 안내 목소리","language":"Korean","max_attempts":2,"settings":{}}
```

For first-time design or direct mimic generation, `settings: {}` records the
current defaults as a reusable recipe. It is not a user-facing tuning step.
Do not replace a resumed request with a newly configured one.

### Reuse or list saved voices

```json
{"action":"voices"}
```

```json
{"action":"generate","mode":"reuse","voice_id":"RETURNED_VOICE_ID","messages":[{"id":"next","text":"다음 작업도 준비됐어요."}],"language":"Korean"}
```

Omit `settings` to restore the saved voice's effective sampling settings. Use its
recipe language; incompatible model revisions or languages are refused rather
than silently changed. Explicit `settings` are supported for a requested
controlled change, not a routine user parameter menu. Old profiles without a
recipe remain compatible but have no recorded comparison selection. Generated
audio includes synthesis evidence tying the applied settings and requested text
to the WAV digest and runtime. Retries use deterministic, distinct seeds without
resetting the original attempt budget.

### Check or resume an interrupted job

```json
{"action":"status","job_id":"RETURNED_JOB_ID"}
```

```json
{"action":"resume","job_id":"RETURNED_JOB_ID"}
```

When a saved generation is waiting for a candidate choice, include the returned
`candidate_id` in `resume`. A changed message or voice is a new request. Resume
preserves the original message list and total attempt budget.
The same actions accept an `audition-...` job ID; its finite trial matrix and
selection state are described in [curation.md](curation.md). Do not add a
`candidate_id` when resuming an audition.

### Reject a result after quality review

An automatic pass does not override listening feedback. Read `status` and use
the exact reviewed artifact's SHA256 (normally `accepted_sha256`):

```json
{"action":"reject","job_id":"RETURNED_JOB_ID","message_id":"attention","sha256":"EXACT_64_CHARACTER_LOWERCASE_SHA256","reason":"The reviewed output has an unwanted sound at the beginning."}
```

This records the rejection and its original audio/automatic checks; it does not
generate audio. Call `resume` on the same job to use only the original remaining
attempts. The text, reference, and successful siblings are retained. Replaying the
same rejection is harmless even after a later output succeeds. A different stale
digest is refused. Rejected bytes cannot pass recovery or notification installation,
even when they are found under another filename. When no attempts remain, report
the unresolved quality issue. Do not create a new job just to reset the budget.

Each synthesis batch uses a fresh numbered directory, preserving every reviewed
artifact even when messages have different attempt counts. Installation holds the
same job lock as review and generation through the settings transaction; concurrent
mutations return `job_busy` and can be retried after the active operation finishes.

### Apply a completion sound, only when requested

```json
{"action":"install","job_id":"COMPLETED_JOB_ID","message_id":"done","target":"both"}
```

`target` is `claude`, `codex`, or `both`. Installation accepts an accepted artifact
from a regular generation job; audition trial files and arbitrary filenames are
not installation inputs. Generate a regular verified message from the selected
profile before installing a completion sound. It copies a
stdlib-only player and WAV into the selected tool home, preserves existing
settings, chains an unrelated existing Codex notifier, and returns backup paths.
Only this project's known legacy player command is retired during explicit
installation, so the old and new sounds do not play together.

## Quality and failure handling

For ordinary jobs, read `status`, current step, input request, message results, attempts,
events, and errors. `complete` requires every requested message to be accepted;
`partial` is not full success. Link existing successful WAVs even if another line
failed, and explain which line still needs attention.

Default attempts: **2 total generations per message**, maximum **3**. Resume
does not grant more attempts. Preserve accepted siblings while retrying a failed
message. Reaching the model's generation token limit produces
`incomplete_generation`; a partial WAV is not published as a successful result.

The current quality policy is `speech-completeness-v2`, recorded in
`quality.policy_version`. It checks decodability, audible level, duration, and
clipping, then combines two forms of ending evidence:

- For a requested Korean ending without a final consonant, 10 ms energy frames
  expose abrupt decay or an ending that is still active at the file boundary.
  A measured drop of at least 18 dB with decay shorter than 40 ms is a retry
  signal. This is a limited cut-risk heuristic, not phoneme recognition; a natural
  brief ending can also trigger it. The abrupt-decay measurement examines the
  speech endpoint even when silence follows it, using thresholds relative to
  the signal level so quieter audio does not bypass it. An unmeasurable vowel
  ending remains unverified. The engine regenerates the utterance rather than
  adding padding or a fade.
- ASR must match the last four normalized letters or digits of the requested
  text, or the entire normalized text if shorter. Normalization ignores spacing
  and punctuation. The whole-text character error rate must also satisfy
  `quality.max_text_error_rate`, which defaults to `0.15`.

These checks do not prove complete pronunciation, naturalness, or speaker
similarity. Never relabel missing ASR or uncertain wording as a verified match.

Saved audio with a missing or older policy version is unverified under the current
policy. `status` requests `quality_revalidation`; call `resume` to check the saved
audio again without spending a generation attempt. If it fails, regeneration uses
only the remaining original budget. Notification installation refuses stale
verification until the artifact passes the current policy. Do not edit the saved
policy version or reset attempts to bypass these checks.

For unsupported hardware, missing tools, inaccessible sources, a model download
failure, or exhausted attempts, report the specific actionable error. Do not
switch to an external API without a separately implemented and requested route.
